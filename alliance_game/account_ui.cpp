// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/account_ui.hpp"
#include "alliance_game/battle_launch.hpp"
#include "alliance_native/account_client.hpp"
#include "alliance_native/account_protocol.hpp"

#include "desktop/clipboard.hpp"
#include "font/constants.hpp"
#include "gui/dialogs/message.hpp"
#include "gui/dialogs/modal_dialog.hpp"
#include "gui/dialogs/network_transmission.hpp"
#include "gui/dialogs/simple_item_selector.hpp"
#include "gui/widgets/button.hpp"
#include "gui/widgets/grid.hpp"
#include "gui/widgets/label.hpp"
#include "gui/widgets/text_box.hpp"
#include "gui/widgets/toggle_button.hpp"
#include "log.hpp"
#include "serialization/unicode.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <cstdlib>
#include <functional>
#include <memory>
#include <stdexcept>
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

// Keep the submitted value and the displayed character count in one editor.
// The engine password_box can retain undisplayed input past its render limit
// and its inherited composition handler edits the mask instead of the secret.
// This scoped widget leaves the engine and its other password dialogs intact.
class CredentialPasswordBox final : public gui2::text_box {
public:
    explicit CredentialPasswordBox(const gui2::implementation::builder_styled_widget& builder)
        : text_box(builder)
    {
        set_max_input_length(limit);
        set_use_markup(false);
    }
    ~CredentialPasswordBox() override
    {
        clear_secret(secret_);
        // The dialog clears its displayed value before closing. Do not render
        // from a widget destructor while its containing grid is being torn down.
    }

    const std::string& get_secret() const { return secret_; }

    void set_value(const std::string& text) override
    {
        std::string bounded;
        if(!bounded_prefix(text, limit, bounded)) return;
        clear_secret(secret_);
        secret_.swap(bounded);
        render();
        set_cursor(utf8::size(secret_), false);
    }

    void set_password_visible(bool visible)
    {
        if(visible_ == visible) return;
        const auto start = get_selection_start();
        const int length = static_cast<int>(get_selection_length());
        visible_ = visible;
        render();
        set_selection(start, length);
    }

protected:
    void insert_char(const std::string& text) override
    {
        if(!is_editable() || text.empty()) return;
        const auto selection = selected_range();
        const auto available = limit - (utf8::size(secret_) - selection.second);
        std::string bounded;
        if(!bounded_prefix(text, available, bounded) || bounded.empty()) return;
        const auto added = utf8::size(bounded);
        utf8::erase(secret_, selection.first, selection.second);
        utf8::insert(secret_, selection.first, bounded);
        clear_secret(bounded);
        render();
        set_cursor(selection.first + added, false);
    }

    void delete_selection() override
    {
        if(!is_editable()) return;
        const auto selection = selected_range();
        if(selection.second == 0) return;
        utf8::erase(secret_, selection.first, selection.second);
        render();
        set_cursor(selection.first, false);
    }

    void copy_selection() override {}

    void paste_selection() override
    {
        if(!is_editable()) return;
        std::string text = desktop::clipboard::copy_from_clipboard();
        insert_char(text);
        clear_secret(text);
        fire(gui2::event::NOTIFY_MODIFIED, *this, nullptr);
    }

    void handle_commit(bool& handled, const std::string& text) override
    {
        handled = true;
        if(text.empty()) return;
        insert_char(text);
        fire(gui2::event::NOTIFY_MODIFIED, *this, nullptr);
    }

    void handle_editing(bool& handled, const std::string&, int32_t, int32_t) override
    {
        // Consume preedit before the base implementation can change the mask,
        // reveal uncommitted text, or replace any canonical input. Only committed
        // text edits the password; no IME text is retained in this widget.
        handled = true;
    }

private:
    static constexpr std::size_t limit = 128;

