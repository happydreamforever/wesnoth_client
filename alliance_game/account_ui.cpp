// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/account_ui.hpp"
#include "alliance_native/account_client.hpp"

#include "gui/dialogs/message.hpp"
#include "gui/dialogs/modal_dialog.hpp"
#include "gui/dialogs/network_transmission.hpp"
#include "gui/dialogs/simple_item_selector.hpp"
#include "gui/widgets/button.hpp"
#include "gui/widgets/grid.hpp"
#include "gui/widgets/label.hpp"
#include "gui/widgets/password_box.hpp"
#include "gui/widgets/text_box.hpp"
#include "gui/widgets/toggle_button.hpp"
#include "serialization/unicode.hpp"

#include <atomic>
#include <cstdlib>
#include <functional>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace alliance_game {
namespace {
namespace account = alliance::account;

void clear_secret(std::string& value) noexcept
{
    // Clear the live string before releasing it. No passwords or session tokens
    // are written to game preferences, history, saved games, files or logs.
    volatile char* bytes = value.empty() ? nullptr : &value[0];
    for(std::size_t i = 0; i < value.size(); ++i) {
        bytes[i] = 0;
    }
    value.clear();
}

struct Session {
    std::string token;
    account::Profile profile;
    std::string identity;

    void clear() noexcept
    {
        clear_secret(token);
        profile = {};
        identity.clear();
    }
    ~Session() { clear_secret(token); }
};

Session& current_session()
{
    static Session session;
    return session;
}

void message(const std::string& text)
{
    gui2::show_message("Alliance", text, gui2::dialogs::message::ok_button,
                      false, false);
}

bool valid_username(const std::string& name)
{
    if(name.size() < 3 || name.size() > 24) return false;
    for(const unsigned char value : name) {
        if(!((value >= 'a' && value <= 'z') || (value >= 'A' && value <= 'Z') ||
             (value >= '0' && value <= '9') || value == '_')) return false;
    }
    return true;
}

// The existing mp_login layout supplies a masked password_box. We deliberately
// do not use mp_login itself: that dialog reads and writes guest-login prefs.
// No fields are registered, so modal_dialog cannot save them to text history.
class CredentialsDialog final : public gui2::dialogs::modal_dialog {
public:
    CredentialsDialog(std::string& username, std::string& password,
                      const std::string& description, const std::string& action,
                      bool confirm_only = false)
        : modal_dialog("mp_login"), username_(username), password_(password),
          description_(description), action_(action), confirm_only_(confirm_only)
    {
        set_allow_plugin_skip(false);
    }

private:
    const std::string& window_id() const override
    {
        static const std::string id("mp_login");
        return id;
    }
    void pre_show() override
    {
        // The verified mp_login layout's first content cell is its anonymous
        // title label. Rename only that label; leave custom-theme fallbacks
        // intact if they use a different arrangement.
        auto* content = find_widget<gui2::grid>("_window_content_grid", false, false);
        if(content == nullptr) content = &get_grid();
        if(content->get_rows() != 0 && content->get_cols() != 0) {
            if(auto* title = dynamic_cast<gui2::label*>(content->get_widget(0, 0))) {
                title->set_use_markup(false);
                title->set_label(action_);
            }
        }
        auto& username = find_widget<gui2::text_box>("user_name");
        auto& password = find_widget<gui2::password_box>("password");
        username.set_max_input_length(24);
        username.set_value(username_);
        username.set_active(!confirm_only_);
        password.set_max_input_length(128);
        password.set_value("");
        auto& label = find_widget<gui2::label>("login_label");
        label.set_use_markup(false);
        label.set_label(description_);
        find_widget<gui2::toggle_button>("remember_password")
            .set_visible(gui2::widget::visibility::invisible);
        find_widget<gui2::button>("ok").set_label(action_);
        add_to_tab_order(&username);
        add_to_tab_order(&password);
        keyboard_capture(confirm_only_ ? static_cast<gui2::widget*>(&password)
                                       : static_cast<gui2::widget*>(&username));
    }
    void post_show() override
    {
        auto& password = find_widget<gui2::password_box>("password");
        if(get_retval() == gui2::retval::OK) {
            username_ = find_widget<gui2::text_box>("user_name").get_value();
            password_ = password.get_real_value();
        }
        password.set_value("");
        find_widget<gui2::text_box>("user_name").set_value("");
    }
    std::string& username_;
    std::string& password_;
    std::string description_;
    std::string action_;
    bool confirm_only_;
};

// network_transmission pumps the regular game event loop and forwards Cancel.
// This adapter's poll never performs network work. Results are read only after
// joining the worker; finished uses release/acquire for the event-loop monitor.
class AccountJob final : public gui2::dialogs::network_transmission::connection_data {
public:
    AccountJob(const alliance::TrustedProfile& profile, account::Request& request)
        : thread_([this, &profile, &request]() {
            try {
                result_ = account::perform_request(profile, request, cancellation_);
            } catch(...) {
                result_.ok = false;
                result_.error_code = "request_failed";
                result_.error_message = "The account request could not be completed.";
            }
            finished_.store(true, std::memory_order_release);
        })
    {
    }
    ~AccountJob() override
    {
        cancellation_.cancel();
        if(thread_.joinable()) thread_.join();
        clear_secret(result_.token);
    }
    bool finished() override { return finished_.load(std::memory_order_acquire); }
    void cancel() override { cancellation_.cancel(); }
    void poll() override {}
    account::Reply take_result()
    {
        if(thread_.joinable()) thread_.join();
        return std::move(result_);
    }

private:
    alliance::CancellationToken cancellation_;
    std::atomic<bool> finished_{false};
    account::Reply result_;
    // Last member: all state must exist before its worker is started.
    std::thread thread_;
};

bool request(const alliance::TrustedProfile& profile, account::Request& input,
             const std::string& action, account::Reply& output)
{
    AccountJob job(profile, input);
    gui2::dialogs::network_transmission progress(
        job, "Alliance", action + "\nYou can cancel this request.");
    progress.set_allow_plugin_skip(false);
    bool accepted = false;
    try {
        accepted = progress.show();
    } catch(...) {
        job.cancel();
        // AccountJob's destructor joins before the request can be destroyed.
        throw;
    }
    if(!accepted) job.cancel();
    output = job.take_result();
    clear_secret(input.password);
    clear_secret(input.token);
    if(!accepted) {
        clear_secret(output.token);
        return false;
    }
    return true;
}

void show_failure(const account::Reply& reply)
{
    if(reply.error_code == "invalid_session" || reply.error_code == "unauthorized" ||
       reply.error_code == "session_expired" ||
       reply.error_code == "account_unavailable") {
        current_session().clear();
    }
    message(reply.error_message.empty()
        ? "The account request failed. Check that the Windows Alliance service is running."
        : reply.error_message);
}

std::string profile_text(const account::Profile& profile)
{
    return "Player: " + profile.username + "\n"
        "Player ID: " + profile.account_id + "\n\n"
        "Personal leader: " + profile.unit_type + "\n"
        "Level: " + std::to_string(profile.level) + "\n"
        "Experience: " + std::to_string(profile.experience) + "\n"
        "Total experience: " + std::to_string(profile.total_experience) + "\n\n"
        "Your leader is saved on the Windows Alliance server.\n"
        "Campaign rewards and team battles will be added in a later update.";
}

void register_player(const alliance::TrustedProfile& profile)
{
    account::Request input;
    input.operation = "register";
    const std::vector<std::string> ids{
        "spearman", "elvish-fighter", "orcish-grunt",
        "dwarvish-fighter", "drake-fighter", "skeleton"};
    gui2::dialogs::simple_item_selector starter(
        "Create an Alliance player", "Choose your personal starting leader.",
        {"Spearman — Loyalists", "Elvish Fighter — Rebels",
         "Orcish Grunt — Northerners", "Dwarvish Fighter — Knalgan Alliance",
         "Drake Fighter — Drakes", "Skeleton — Undead"});
    starter.set_allow_plugin_skip(false);
    starter.set_selected_index(0);
    starter.set_ok_label("Choose leader");
    if(!starter.show()) return;
    const int selected = starter.selected_index();
    if(selected < 0 || static_cast<std::size_t>(selected) >= ids.size()) return;
    input.starter_id = ids[selected];

    CredentialsDialog credentials(input.username, input.password,
        "Create a player account.\nUsername: 3–24 letters, digits or underscores.\n"
        "Password: 12–128 characters. Your administrator account is separate.",
        "Create account");
    if(!credentials.show()) {
        clear_secret(input.password);
        return;
    }
    if(!valid_username(input.username) || utf8::size(input.password) < 12 ||
       utf8::size(input.password) > 128) {
        clear_secret(input.password);
        message("Use a username of 3–24 ASCII letters, digits or underscores "
                "and a password of 12–128 characters.");
        return;
    }
    std::string repeated;
    CredentialsDialog confirmation(input.username, repeated,
        "Create player account: repeat the same password.", "Confirm password", true);
    const bool confirmed = confirmation.show() && repeated == input.password;
    clear_secret(repeated);
    if(!confirmed) {
        clear_secret(input.password);
        message("Account creation cancelled or the passwords did not match.");
        return;
    }
    account::Reply output;
    if(!request(profile, input, "Creating your player account…", output)) {
        message("Request cancelled. Account creation may have reached the server. "
                "Try logging in with the same username before creating another account.");
        return;
    }
    if(!output.ok) {
        show_failure(output);
        if(output.error_code == "timeout" || output.error_code == "unavailable" ||
           output.error_code == "service_unavailable" || output.error_code == "protocol") {
            message("Account creation may have reached the server. "
                    "Try logging in with the same username before registering again.");
        }
        return;
    }
    message("Your player account was created.\n\n" + profile_text(output.profile) +
            "\n\nChoose Log in to continue.");
}

void login(const alliance::TrustedProfile& profile)
{
    account::Request input;
    input.operation = "login";
    CredentialsDialog credentials(input.username, input.password,
        "Log in to your Alliance player account.\n"
        "The Windows administrator account is separate.", "Log in");
    if(!credentials.show()) {
        clear_secret(input.password);
        return;
    }
    if(!valid_username(input.username) || input.password.empty() ||
       utf8::size(input.password) > 128) {
        clear_secret(input.password);
        message("Enter your player username and password.");
        return;
    }
    account::Reply output;
    if(!request(profile, input, "Logging in…", output)) return;
    if(!output.ok) {
        show_failure(output);
        return;
    }
    auto& session = current_session();
    session.clear();
    session.token = std::move(output.token);
    session.profile = std::move(output.profile);
    session.identity = profile.address + ":" + std::to_string(profile.port) + ":" +
                       profile.certificate_sha256;
    message("Logged in.\n\n" + profile_text(session.profile));
}

void refresh(const alliance::TrustedProfile& profile)
{
    account::Request input;
    input.operation = "me";
    input.token = current_session().token;
    account::Reply output;
    if(!request(profile, input, "Loading your personal leader…", output)) return;
    if(!output.ok) {
        show_failure(output);
        return;
    }
    current_session().profile = std::move(output.profile);
    message(profile_text(current_session().profile));
}

// Reuse the existing edit_text layout without registering fields or history.
class TeamNameDialog final : public gui2::dialogs::modal_dialog {
public:
    explicit TeamNameDialog(std::string& name) : modal_dialog("edit_text"), name_(name)
    {
        set_allow_plugin_skip(false);
    }
private:
    const std::string& window_id() const override
    {
        static const std::string id("edit_text");
        return id;
    }
    void pre_show() override
    {
        auto& title = find_widget<gui2::label>("title");
        title.set_use_markup(false);
        title.set_label("Create a team");
        auto& label = find_widget<gui2::label>("label");
        label.set_use_markup(false);
        label.set_label("Name (3–40 characters):\nLetters, digits, spaces, _ or -");
        auto& input = find_widget<gui2::text_box>("text");
        input.set_max_input_length(40);
        input.set_value(name_);
        keyboard_capture(&input);
        add_to_tab_order(&input);
        find_widget<gui2::button>("ok").set_label("Continue");
    }
    void post_show() override
    {
        auto& input = find_widget<gui2::text_box>("text");
        if(get_retval() == gui2::retval::OK) name_ = input.get_value();
        input.set_value("");
    }
    std::string& name_;
};

bool team_request(const alliance::TrustedProfile& profile, account::Request input,
                  const std::string& action, account::Reply& output)
{
    input.token = current_session().token;
    if(!request(profile, input, action, output)) return false;
    if(!output.ok) {
        show_failure(output);
        return false;
    }
    return true;
}

int select(const std::string& title, const std::string& description,
           const std::vector<std::string>& items)
{
    gui2::dialogs::simple_item_selector menu(title, description, items, false, false);
    menu.set_allow_plugin_skip(false);
    menu.set_selected_index(0);
    menu.set_ok_label("Open");
    menu.set_cancel_label("Back");
    return menu.show() ? menu.selected_index() : -1;
}

// Each page is fetched afresh. Only cursors are kept for Previous page, and all
// server-provided display strings have already passed the strict text schema.
bool choose_area(const alliance::TrustedProfile& profile, account::Area& selected)
{
    std::vector<std::string> cursors{std::string{}};
    while(!current_session().token.empty()) {
        account::Request input;
        input.operation = "areas";
        input.after = cursors.back();
        account::Reply output;
        if(!team_request(profile, input, "Loading areas…", output)) return false;
        std::vector<std::string> rows;
        for(const auto& area : output.areas) rows.push_back(area.name);
        const int previous = cursors.size() > 1 ? static_cast<int>(rows.size()) : -1;
        if(previous >= 0) rows.push_back("Previous page");
        const int more = output.next.empty() ? -1 : static_cast<int>(rows.size());
        if(more >= 0) rows.push_back("More areas");
        if(rows.empty()) {
            message("No enabled areas are available. Ask the Windows administrator to create an area.");
            return false;
        }
        const int choice = select("Choose an area", "Teams are grouped by area.", rows);
        if(choice < 0) return false;
        if(choice == previous) { cursors.pop_back(); continue; }
        if(choice == more) { cursors.push_back(output.next); continue; }
        if(static_cast<std::size_t>(choice) < output.areas.size()) {
            selected = output.areas[choice];
            return true;
        }
    }
    return false;
}

std::string team_text(const account::Team& team)
{
    std::string text = "Team: " + team.name + "\nTeam ID: " + team.id +
        "\nArea: " + team.area_name + "\nArea ID: " + team.area_id +
        "\n\nTeam level: " + std::to_string(team.level) +
        "\nTeam experience: " + std::to_string(team.experience) +
        "\nRating: " + std::to_string(team.rating) +
        "\nMembers: " + std::to_string(team.members.size()) + "/" + std::to_string(team.capacity) +
        "\n\nRoster:";
    for(const auto& member : team.members) {
        text += "\n" + member.username;
        if(member.id == team.captain_id) text += " (captain)";
        text += " — " + member.unit_type + ", leader level " + std::to_string(member.level);
    }
    text += "\n\nTeam experience is separate from personal leader experience.\n"
            "Joining teams and team battles will be added in a later update.";
    return text;
}

void my_team(const alliance::TrustedProfile& profile)
{
    account::Request input;
    input.operation = "my_team";
    account::Reply output;
    if(!team_request(profile, input, "Loading your team…", output)) return;
    message(output.team ? team_text(*output.team) :
        "You do not belong to a team yet. Choose Create team to become its captain.\n"
        "Joining another team will be added in a later update.");
}

void browse_teams(const alliance::TrustedProfile& profile, const account::Area& area)
{
    std::vector<std::string> cursors{std::string{}};
    while(!current_session().token.empty()) {
        account::Request input;
        input.operation = "teams";
        input.area_id = area.id;
        input.after = cursors.back();
        account::Reply output;
        if(!team_request(profile, input, "Loading teams…", output)) return;
        std::vector<std::string> rows;
        for(const auto& team : output.teams) {
            rows.push_back(team.name + " — team level " + std::to_string(team.level) +
                           ", " + std::to_string(team.member_count) + "/8 members");
        }
        const int previous = cursors.size() > 1 ? static_cast<int>(rows.size()) : -1;
        if(previous >= 0) rows.push_back("Previous page");
        const int more = output.next.empty() ? -1 : static_cast<int>(rows.size());
        if(more >= 0) rows.push_back("More teams");
        if(rows.empty()) {
            message("There are no active teams in " + area.name + " yet.");
            return;
        }
        const int choice = select("Teams in " + area.name,
            "Open a team to view its captain and leaders.", rows);
        if(choice < 0) return;
        if(choice == previous) { cursors.pop_back(); continue; }
        if(choice == more) { cursors.push_back(output.next); continue; }
        if(static_cast<std::size_t>(choice) >= output.teams.size()) continue;
        account::Request detail;
        detail.operation = "team_detail";
        detail.team_id = output.teams[choice].id;
        account::Reply loaded;
        if(team_request(profile, detail, "Loading the team roster…", loaded) && loaded.team)
            message(team_text(*loaded.team));
    }
}

void browse_areas(const alliance::TrustedProfile& profile)
{
    while(!current_session().token.empty()) {
        account::Area area;
        if(!choose_area(profile, area)) return;
        browse_teams(profile, area);
    }
}

void create_team(const alliance::TrustedProfile& profile)
{
    account::Area area;
    if(!choose_area(profile, area)) return;
    account::Request input;
    input.operation = "create_team";
    input.area_id = area.id;
    TeamNameDialog dialog(input.name);
    if(!dialog.show()) return;
    const auto start = input.name.find_first_not_of(" ");
    if(start == std::string::npos) input.name.clear();
    else input.name = input.name.substr(start, input.name.find_last_not_of(" ") - start + 1);
    if(!account::team_name_valid(input.name)) {
        message("Use 3–40 ASCII letters, digits, spaces, underscores or hyphens. "
                "Start the team name with a letter or digit.");
        return;
    }
    if(select("Confirm new team", "Team: " + input.name + "\nArea: " + area.name +
              "\n\nYou will become captain. A team can have up to 8 members.\n"
              "Joining teams and team battles are not available yet.", {"Create this team"}) != 0) return;
    input.token = current_session().token;
    account::Reply output;
    if(!request(profile, input, "Creating your team…", output)) {
        message("Team creation may have reached the server. Open My team to check before trying to create a team again.");
        return;
    }
    if(!output.ok) {
        show_failure(output);
        if(output.error_code == "timeout" || output.error_code == "cancelled" ||
           output.error_code == "unavailable" || output.error_code == "service_unavailable" ||
           output.error_code == "protocol" || output.error_code == "team_failed" ||
           output.error_code == "request_failed") {
            message("Team creation may have reached the server. Open My team to check before trying to create a team again.");
        }
        return;
    }
    if(output.team) message("Your team was created. You are its captain.\n\n" + team_text(*output.team));
}

void teams(const alliance::TrustedProfile& profile)
{
    while(!current_session().token.empty()) {
        const int action = select("Alliance teams",
            "Build your team in an area. Team battles and joining will follow in a later update.",
            {"Browse areas and teams", "Create team", "My team"});
        if(action < 0) return;
        if(action == 0) browse_areas(profile);
        else if(action == 1) create_team(profile);
        else if(action == 2) my_team(profile);
    }
}

void logout(const alliance::TrustedProfile& profile)
{
    account::Request input;
    input.operation = "logout";
    input.token = current_session().token;
    account::Reply output;
    const bool completed = request(profile, input, "Logging out…", output);
    current_session().clear();
    if(completed && output.ok) {
        message("Logged out. The server has revoked this session.");
    } else {
        message("You are signed out on this device. The server did not confirm "
                "session revocation; the previous session will expire on the server.");
    }
}
} // namespace

