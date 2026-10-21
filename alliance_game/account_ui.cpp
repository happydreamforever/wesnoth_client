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

void application_decisions(const alliance::TrustedProfile& profile, bool unread_only);

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
    application_decisions(profile, true);
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

// Mutation menus deliberately open on a no-action row. Enter/Open alone
// cannot approve, reject, transfer captain or leave a team.
int select_change(const std::string& title, const std::string& description,
                  const std::vector<std::string>& actions)
{
    std::vector<std::string> rows{"Back without changes"};
    rows.insert(rows.end(), actions.begin(), actions.end());
    const int selected = select(title, description, rows);
    return selected > 0 ? selected - 1 : -1;
}

void application_decisions(const alliance::TrustedProfile& profile, bool unread_only)
{
    while(!current_session().token.empty()) {
        account::Request input;
        input.operation = "application_decisions";
        account::Reply output;
        if(!team_request(profile, input, "Checking your application decisions…", output)) return;
        std::vector<account::Decision> entries;
        std::vector<std::string> rows;
        for(const auto& decision : output.decisions) {
            if(unread_only && !decision.unread) continue;
            entries.push_back(decision);
            rows.push_back(std::string(decision.unread ? "Unread — " : "Read — ") +
                (decision.status == "approved" ? "Approved — " : "Rejected — ") +
                decision.team_name + " — " + decision.created_at);
        }
        if(rows.empty()) {
            if(!unread_only) message("You have no saved application decisions yet.\n"
                "This history records new captain approvals and rejections from this update onward.\n"
                "Open My team to check your current membership.");
            return;
        }
        const int choice = select(unread_only ? "Unread application decisions" : "Application decisions",
            "Your latest 50 captain decisions are retained, newest first.\n"
            "Open one to read its result. Back leaves unopened decisions unread.\n"
            "History does not replace My team for your current membership.", rows);
        if(choice < 0) return;
        if(static_cast<std::size_t>(choice) >= entries.size()) continue;
        const auto& decision = entries[choice];
        const bool approved = decision.status == "approved";
        const std::string detail = std::string(approved ? "Application approved.\n" : "Application rejected.\n") +
            (approved ? "The captain approved your application to join " : "The captain rejected your application to join ") +
            decision.team_name + ".\n\nTeam ID: " + decision.team_id +
            "\nDecision time (UTC): " + decision.created_at +
            "\n\nThis is a saved decision, not a live membership check. Open My team for your current team." +
            (decision.unread ? "\nChoose OK to mark only this decision as read." : "\nThis decision has already been read.");
        // Opening a list is read-only. Only an explicitly opened detail that
        // closes with OK can acknowledge that one notice; plugins cannot skip it.
        gui2::dialogs::message dialog("Alliance application decision", detail, false, false, false);
        dialog.set_allow_plugin_skip(false);
        dialog.set_button_visible(gui2::dialogs::message::ok, gui2::widget::visibility::visible);
        dialog.set_button_caption(gui2::dialogs::message::ok, "OK");
        dialog.show();
        if(dialog.get_retval() != gui2::retval::OK || !decision.unread) continue;
        account::Request acknowledge;
        acknowledge.operation = "ack_decision";
        acknowledge.decision_id = decision.id;
        acknowledge.token = current_session().token;
        account::Reply saved;
        const bool completed = request(profile, acknowledge, "Marking this decision as read…", saved);
        if(!completed || !saved.ok) {
            if(completed) show_failure(saved);
            if(!current_session().token.empty()) message(
                "The read status was not confirmed. Open Application decisions again to check it.\n"
                "Your saved approval or rejection is unchanged. No acknowledgement was automatically retried.");
            return;
        }
        // Refresh after this acknowledgement. Other entries retain their own
        // unread flags; there is no bulk acknowledgement or mutation retry.
    }
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
            "Team battles will be added in a later update.";
    return text;
}

bool my_team(const alliance::TrustedProfile& profile)
{
    account::Request input;
    input.operation = "my_team";
    account::Reply output;
    if(!team_request(profile, input, "Loading your team…", output)) return false;
    message(output.team ? team_text(*output.team) :
        "You do not belong to a team yet. Choose Create team to become its captain.\n"
        "Or browse areas and teams to apply to join an existing team.");
    return true;
}

void my_applications(const alliance::TrustedProfile& profile);

std::string membership_recovery(const std::string& operation)
{
    if(operation == "leave_team" || operation == "transfer_captain") {
        return "The action may have reached the server. Open My team to check your membership and captain "
               "before trying another change. No team change was automatically retried.";
    }
    if(operation == "apply_team" || operation == "withdraw_application") {
        return "The action may have reached the server. Check My applications and My team "
               "before trying it again. An absent application may have been approved, rejected or withdrawn.";
    }
    return "The action may have reached the server. Check My team and Review applications "
           "before trying it again. An absent application alone does not prove approval.";
}