    static bool bounded_prefix(const std::string& text, std::size_t characters,
                               std::string& bounded)
    {
        // At most 128 Unicode scalars need at most 512 UTF-8 bytes. Validate
        // the retained prefix before it can reach the renderer in visible mode.
        bounded.assign(text, 0, std::min(text.size(), limit * 4));
        try { utf8::truncate(bounded, characters); }
        catch(...) { clear_secret(bounded); return false; }
        if(!bounded.empty() && !account::password_valid(bounded, false)) {
            clear_secret(bounded);
            return false;
        }
        return true;
    }

    std::pair<std::size_t, std::size_t> selected_range() const
    {
        const auto size = utf8::size(secret_);
        const auto anchor = std::min(get_selection_start(), size);
        const int length = static_cast<int>(get_selection_length());
        if(length < 0) {
            const auto count = std::min(anchor, static_cast<std::size_t>(-static_cast<long long>(length)));
            return {anchor - count, count};
        }
        return {anchor, std::min(size - anchor, static_cast<std::size_t>(length))};
    }

    void render()
    {
        if(visible_) {
            gui2::text_box::set_value(secret_);
        } else {
            std::string mask;
            for(std::size_t i = 0; i < utf8::size(secret_); ++i) mask += font::unicode_bullet;
            gui2::text_box::set_value(mask);
        }
    }

    std::string secret_;
    bool visible_ = false;
};

// Pinned text layout/drawing debug messages can include the displayed text.
// Keep both domains silent through the entire modal window lifetime, including
// later redraws and teardown, then clear the shared canvas renderer before
// restoring the caller's exact logging levels. This must be the first base.
class CredentialRenderPrivacy {
public:
    CredentialRenderPrivacy()
    {
        try {
            for(std::size_t i = 0; i < domains_.size(); ++i) {
                if(!lg::get_log_domain_severity(domains_[i], previous_[i]) ||
                   !lg::set_log_domain_severity(domains_[i], lg::severity::LG_NONE)) {
                    throw std::runtime_error("The credential dialog could not protect its display.");
                }
                ++suppressed_;
            }
        } catch(...) {
            restore();
            throw;
        }
    }
    ~CredentialRenderPrivacy() noexcept
    {
        try { font::get_text_renderer().set_text("", false); }
        catch(...) {
            // Fail closed if the renderer could still contain visible text.
            return;
        }
        restore();
    }
    CredentialRenderPrivacy(const CredentialRenderPrivacy&) = delete;
    CredentialRenderPrivacy& operator=(const CredentialRenderPrivacy&) = delete;

private:
    void restore() noexcept
    {
        while(suppressed_ != 0) {
            const auto i = --suppressed_;
            try { lg::set_log_domain_severity(domains_[i], previous_[i]); }
            catch(...) {} // A failed restoration leaves that domain suppressed.
        }
    }
    static constexpr std::array<const char*, 2> domains_{{"gui/layout", "gui/draw"}};
    std::array<lg::severity, 2> previous_{};
    std::size_t suppressed_ = 0;
};

