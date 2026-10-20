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
       reply.error_code == "forbidden" || reply.error_code == "session_expired" ||
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
                    ? std::vector<std::string>{"View personal leader", "Log out"}
                    : std::vector<std::string>{"Create player account", "Log in"});
            menu.set_allow_plugin_skip(false);
            menu.set_selected_index(0);
            menu.set_ok_label("Open");
            menu.set_cancel_label("Back to game");
            if(!menu.show()) return;
            const int action = menu.selected_index();
            if(logged_in) {
                if(action == 0) refresh(profile);
                else if(action == 1) logout(profile);
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