bool membership_request(const alliance::TrustedProfile& profile, account::Request input,
                        const std::string& action, account::Reply* confirmed = nullptr)
{
    const std::string operation = input.operation;
    const std::string actor = current_session().profile.account_id;
    input.token = current_session().token;
    account::Reply output;
    if(!request(profile, input, action, output)) {
        message(membership_recovery(operation));
        return false;
    }
    if(!output.ok) {
        show_failure(output);
        if(output.error_code == "timeout" || output.error_code == "cancelled" ||
           output.error_code == "unavailable" || output.error_code == "service_unavailable" ||
           output.error_code == "protocol" || output.error_code == "team_failed" ||
           output.error_code == "request_failed") message(membership_recovery(operation));
        return false;
    }
    if((operation == "apply_team" || operation == "withdraw_application" || operation == "leave_team") && output.account_id != actor) {
        message("The response did not identify your player. " + membership_recovery(operation));
        return false;
    }
    if(confirmed != nullptr) *confirmed = std::move(output);
    return true;
}

bool load_management_team(const alliance::TrustedProfile& profile, account::Team& team)
{
    account::Request input;
    input.operation = "my_team";
    account::Reply output;
    if(!team_request(profile, input, "Loading your current team…", output)) return false;
    if(!output.team) {
        message("You do not currently belong to a team. Open My team or browse areas and teams.");
        return false;
    }
    const auto& actor = current_session().profile.account_id;
    bool member_found = false;
    for(const auto& member : output.team->members) if(member.id == actor) member_found = true;
    if(!member_found) {
        message("The roster did not identify your player. Open My team again before making a change.");
        return false;
    }
    team = std::move(*output.team);
    return true;
}

void refresh_confirmed_team(const alliance::TrustedProfile& profile, const std::string& result)
{
    message(result + "\nMy team will now refresh.");
    if(!my_team(profile)) message(result +
        "\nThe change was confirmed, but the new My team view could not be loaded. "
        "Open My team again to check the current roster. Do not repeat the change to refresh it.");
}

void transfer_captain(const alliance::TrustedProfile& profile)
{
    account::Team team;
    if(!load_management_team(profile, team)) return;
    const auto& actor = current_session().profile.account_id;
    if(team.captain_id != actor) {
        message("Only the current team captain can transfer captain to another member.");
        return;
    }
    std::vector<account::Member> members;
    std::vector<std::string> rows;
    for(const auto& member : team.members) {
        if(member.id == actor) continue;
        members.push_back(member);
        rows.push_back(member.username + " — " + member.unit_type + ", leader level " + std::to_string(member.level));
    }
    if(rows.empty()) {
        message("There is no other current member to become captain. Approve another player's application first.");
        return;
    }
    const int selected = select_change("Transfer captain", "Team: " + team.name + "\nTeam ID: " + team.id +
        "\nChoose another current member. You will remain a member after the transfer.", rows);
    if(selected < 0 || static_cast<std::size_t>(selected) >= members.size()) return;
    const auto& target = members[selected];
    if(select_change("Confirm captain transfer", "Team: " + team.name + "\nTeam ID: " + team.id +
        "\n\nNew captain: " + target.username + "\nPlayer ID: " + target.id +
        "\n\nYou will remain a member and lose captain permissions. Personal and team experience are unchanged.",
        {"Transfer captain to " + target.username}) != 0) return;
    account::Request input;
    input.operation = "transfer_captain";
    input.team_id = team.id;
    input.account_id = target.id;
    if(membership_request(profile, input, "Transferring captain…"))
        refresh_confirmed_team(profile, "Captain transferred to " + target.username + ".");
}

void leave_team(const alliance::TrustedProfile& profile)
{
    account::Team team;
    if(!load_management_team(profile, team)) return;
    const bool captain = team.captain_id == current_session().profile.account_id;
    if(captain && team.members.size() > 1) {
        message("You are captain of " + team.name + ". Transfer captain to another current member before leaving.\n"
                "Open Transfer captain, then check My team before choosing Leave team again.");
        return;
    }
    const std::string consequence = captain
        ? "You are the only member and captain. Leaving will archive this team and clear its pending applications. "
          "The team will no longer appear in active team lists or area rankings."
        : "You will leave this team. Its captain and other members will remain.";
    const std::string preview = "Team: " + team.name + "\nTeam ID: " + team.id +
        "\nArea: " + team.area_name + "\nMembers: " + std::to_string(team.members.size()) + "/8";
    if(select_change("Confirm leaving team", preview + "\n\n" + consequence +
        "\nYour personal leader and experience are unchanged.",
        {captain ? "Leave and archive this team" : "Leave this team"}) != 0) return;
    account::Request input;
    input.operation = "leave_team";
    input.team_id = team.id;
    input.allow_archive = captain && team.members.size() == 1;
    account::Reply confirmed;
    if(membership_request(profile, input, "Leaving your team…", &confirmed)) {
        refresh_confirmed_team(profile, confirmed.archived
            ? "You left " + team.name + ". The team was archived and its pending applications were cleared."
            : "You left " + team.name + ". The team remains active.");
    }
}