// The existing mp_login layout supplies the credential fields. We deliberately
// do not use mp_login itself: that dialog reads and writes guest-login prefs.
// No fields are registered, so modal_dialog cannot save them to text history.
class CredentialsDialog final : private CredentialRenderPrivacy,
                                public gui2::dialogs::modal_dialog {
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
        config password_config;
        password_config["id"] = "password";
        password_config["definition"] = "default";
        // The window's outer grid contains a scrollbar panel. swap_child only
        // recurses into grids, so replace the field in its actual owning grid.
        auto& original_password = find_widget<gui2::text_box>("password");
        auto* password_parent = dynamic_cast<gui2::grid*>(original_password.parent());
        if(password_parent == nullptr) {
            throw std::runtime_error("The credential field has no owning grid.");
        }
        auto replacement = std::make_unique<CredentialPasswordBox>(
            gui2::implementation::builder_text_box(password_config));
        auto* replacement_pointer = replacement.get();
        auto previous = password_parent->swap_child("password", std::move(replacement), false);
        if(previous.get() != &original_password ||
           find_widget<CredentialPasswordBox>("password", false, false) != replacement_pointer) {
            throw std::runtime_error("The credential field could not be installed.");
        }
        auto& password = *replacement_pointer;
        username.set_max_input_length(24);
        username.set_value(username_);
        username.set_active(!confirm_only_);
        password.set_value("");
        auto& label = find_widget<gui2::label>("login_label");
        label.set_use_markup(false);
        label.set_label(description_);
        auto& show_password = find_widget<gui2::toggle_button>("remember_password");
        show_password.set_label("Show password");
        show_password.set_value(false);
        show_password.set_visible(gui2::widget::visibility::visible);
        gui2::event::connect_signal_notify_modified(show_password, [this](auto&&...) {
            auto& password = find_widget<CredentialPasswordBox>("password");
            password.set_password_visible(
                find_widget<gui2::toggle_button>("remember_password").get_value_bool());
            keyboard_capture(&password);
        });
        find_widget<gui2::button>("ok").set_label(action_);
        add_to_tab_order(&username);
        add_to_tab_order(&password);
        keyboard_capture(confirm_only_ ? static_cast<gui2::widget*>(&password)
                                       : static_cast<gui2::widget*>(&username));
    }
    void post_show() override
    {
        auto& password = find_widget<CredentialPasswordBox>("password");
        clear_secret(password_);
        if(get_retval() == gui2::retval::OK) {
            username_ = find_widget<gui2::text_box>("user_name").get_value();
            password_ = password.get_secret();
        }
        password.set_value("");
        password.set_password_visible(false);
        find_widget<gui2::toggle_button>("remember_password").set_value(false);
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
        "Saved battle plans can be practised against the computer.\n"
        "Campaign rewards and shared team battles will be added in a later update.";
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
            "Open Battle plans to practise a saved lineup against the computer.\n"
            "Shared team battles will be added in a later update.";
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
              "Other players can apply to join your team. Shared team battles will follow later.", {"Create this team"}) != 0) return;
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

// Selection is local until the final proposal confirmation. The order of
// selection fixes the map's side assignments; removing a player closes that
// slot and makes the updated assignments visible before continuing.
bool choose_battle_lineup(const account::Team& team, const std::vector<int>& sides,
                          std::vector<std::string>& selected)
{
    selected.clear();
    if(team.members.size() < sides.size()) {
        message(team.name + " does not have enough members for this map.");
        return false;
    }
    for(;;) {
        std::vector<std::string> rows;
        for(const auto& member : team.members) {
            std::string prefix;
            for(std::size_t i = 0; i < selected.size(); ++i) {
                if(selected[i] == member.id) prefix = "[Side " + std::to_string(sides[i]) + "] ";
            }
            rows.push_back(prefix + member.username + " — " + member.unit_type +
                ", leader level " + std::to_string(member.level));
        }
        const int use = selected.size() == sides.size() ? static_cast<int>(rows.size()) : -1;
        if(use >= 0) rows.push_back("Use this lineup");
        const int choice = select_change("Choose lineup — " + team.name,
            "Selected " + std::to_string(selected.size()) + "/" + std::to_string(sides.size()) +
            ". Open a player to select or remove them.\n"
            "Selection order assigns the map sides shown beside each selected player.\n"
            "The captain may select any current members, including themselves.", rows);
        if(choice < 0) return false;
        if(choice == use) return true;
        if(static_cast<std::size_t>(choice) >= team.members.size()) continue;
        const auto& id = team.members[choice].id;
        bool removed = false;
        for(auto it = selected.begin(); it != selected.end(); ++it) {
            if(*it == id) { selected.erase(it); removed = true; break; }
        }
        if(removed) continue;
        if(selected.size() >= sides.size()) {
            message("This lineup is full. Remove a selected player before choosing another.");
            continue;
        }
        selected.push_back(id);
    }
}

std::string battle_validity_text(const std::string& value)
{
    if(value == "valid") return "The saved lineups match the current teams and leaders.";
    if(value == "roster_changed") return "Team membership changed after this plan was saved.";
    if(value == "area_changed") return "A team's area changed after this plan was saved.";
    if(value == "map_unavailable") return "This map is no longer enabled in the area.";
    if(value == "leader_changed") return "A selected personal leader changed after this plan was saved.";
    if(value == "player_unavailable") return "A selected player is unavailable.";
    if(value == "team_unavailable") return "A participating team is unavailable.";
    if(value == "busy") return "A participating team or player is currently busy.";
    return "This plan requires a different version of the battle rules.";
}