void show_account_dialog()
{
    // A title-screen hotkey must not open another account flow while a modal
    // request is pumping events. This flag is used only by the game UI thread.
    static bool active = false;
    if(active) return;
    struct ActiveFlow {
        bool& active;
        explicit ActiveFlow(bool& value) : active(value) { active = true; }
        ~ActiveFlow() { active = false; }
    } flow(active);
    try {
        const char* path = std::getenv("WESNOTH_ALLIANCE_PROFILE");
        if(path == nullptr || *path == '\0') {
            message("Import the Windows Alliance public connection profile "
                    "before opening your player account.");
            return;
        }
        const alliance::TrustedProfile profile = account::read_public_profile(path);
        auto& session = current_session();
        const std::string identity = profile.address + ":" + std::to_string(profile.port)
                                   + ":" + profile.certificate_sha256;
        if(!session.token.empty() && session.identity != identity) session.clear();
        for(;;) {
            const bool logged_in = !session.token.empty();
            gui2::dialogs::simple_item_selector menu(
                "Alliance",
                logged_in ? "Logged in as " + session.profile.username
                          : "Grow your personal leader.\nCreate a player account or log in.",
                logged_in
                    ? std::vector<std::string>{"View personal leader", "Teams", "Log out"}
                    : std::vector<std::string>{"Create player account", "Log in"});
            menu.set_allow_plugin_skip(false);
            menu.set_selected_index(0);
            menu.set_ok_label("Open");
            menu.set_cancel_label("Back to game");
            if(!menu.show()) return;
            const int action = menu.selected_index();
            if(logged_in) {
                if(action == 0) refresh(profile);
                else if(action == 1) teams(profile);
                else if(action == 2) logout(profile);
            } else {
                if(action == 0) register_player(profile);
                else if(action == 1) login(profile);
            }
        }
    } catch(...) {
        // Never surface arbitrary exception strings containing request material.
        message("Alliance could not open. Check that the trusted Windows public "
                "connection profile has been imported and try again.");
    }
}
} // namespace alliance_game