void team_actions(const alliance::TrustedProfile& profile, const std::string& team_id)
{
    while(!current_session().token.empty()) {
        account::Request input;
        input.operation = "team_detail";
        input.team_id = team_id;
        account::Reply output;
        if(!team_request(profile, input, "Loading the team roster…", output) || !output.team) return;
        const auto& team = *output.team;
        bool is_member = false;
        for(const auto& member : team.members) {
            if(member.id == current_session().profile.account_id) is_member = true;
        }
        std::vector<std::string> actions{"View roster"};
        if(!is_member) actions.push_back("Apply to join");
        const int choice = select(team.name,
            "Area: " + team.area_name + "\nMembers: " + std::to_string(team.members.size()) +
            "/8\nThe captain decides which applications to approve.", actions);
        if(choice < 0) return;
        if(choice == 0) { message(team_text(team)); continue; }
        if(choice != 1 || is_member) continue;
        if(select("Confirm application", "Apply to join " + team.name + " in " + team.area_name +
                  "?\nTeam ID: " + team.id + "\nThis sends an application; it does not immediately join the team.",
                  {"Send application"}) != 0) return;
        account::Request apply;
        apply.operation = "apply_team";
        apply.team_id = team.id;
        if(membership_request(profile, apply, "Sending your application…")) {
            message("Application submitted. Your captain must approve it before you join.\nMy applications will now refresh.");
            my_applications(profile);
        }
        return;
    }
}

void my_applications(const alliance::TrustedProfile& profile)
{
    application_decisions(profile, true);
    std::vector<std::string> cursors{std::string{}};
    while(!current_session().token.empty()) {
        account::Request input;
        input.operation = "my_applications";
        input.after = cursors.back();
        account::Reply output;
        if(!team_request(profile, input, "Loading your applications…", output)) return;
        std::vector<std::string> rows;
        for(const auto& team : output.teams) rows.push_back(team.name + " — " + std::to_string(team.member_count) + "/8 members");
        const int previous = cursors.size() > 1 ? static_cast<int>(rows.size()) : -1;
        if(previous >= 0) rows.push_back("Previous page");
        const int more = output.next.empty() ? -1 : static_cast<int>(rows.size());
        if(more >= 0) rows.push_back("More applications");
        if(rows.empty()) {
            message("You have no pending team applications.\n"
                    "Open Application decisions for saved captain approvals and rejections.\n"
                    "Open My team to check your current membership.");
            return;
        }
        const int choice = select("My applications", "These applications are waiting for a captain's decision.", rows);
        if(choice < 0) return;
        if(choice == previous) { cursors.pop_back(); continue; }
        if(choice == more) { cursors.push_back(output.next); continue; }
        if(static_cast<std::size_t>(choice) >= output.teams.size()) continue;
        const auto& team = output.teams[choice];
        const int action = select(team.name, "Team ID: " + team.id,
                                  {"View roster", "Withdraw application"});
        if(action == 0) {
            account::Request detail;
            detail.operation = "team_detail";
            detail.team_id = team.id;
            account::Reply loaded;
            if(team_request(profile, detail, "Loading the team roster…", loaded) && loaded.team)
                message(team_text(*loaded.team));
        } else if(action == 1 && select("Confirm withdrawal", "Withdraw your application to " + team.name +
                  "?\nTeam ID: " + team.id + "\nThis does not leave a team if you have already been approved.",
                  {"Withdraw this application"}) == 0) {
            account::Request withdraw;
            withdraw.operation = "withdraw_application";
            withdraw.team_id = team.id;
            if(membership_request(profile, withdraw, "Withdrawing your application…"))
                message("Application withdrawn. The list will now refresh.");
        }
        // Every return from a detail/action dialog re-reads the current page.
    }
}