std::string battle_text(const account::Battle& battle)
{
    const bool historical = battle.state == "cancelled" || battle.state == "completed";
    const bool invalid_draft = battle.state == "draft" && battle.validity != "valid";
    std::string text = "Battle plan ID: " + battle.id + "\nState: " + battle.state +
        "\nCreated (UTC): " + battle.created_at + "\nMap: " + battle.map.name +
        " (" + std::to_string(battle.map.size) + "v" + std::to_string(battle.map.size) + ")\n" +
        battle_validity_text(battle.validity);
    if(battle.state == "cancelled")
        text += "\nThis Alliance battle plan is cancelled and inactive. It cannot start a shared battle. "
            "Readiness below is saved history; captain approval is inactive.";
    else if(battle.state == "completed")
        text += "\nThis Alliance battle is complete and inactive. It cannot start again. "
            "Readiness below is saved history; captain approval is inactive.";
    else if(invalid_draft)
        text += "\nThis draft cannot start a shared battle while these changes remain. "
            "Captain approval and saved readiness below are inactive.";
    for(const auto& team : battle.teams) {
        text += "\n\nTeam: " + team.name + "\nTeam ID: " + team.id;
        text += historical || invalid_draft ? "\nCaptain approval (inactive): " : "\nCaptain approval: ";
        text += team.approved ? "Approved" : "Waiting";
        for(const auto& player : team.players) {
            text += "\nSide " + std::to_string(player.side) + ": " + player.username + " — " +
                player.unit_type + ", level " + std::to_string(player.level);
            text += historical ? " — Saved readiness (history): " :
                invalid_draft ? " — Saved readiness (inactive): " : " — ";
            text += player.ready ? "Ready" : "Not ready";
        }
    }
    text += "\n\nPlanning does not start a shared team battle.";
    if(battle.state == "draft" || battle.state == "cancelled")
        text += " A selected player can practise this saved lineup locally against the computer. "
            "Practice grants no account experience, team experience or rating.";
    return text;
}

bool can_practice_plan(const account::Battle& battle, const account::Profile& player)
{
    if(player.account_id.empty() || (battle.state != "draft" && battle.state != "cancelled")) return false;
    int selections = 0;
    for(const auto& team : battle.teams)
        for(const auto& member : team.players)
            if(member.id == player.account_id) ++selections;
    return selections == 1;
}

void practice_plan(const alliance::TrustedProfile& profile, const account::Battle& battle)
{
    const std::string actor = current_session().profile.account_id;
    if(current_session().token.empty() || !can_practice_plan(battle, current_session().profile)) return;
    account::Request input;
    input.operation = "me";
    account::Reply output;
    if(!team_request(profile, input, "Checking your player before practice…", output)) return;
    if(current_session().token.empty() || current_session().profile.account_id != actor ||
       output.profile.account_id != actor) {
        message("Your player changed while opening practice. Open Alliance again before continuing.");
        return;
    }
    current_session().profile = output.profile;
    int human_side = 0;
    for(const auto& team : battle.teams)
        for(const auto& member : team.players)
            if(member.id == actor) human_side = member.side;
    const std::string explanation = "Start a local practice game from this saved plan?\n\n"
        "You control side " + std::to_string(human_side) + ". The computer controls every other side, "
        "including your teammates. All sides use the saved leaders and alliances shown above.\n\n"
        "This uses the saved snapshot even if membership, leaders or approvals have changed, or the plan was cancelled. "
        "It does not start or change the shared battle plan. No players or teams are reserved.\n\n"
        "Practice grants no account experience, team experience or rating. Other players do not join this game.";
    if(select_change("Confirm local team practice", battle_text(battle) + "\n\n" + explanation,
                     {"Start local practice"}) != 0) return;
    try {
        // Queue data only. The title-screen caller launches after every modal
        // account window has unwound; no combat runs inside this dialog.
        queue_practice(battle, output.profile);
    } catch(...) {
        message("This saved plan could not prepare a local practice game. No battle plan or account data was changed.");
    }
}

bool battle_mutation(const alliance::TrustedProfile& profile, account::Request input,
                     const std::string& action, account::Battle& result)
{
    const std::string actor = current_session().profile.account_id;
    const std::string operation = input.operation;
    const bool ready = input.ready;
    input.token = current_session().token;
    account::Reply output;
    const std::string recovery = "The change may have reached the server. Open My battle plans and its detail "
        "before trying another change. No battle request was automatically retried.";
    if(!request(profile, input, action, output)) { message(recovery); return false; }
    if(!output.ok) {
        show_failure(output);
        if(output.error_code == "timeout" || output.error_code == "cancelled" || output.error_code == "unavailable" ||
           output.error_code == "protocol" || output.error_code == "team_failed" || output.error_code == "request_failed")
            message(recovery);
        return false;
    }
    bool bound = output.battle.has_value();
    if(bound && operation == "propose_battle")
        bound = output.battle->teams[0].captain_id == actor;
    if(bound && (operation == "approve_battle" || operation == "battle_ready")) {
        bound = false;
        for(const auto& team : output.battle->teams) {
            if(operation == "approve_battle" && team.captain_id == actor && team.approved) bound = true;
            if(operation == "battle_ready") for(const auto& player : team.players)
                if(player.id == actor && player.ready == ready) bound = true;
        }
    }
    if(!bound) { message("The response did not confirm your player's change. " + recovery); return false; }
    result = std::move(*output.battle);
    return true;
}