void review_applications(const alliance::TrustedProfile& profile)
{
    account::Request own;
    own.operation = "my_team";
    account::Reply current;
    if(!team_request(profile, own, "Loading your team…", current)) return;
    if(!current.team || current.team->captain_id != current_session().profile.account_id) {
        message("Only the current team captain can review applications.");
        return;
    }
    const std::string team_id = current.team->id;
    const std::string team_name = current.team->name;
    std::vector<std::string> cursors{std::string{}};
    while(!current_session().token.empty()) {
        account::Request input;
        input.operation = "team_applicants";
        input.team_id = team_id;
        input.after = cursors.back();
        account::Reply output;
        if(!team_request(profile, input, "Loading team applications…", output)) return;
        std::vector<std::string> rows;
        for(const auto& member : output.applicants)
            rows.push_back(member.username + " — " + member.unit_type + ", leader level " + std::to_string(member.level));
        const int previous = cursors.size() > 1 ? static_cast<int>(rows.size()) : -1;
        if(previous >= 0) rows.push_back("Previous page");
        const int more = output.next.empty() ? -1 : static_cast<int>(rows.size());
        if(more >= 0) rows.push_back("More applications");
        if(rows.empty()) {
            message("There are no pending applications for " + team_name + ".");
            return;
        }
        const int choice = select("Applications to " + team_name, "Choose a player to inspect and approve or reject.", rows);
        if(choice < 0) return;
        if(choice == previous) { cursors.pop_back(); continue; }
        if(choice == more) { cursors.push_back(output.next); continue; }
        if(static_cast<std::size_t>(choice) >= output.applicants.size()) continue;
        const auto& applicant = output.applicants[choice];
        const std::string preview = "Player: " + applicant.username + "\nPlayer ID: " + applicant.id +
            "\nPersonal leader: " + applicant.unit_type + "\nLeader level: " + std::to_string(applicant.level);
        const int action = select_change("Review application", preview, {"Approve application", "Reject application"});
        if(action < 0 || action > 1) continue;
        const bool approve = action == 0;
        if(select_change(approve ? "Confirm approval" : "Confirm rejection",
                  preview + "\n\nTeam: " + team_name + "\nTeam ID: " + team_id +
                  (approve ? "\nApprove this player as a member?" : "\nReject this pending application?"),
                  {approve ? "Approve this player" : "Reject this application"}) != 0) continue;
        account::Request mutation;
        mutation.operation = approve ? "approve_application" : "reject_application";
        mutation.team_id = team_id;
        mutation.account_id = applicant.id;
        if(membership_request(profile, mutation, approve ? "Approving application…" : "Rejecting application…")) {
            message(approve ? "Application approved. The roster and application list will now refresh."
                            : "Application rejected. The application list will now refresh.");
            if(approve) my_team(profile);
        }
    }
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
        team_actions(profile, output.teams[choice].id);
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

void area_rankings(const alliance::TrustedProfile& profile)
{
    account::Area area;
    if(!choose_area(profile, area)) return;
    while(!current_session().token.empty()) {
        account::Request input;
        input.operation = "standings";
        input.area_id = area.id;
        account::Reply output;
        if(!team_request(profile, input, "Loading area rankings…", output)) return;
        std::vector<std::string> rows;
        for(const auto& team : output.teams) {
            rows.push_back(std::to_string(rows.size() + 1) + ". " + team.name +
                " — rating " + std::to_string(team.rating) + ", team level " +
                std::to_string(team.level) + ", " + std::to_string(team.member_count) + "/8 members");
        }
        if(rows.empty()) {
            message("There are no active teams in " + area.name + " yet.");
            return;
        }
        const int selected = select("Area rankings — " + area.name,
            "Top 20 active teams by rating, highest first. Equal ratings are ordered by team ID.\n"
            "Open a team to view its current roster." + std::string(output.has_more
                ? "\nMore teams exist in this area. Use Browse areas and teams to see the full directory."
                : "\nAll active teams in this area are shown."), rows);
        if(selected < 0) return;
        if(static_cast<std::size_t>(selected) >= output.teams.size()) continue;
        account::Request detail;
        detail.operation = "team_detail";
        detail.team_id = output.teams[selected].id;
        account::Reply loaded;
        if(team_request(profile, detail, "Loading the team roster…", loaded) && loaded.team)
            message(team_text(*loaded.team));
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
              "Other players can apply to join your team. Team battles will follow later.", {"Create this team"}) != 0) return;
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
            "Build your team in an area. Apply to join a team, or review applications as captain.",
            {"Browse areas and teams", "Create team", "My team", "My applications", "Review applications", "Application decisions",
             "Transfer captain", "Leave team", "Area rankings"});
        if(action < 0) return;
        if(action == 0) browse_areas(profile);
        else if(action == 1) create_team(profile);
        else if(action == 2) my_team(profile);
        else if(action == 3) my_applications(profile);
        else if(action == 4) review_applications(profile);
        else if(action == 5) application_decisions(profile, false);
        else if(action == 6) transfer_captain(profile);
        else if(action == 7) leave_team(profile);
        else if(action == 8) area_rankings(profile);
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