void battle_detail(const alliance::TrustedProfile& profile, const std::string& battle_id,
                   std::string confirmed_change = {})
{
    while(!current_session().token.empty()) {
        account::Request input;
        input.operation = "battle_detail";
        input.battle_id = battle_id;
        account::Reply output;
        if(!team_request(profile, input, "Loading the saved battle plan…", output) || !output.battle) {
            if(!confirmed_change.empty()) message(confirmed_change + "\nThe change was confirmed, but the latest plan could not be loaded. "
                "Open My battle plans to refresh it; do not repeat the change to refresh the view.");
            return;
        }
        confirmed_change.clear();
        const auto& battle = *output.battle;
        const auto actor = current_session().profile.account_id;
        bool captain = false, approved = false, selected = false, ready = false, both_approved = true;
        for(const auto& team : battle.teams) {
            both_approved = both_approved && team.approved;
            if(team.captain_id == actor) { captain = true; approved = team.approved; }
            for(const auto& player : team.players) if(player.id == actor) { selected = true; ready = player.ready; }
        }
        std::vector<std::string> actions{"View fixed lineups", "Refresh plan"};
        const int practice = can_practice_plan(battle, current_session().profile)
            ? static_cast<int>(actions.size()) : -1;
        if(practice >= 0) actions.push_back("Practice this plan");
        const bool draft = battle.state == "draft";
        const int approve = draft && captain && !approved && battle.validity == "valid"
            ? static_cast<int>(actions.size()) : -1;
        if(approve >= 0) actions.push_back("Approve this plan");
        const int readiness = draft && selected && (ready || (both_approved && battle.validity == "valid"))
            ? static_cast<int>(actions.size()) : -1;
        if(readiness >= 0) actions.push_back(ready ? "Set myself not ready" : "Set myself ready");
        const int cancel = draft && captain ? static_cast<int>(actions.size()) : -1;
        if(cancel >= 0) actions.push_back("Cancel this battle plan");
        const std::string consent = battle.state == "cancelled"
            ? "This plan is cancelled and inactive. Readiness is saved history; captain approval is inactive."
            : battle.state == "completed"
            ? "This battle is complete and inactive. Readiness is saved history; captain approval is inactive."
            : draft && battle.validity != "valid"
            ? "This draft is not eligible for a shared battle. Captain approval and saved readiness are inactive."
            : "Both captains must approve the saved lineups before selected players can become ready.";
        const int choice = select_change("Battle plan", battle.teams[0].name + " vs " + battle.teams[1].name +
            "\nBattle plan ID: " + battle.id +
            "\n" + battle.map.name + " — " + battle.state + "\n" + battle_validity_text(battle.validity) +
            "\n" + consent + (practice >= 0
                ? "\nPractice this plan opens a separate local game against the computer with no account rewards." : ""), actions);
        if(choice < 0) return;
        if(choice == 0) {
            // Another player may have changed membership, consent or state
            // while the action selector was open. Never display its old copy
            // as the newly opened detail, even if this read fails.
            account::Reply latest;
            if(!team_request(profile, input, "Refreshing the fixed lineups…", latest)) return;
            if(!latest.battle) {
                message("The latest fixed lineups could not be loaded. Open My battle plans to try viewing them again.");
                return;
            }
            message(battle_text(*latest.battle));
            continue;
        }
        if(choice == 1) continue;
        if(choice == practice) { practice_plan(profile, battle); if(practice_pending()) return; continue; }
        account::Request mutation;
        mutation.battle_id = battle.id;
        std::string title, explanation, affirmative, success;
        if(choice == approve) {
            mutation.operation = "approve_battle";
            title = "Confirm battle approval";
            explanation = "Approve this exact saved map and both fixed lineups as your team's current captain?";
            affirmative = "Approve saved map and lineups";
            success = "Your captain approval was saved.";
        } else if(choice == readiness) {
            mutation.operation = "battle_ready";
            mutation.ready = !ready;
            title = ready ? "Confirm not ready" : "Confirm ready";
            explanation = ready ? "Withdraw only your readiness for this plan?" : "Mark only yourself ready for this saved plan?";
            affirmative = ready ? "Set myself not ready" : "Set myself ready";
            success = ready ? "You are now not ready." : "You are now ready. This does not start a battle.";
        } else if(choice == cancel) {
            mutation.operation = "cancel_battle";
            title = "Confirm battle cancellation";
            explanation = "Cancel this draft plan for both teams? The saved lineups remain in history. "
                "Team membership, leaders, experience and rating are unchanged.";
            affirmative = "Cancel this draft plan";
            success = "The battle plan was cancelled. No battle was started.";
        } else continue;
        if(select_change(title, battle_text(battle) + "\n\n" + explanation, {affirmative}) != 0) return;
        account::Battle confirmed;
        if(!battle_mutation(profile, mutation, "Saving the battle plan change…", confirmed)) return;
        // The acknowledgement contains the confirmed snapshot. Show it before
        // another read so a later read failure cannot erase the saved result.
        message(success + "\n\n" + battle_text(confirmed));
        confirmed_change = success;
    }
}

void my_battles(const alliance::TrustedProfile& profile)
{
    std::vector<std::string> cursors{std::string{}};
    while(!current_session().token.empty()) {
        account::Request input;
        input.operation = "my_battles";
        input.after = cursors.back();
        account::Reply output;
        if(!team_request(profile, input, "Loading your battle plans…", output)) return;
        std::vector<std::string> rows;
        for(const auto& battle : output.battles) rows.push_back(battle.teams[0].name + " vs " +
            battle.teams[1].name + " — " + battle.state + " — " + battle.created_at + " — " + battle.id);
        const int previous = cursors.size() > 1 ? static_cast<int>(rows.size()) : -1;
        if(previous >= 0) rows.push_back("Previous page");
        const int more = output.next.empty() ? -1 : static_cast<int>(rows.size());
        if(more >= 0) rows.push_back("More battle plans");
        if(rows.empty()) { message("There are no battle plans available to your player yet."); return; }
        const int choice = select("My battle plans", "Open a plan to inspect its saved map, fixed lineups and readiness.\n"
            "Plans are listed by their ID, with up to 20 on each page.", rows);
        if(choice < 0) return;
        if(choice == previous) { cursors.pop_back(); continue; }
        if(choice == more) { cursors.push_back(output.next); continue; }
        if(static_cast<std::size_t>(choice) < output.battles.size()) battle_detail(profile, output.battles[choice].id);
        if(practice_pending()) return;
    }
}

bool choose_battle_opponent(const alliance::TrustedProfile& profile, const account::Team& own,
                            const account::BattleMap& map, account::Team& opponent)
{
    std::vector<std::string> cursors{std::string{}};
    while(!current_session().token.empty()) {
        account::Request input;
        input.operation = "teams";
        input.area_id = own.area_id;
        input.after = cursors.back();
        account::Reply output;
        if(!team_request(profile, input, "Loading teams in your area…", output)) return false;
        std::vector<account::TeamSummary> entries;
        std::vector<std::string> rows;
        for(const auto& team : output.teams) {
            if(team.id == own.id || team.member_count < map.size) continue;
            entries.push_back(team);
            rows.push_back(team.name + " — " + std::to_string(team.member_count) + "/8 members");
        }
        const int previous = cursors.size() > 1 ? static_cast<int>(rows.size()) : -1;
        if(previous >= 0) rows.push_back("Previous page");
        const int more = output.next.empty() ? -1 : static_cast<int>(rows.size());
        if(more >= 0) rows.push_back("More teams");
        if(rows.empty()) { message("There is no other team with enough members for this map in " + own.area_name + "."); return false; }
        const int choice = select_change("Choose opponent", "Area: " + own.area_name + "\nMap: " + map.name +
            "\nOnly other teams with at least " + std::to_string(map.size) + " members are shown.", rows);
        if(choice < 0) return false;
        if(choice == previous) { cursors.pop_back(); continue; }
        if(choice == more) { cursors.push_back(output.next); continue; }
        if(static_cast<std::size_t>(choice) >= entries.size()) continue;
        account::Request detail;
        detail.operation = "team_detail";
        detail.team_id = entries[choice].id;
        account::Reply loaded;
        if(!team_request(profile, detail, "Loading the opponent's current roster…", loaded) || !loaded.team) return false;
        if(loaded.team->area_id != own.area_id || loaded.team->id == own.id ||
           loaded.team->members.size() < static_cast<std::size_t>(map.size)) {
            message("That team's area or roster changed. Open Propose battle again to load current teams.");
            return false;
        }
        opponent = std::move(*loaded.team);
        return true;
    }
    return false;
}

void propose_battle(const alliance::TrustedProfile& profile)
{
    account::Team own;
    if(!load_management_team(profile, own)) return;
    if(own.captain_id != current_session().profile.account_id) {
        message("Only your current team captain can propose a battle plan.");
        return;
    }
    account::Request maps;
    maps.operation = "battle_maps";
    maps.area_id = own.area_id;
    account::Reply available;
    if(!team_request(profile, maps, "Loading the area's approved battle maps…", available)) return;
    std::vector<std::string> rows;
    for(const auto& map : available.maps) rows.push_back(map.name + " — " + std::to_string(map.size) + "v" + std::to_string(map.size));
    if(rows.empty()) { message("No battle maps are enabled in this team's area."); return; }
    const int choice = select_change("Choose battle map", "Area: " + own.area_name +
        "\nChoose a fixed lineup of 2, 3 or 4 players from each team. Saving a plan does not start a game.", rows);
    if(choice < 0 || static_cast<std::size_t>(choice) >= available.maps.size()) return;
    const auto& map = available.maps[choice];
    if(own.members.size() < static_cast<std::size_t>(map.size)) {
        message("Your team needs at least " + std::to_string(map.size) + " members for this map.");
        return;
    }
    account::Team opponent;
    if(!choose_battle_opponent(profile, own, map, opponent)) return;
    account::Request proposal;
    proposal.operation = "propose_battle";
    proposal.team_id = own.id;
    proposal.opponent_team_id = opponent.id;
    proposal.map_id = map.id;
    proposal.lineups = {{own.id, {}}, {opponent.id, {}}};
    if(!choose_battle_lineup(own, map.sides[0], proposal.lineups[0].account_ids) ||
       !choose_battle_lineup(opponent, map.sides[1], proposal.lineups[1].account_ids)) return;
    std::string preview = "Map: " + map.name + " (" + std::to_string(map.size) + "v" + std::to_string(map.size) + ")";
    const std::vector<const account::Team*> teams{&own, &opponent};
    for(std::size_t i = 0; i < teams.size(); ++i) {
        preview += "\n\nTeam: " + teams[i]->name + "\nTeam ID: " + teams[i]->id;
        for(std::size_t j = 0; j < proposal.lineups[i].account_ids.size(); ++j)
            for(const auto& member : teams[i]->members) if(member.id == proposal.lineups[i].account_ids[j])
                preview += "\nSide " + std::to_string(map.sides[i][j]) + ": " + member.username + " — " +
                    member.unit_type + ", level " + std::to_string(member.level);
    }
    preview += "\n\nSave this fixed battle plan? Both captains must then review and explicitly approve the saved map and lineups. "
        "This does not start combat or award experience or rating.";
    if(select_change("Confirm battle proposal", preview, {"Send battle proposal"}) != 0) return;
    account::Battle confirmed;
    if(!battle_mutation(profile, proposal, "Saving your battle proposal…", confirmed)) return;
    if(confirmed.area_id != own.area_id) {
        message("The saved proposal did not match the selected area. Open My battle plans to check it before making another change.");
        return;
    }
    message("Battle plan saved. Both captains must review and explicitly approve its saved map and fixed lineups.\n\n" + battle_text(confirmed));
    battle_detail(profile, confirmed.id, "Battle plan saved.");
}

void battle_plans(const alliance::TrustedProfile& profile)
{
    while(!current_session().token.empty()) {
        const int choice = select("Battle plans", "Prepare maps and fixed lineups with another team in your area.\n"
            "Both captains approve, then selected players set their readiness.\n"
            "Open a saved plan to practise your side locally with computer teammates and opponents. "
            "Practice does not change the plan or award account experience or rating.", {"My battle plans", "Propose battle"});
        if(choice < 0) return;
        if(choice == 0) my_battles(profile);
        if(choice == 1) propose_battle(profile);
        if(practice_pending()) return;
    }
}

void teams(const alliance::TrustedProfile& profile)
{
    while(!current_session().token.empty()) {
        const int action = select("Alliance teams",
            "Build your team in an area. Apply to join a team, or review applications as captain.",
            {"Browse areas and teams", "Create team", "My team", "My applications", "Review applications", "Application decisions",
             "Transfer captain", "Leave team", "Area rankings", "Battle plans"});
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
        else if(action == 9) battle_plans(profile);
        if(practice_pending()) return;
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
    if(active || practice_pending()) return;
    struct ActiveFlow {
        bool& active;
        explicit ActiveFlow(bool& value) : active(value) { active = true; }
        ~ActiveFlow() { active = false; }
    } flow(active);
    enum class OpenStage { profile, menu, account_screen };
    auto stage = OpenStage::profile;
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
            stage = OpenStage::menu;
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
            stage = OpenStage::account_screen;
            if(logged_in) {
                if(action == 0) refresh(profile);
                else if(action == 1) teams(profile);
                else if(action == 2) logout(profile);
            } else {
                if(action == 0) register_player(profile);
                else if(action == 1) login(profile);
            }
            if(practice_pending()) return;
        }
    } catch(const account::ProfileReadError& error) {
        // This type accepts only an enum and returns fixed public strings.
        // Arbitrary exceptions below never expose their what() text.
        message(error.what());
    } catch(...) {
        // Never surface arbitrary exception strings containing request material.
        if(stage == OpenStage::profile) {
            message("Alliance could not validate the saved server profile "
                    "[profile-unexpected]. Please report this message.");
        } else if(stage == OpenStage::menu) {
            message("Alliance could not open the account menu. Please report this message.");
        } else {
            message("Alliance could not open the selected account screen. Please report "
                    "which screen you were opening.");
        }
    }
}
} // namespace alliance_game
