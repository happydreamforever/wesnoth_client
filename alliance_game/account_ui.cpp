// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/account_ui.hpp"
#include "alliance_game/android_lifecycle.hpp"
#include "alliance_game/session_watch.hpp"
#include "alliance_game/hero_catalog.hpp"
#include "alliance_game/story_progression.hpp"
#include "alliance_game/battle_launch.hpp"
#include "alliance_game/practice_plan.hpp"
#include "alliance_game/progression_plan.hpp"
#include "alliance_game/progression_result.hpp"
#include "alliance_game/shared_launch.hpp"
#include "alliance_game/shared_result.hpp"
#include "alliance_game/replay_launch.hpp"
#include "alliance_native/progression_protocol.hpp"
#include "alliance_native/account_client.hpp"
#include "alliance_native/account_protocol.hpp"

#include "desktop/clipboard.hpp"
#include "color.hpp"
#include "draw.hpp"
#include "draw_manager.hpp"
#include "gui/core/top_level_drawable.hpp"
#include "video.hpp"
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
    gui2::show_message("Wesnoth", text, gui2::dialogs::message::ok_button,
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
#ifdef __ANDROID__
        auto* controls_parent = dynamic_cast<gui2::grid*>(show_password.parent());
        if(controls_parent == nullptr) throw std::runtime_error("The credential controls have no owning grid.");
        auto controls = std::make_unique<gui2::grid>(2, 1);
        auto* controls_ptr = controls.get();
        auto original_toggle = controls_parent->swap_child("remember_password", std::move(controls), false);
        if(original_toggle.get() != &show_password) throw std::runtime_error("The credential controls could not be installed.");
        const auto flags = gui2::grid::VERTICAL_ALIGN_CENTER | gui2::grid::HORIZONTAL_ALIGN_LEFT;
        controls_ptr->set_child(std::move(original_toggle), 0, 0, flags, 0);
        config reset_config;
        reset_config["id"] = "reset_keyboard_modifiers";
        reset_config["definition"] = "default";
        reset_config["label"] = "Reset keyboard modifiers";
        auto reset = std::make_unique<gui2::button>(gui2::implementation::builder_button(reset_config));
        auto* reset_ptr = reset.get();
        controls_ptr->set_child(std::move(reset), 1, 0, flags, 0);
        gui2::event::connect_signal_mouse_left_click(*reset_ptr, [this](auto&&...) {
            const bool reset_done = reset_android_keyboard_modifiers();
            // Inline feedback keeps exactly one credential window visible and
            // never reads, transforms or submits the current credential text.
            find_widget<gui2::label>("login_label").set_label(description_ + "\n" +
                (reset_done ? "Keyboard modifiers reset for new typing. Existing text was not changed."
                            : "Keyboard reset is unavailable. Existing text was not changed."));
            keyboard_capture(&find_widget<CredentialPasswordBox>("password"));
            invalidate_layout();
        });
        add_to_tab_order(reset_ptr);
#endif
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

// Startup runs before the title screen exists. Always give hidden/closed
// authentication windows a real background to reveal, rather than retaining
// their last pixels behind the next, smaller dialog.
class AuthenticationBackdrop final : public gui2::top_level_drawable {
public:
    AuthenticationBackdrop() { draw_manager::invalidate_all(); }
    ~AuthenticationBackdrop() override { draw_manager::invalidate_all(); }
    rect screen_location() override { return video::game_canvas(); }
    bool expose(const rect& region) override
    {
        draw::fill(region, color_t{18, 24, 32, 255});
        return true;
    }
};

int welcome_action(bool allow_signup, const std::string& notice)
{
    // This function returns only after the welcome modal has been destroyed.
    // Button return values select an action directly; there is no list or OK.
    constexpr int login_action = 10, signup_action = 11;
    gui2::dialogs::message welcome("Welcome to Wesnoth",
        notice.empty() ? "Log in once to play Campaigns, Multiplayer and Teams." : notice, false, false, false);
    using Dialog = gui2::dialogs::message;
    using Visibility = gui2::widget::visibility;
    welcome.set_allow_plugin_skip(false);
    welcome.set_button_caption(Dialog::left_1, "Log in");
    welcome.set_button_visible(Dialog::left_1, Visibility::visible);
    welcome.set_button_retval(Dialog::left_1, login_action);
    welcome.set_button_caption(Dialog::ok, "Sign up");
    welcome.set_button_visible(Dialog::ok, allow_signup ? Visibility::visible : Visibility::invisible);
    welcome.set_button_retval(Dialog::ok, signup_action);
    welcome.set_button_caption(Dialog::cancel, "Quit");
    welcome.set_button_visible(Dialog::cancel, Visibility::visible);
    welcome.set_button_retval(Dialog::cancel, gui2::retval::CANCEL);
    welcome.set_enter_disabled(true);
    welcome.show();
    const int action = welcome.get_retval();
    return action == login_action ? 0 : allow_signup && action == signup_action ? 1 : -1;
}

bool collect_credentials(std::string& username, std::string& password,
                         const std::string& description, const std::string& action,
                         bool confirm_only = false)
{
    // Destruction releases the modal and clears its private text renderer
    // before confirmation, network progress, feedback or welcome can open.
    CredentialsDialog credentials(username, password, description, action, confirm_only);
    return credentials.show();
}

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
    AccountNetworkActivity foreground;
    AccountJob job(profile, input);
    gui2::dialogs::network_transmission progress(
        job, "Wesnoth", action + "\nYou can cancel this request.");
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
        if(!current_session().token.empty())
            request_session_reauthentication(current_session().profile.account_id, current_session().profile.database_generation);
        current_session().clear();
    }
    message(reply.error_message.empty()
        ? "The account request failed. Check that the Windows Alliance service is running."
        : reply.error_message);
}

std::string profile_text(const account::Profile& profile)
{
    return "Player: " + profile.username + "\nPlayer ID: " + profile.account_id +
        "\nHeroes unlocked: " + std::to_string(profile.heroes.size()) +
        "\n\nPlay a campaign and win a scenario with its named heroes to add them to your collection. "
        "Each hero has independent permanent experience and earned forms. "
        "Campaigns use their original story strength; Multiplayer uses your collected heroes.";
}

bool reload_player(const alliance::TrustedProfile& trusted)
{
    const auto actor=current_session().profile.account_id;
    const auto generation=current_session().profile.database_generation;
    account::Request input; input.operation="me"; input.token=current_session().token;
    account::Reply output;
    if(!request(trusted,input,"Loading your heroes…",output)) return false;
    if(!output.ok) {show_failure(output);return false;}
    if(output.profile.account_id!=actor || output.profile.database_generation!=generation) {
        request_session_reauthentication(actor, generation);
        current_session().clear(); message("The signed-in account or game database changed. Log in again.");return false;
    }
    current_session().profile=std::move(output.profile);
    return true;
}

bool choose_hero(const alliance::TrustedProfile& trusted, std::string& hero_id, std::string& form_id)
{
    if(!reload_player(trusted)) return false;
    const auto actor=current_session().profile.account_id;
    const auto heroes=current_session().profile.heroes;
    if(heroes.empty()) {message("No heroes unlocked yet. Win a story campaign scenario with a named hero before joining a multiplayer side.");return false;}
    std::vector<std::string> rows;
    for(const auto& hero:heroes) rows.push_back(hero.name+" — level "+std::to_string(hero.hero_level)+" — XP "+std::to_string(hero.experience));
    gui2::dialogs::simple_item_selector choice("Choose your hero","Each hero keeps its own experience. Choose an earned form next.",rows);
    choice.set_allow_plugin_skip(false); if(!choice.show()) return false;
    const int index=choice.selected_index();if(index<0||static_cast<std::size_t>(index)>=heroes.size())return false;
    const auto& hero=heroes[index];
    std::vector<std::string> form_labels;
    for(const auto& id:hero.forms){const auto* spec=find_hero_form(hero.hero_id,id);if(!spec)throw std::runtime_error("Unsupported collected form.");form_labels.push_back(spec->name+" — original unit tier "+std::to_string(spec->native_level));}
    gui2::dialogs::simple_item_selector forms("Choose hero form",hero.name+" — level "+std::to_string(hero.hero_level),form_labels);
    forms.set_allow_plugin_skip(false);if(!forms.show())return false;
    const int form=forms.selected_index();if(form<0||static_cast<std::size_t>(form)>=hero.forms.size())return false;
    if(!reload_player(trusted)||current_session().profile.account_id!=actor)return false;
    const auto& latest=current_session().profile.heroes;
    const auto found=std::find_if(latest.begin(),latest.end(),[&](const auto& h){return h.hero_id==hero.hero_id;});
    if(found==latest.end()||found->revision!=hero.revision||std::find(found->forms.begin(),found->forms.end(),hero.forms[form])==found->forms.end()) {
        message("This hero changed while you were choosing. Open the selection again.");return false;
    }
    hero_id=hero.hero_id;form_id=hero.forms[form];return true;
}

void heroes_dialog(const alliance::TrustedProfile& trusted)
{
    while(reload_player(trusted)) {
        const auto heroes=current_session().profile.heroes;
        if(heroes.empty()){message(profile_text(current_session().profile));return;}
        std::vector<std::string> rows;
        for(const auto& h:heroes)rows.push_back(h.name+" — level "+std::to_string(h.hero_level));
        gui2::dialogs::simple_item_selector choice("Heroes","Your campaign heroes and their earned forms.",rows);
        choice.set_allow_plugin_skip(false);if(!choice.show())return;
        const int index=choice.selected_index();if(index<0||static_cast<std::size_t>(index)>=heroes.size())return;
        const auto& h=heroes[index];
        std::string detail=h.name+"\nHero level: "+std::to_string(h.hero_level)+"\nExperience: "+std::to_string(h.experience)+
            "\nTotal experience: "+std::to_string(h.total_experience)+"\n\nEarned forms:";
        for(const auto& form:h.forms){const auto* spec=find_hero_form(h.hero_id,form);if(!spec)throw std::runtime_error("Unsupported collected form.");detail+="\n"+spec->name+" — original unit tier "+std::to_string(spec->native_level); }
        detail+="\n\nEvery level adds 2% of original HP and 1% of original attack damage. Movement, number of strikes, resistances and innate abilities stay unchanged. "
            "Next-level XP is 5 × current level² + 15. Story campaigns start at their original strength.";
        message(detail);
    }
}

void register_player(const alliance::TrustedProfile& profile)
{
    account::Request input;
    input.operation = "register";
    if(!collect_credentials(input.username, input.password,
        "Create a player account.\nUsername: 3–24 letters, digits or underscores.\n"
        "Password: 12–128 characters. Your administrator account is separate.",
        "Create account")) {
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
    const bool confirmed = collect_credentials(input.username, repeated,
        "Create player account: repeat the same password.", "Confirm password", true) && repeated == input.password;
    clear_secret(repeated);
    if(!confirmed) {
        clear_secret(input.password);
        message("Account creation cancelled or the passwords did not match.");
        return;
    }
    std::string signup_password=input.password;
    struct ClearSignup { std::string& value; ~ClearSignup(){clear_secret(value);} } clear_signup{signup_password};
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
    account::Request admission;
    admission.operation="login";admission.username=input.username;admission.password=signup_password;
    account::Reply signed_in;
    if(!request(profile,admission,"Opening your new account…",signed_in)||!signed_in.ok) {
        message("Your account was created. Automatic login was not confirmed. Choose Log in with the same username and password; do not create another account.");return;
    }
    if(signed_in.profile.account_id!=output.profile.account_id||signed_in.profile.database_generation!=output.profile.database_generation)
        throw std::runtime_error("Signup account changed.");
    auto& session=current_session();session.clear();session.token=std::move(signed_in.token);session.profile=std::move(signed_in.profile);
    session.identity=profile.address+":"+std::to_string(profile.port)+":"+profile.certificate_sha256;
    message("Welcome, "+session.profile.username+". Your account is ready.\n\nChoose Campaigns to start a story and unlock your first named hero.");
}

void application_decisions(const alliance::TrustedProfile& profile, bool unread_only);

void login(const alliance::TrustedProfile& profile, const std::string& required_account = {},
           const std::string& required_generation = {})
{
    account::Request input;
    input.operation = "login";
    if(!collect_credentials(input.username, input.password,
        "Log in to your player account.\n"
        "The Windows administrator account is separate.", "Log in")) {
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
    if((!required_account.empty() && output.profile.account_id != required_account) ||
       (!required_generation.empty() && output.profile.database_generation != required_generation)) {
        // A reconnect must never temporarily expose another player's account,
        // decisions or token to the paused game. Revoke only this new session.
        account::Request revoke;
        revoke.operation = "logout";
        revoke.token = std::move(output.token);
        account::Reply ignored;
        try { request(profile, revoke, "Closing the other account session…", ignored); }
        catch(...) {}
        clear_secret(revoke.token);
        current_session().clear();
        message("Sign in as the player who started this game to resume. Quit game to switch accounts.");
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
    if(!request(profile, input, "Loading your heroes…", output)) return;
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
        text += " — " + std::to_string(member.hero_count) + " heroes";
    }
    text += "\n\nTeam experience is separate from each hero's experience.\n"
            "Open Battle plans for local training or a shared team battle.\n"
            "Open Captain orders for your team's saved plan and instructions.";
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
        rows.push_back(member.username + " — " + std::to_string(member.hero_count) + " heroes");
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
        "\nYour heroes and their experience are unchanged.",
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
            rows.push_back(member.username + " — " + std::to_string(member.hero_count) + " heroes");
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
            "\nHeroes unlocked: " + std::to_string(applicant.hero_count);
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
            rows.push_back(prefix + member.username + " — " + std::to_string(member.hero_count) + " heroes");
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
    if(value == "leader_changed") return "A selected hero changed after this plan was saved.";
    if(value == "player_unavailable") return "A selected player is unavailable.";
    if(value == "team_unavailable") return "A participating team is unavailable.";
    if(value == "busy") return "A participating team or player is currently busy.";
    return "This plan requires a different version of the battle rules.";
}

std::string practice_availability_text(const account::Battle& battle, const account::Profile& player)
{
    if(battle.state == "cancelled")
        return "Cancelled plans are history only. Local practice is unavailable.";
    if(battle.state == "completed")
        return "Completed battles cannot start practice. Watch completed battle replay is available when this player's verified recording is saved on this device.";
    if(battle.state == "running")
        return "This Alliance battle is already running. Local practice is unavailable.";
    const auto eligibility = practice_eligibility(battle, player);
    std::string text = eligibility == PracticeEligibility::eligible
        ? "Your personal practice is available. Other players do not need to be Ready."
        : practice_eligibility_reason(eligibility);
    if(eligibility == PracticeEligibility::viewer_readiness_required)
        text += " Choose Set myself ready first, then choose Practice this plan when you want to start.";
    if(battle.state != "draft" || battle.validity != "valid" || eligibility == PracticeEligibility::invalid_plan) return text;
    int approved = 0, ready = 0, players = 0;
    std::string captains_waiting, players_waiting;
    for(const auto& team : battle.teams) {
        if(team.approved) ++approved;
        else captains_waiting += (captains_waiting.empty() ? "" : ", ") + team.name;
        for(const auto& member : team.players) {
            ++players;
            if(member.ready) ++ready;
            else players_waiting += (players_waiting.empty() ? "" : ", ") + member.username;
        }
    }
    text += "\nCaptains approved: " + std::to_string(approved) + "/2\nTeam readiness: " +
        std::to_string(ready) + "/" + std::to_string(players) + " selected players Ready. "
        "Shared battles require everyone Ready.";
    if(!captains_waiting.empty()) text += "\nWaiting for captain approval: " + captains_waiting;
    if(!players_waiting.empty()) text += "\nNot ready for a shared battle: " + players_waiting;
    return text;
}

std::string hero_form_text(const std::string& hero_id,const std::string& form_id,std::int64_t level)
{
    const auto* form=find_hero_form(hero_id,form_id);
    if(!form)throw std::runtime_error("Unsupported hero form.");
    return hero_name(hero_id)+" / "+form->name+", hero level "+std::to_string(level);
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
                (player.selection_present ? hero_form_text(player.hero_id,player.form_id,player.hero_level) : "Choose a hero before Ready");
            text += historical ? " — Saved readiness (history): " :
                invalid_draft ? " — Saved readiness (inactive): " : " — ";
            text += player.ready ? "Ready" : "Not ready";
        }
    }
    text += "\n\nA shared battle starts only after every selected player is Ready, both captains approve and every fixed seat joins.";
    text += "\n" + practice_availability_text(battle, current_session().profile);
    if(battle.state == "draft")
        text += "\nPersonal practice requires a valid plan, both captains' approval and your own Ready. "
            "Practice grants no account experience, team experience or rating.";
    return text;
}

bool can_practice_plan(const account::Battle& battle, const account::Profile& player)
{
    return practice_eligibility(battle, player) == PracticeEligibility::eligible;
}

bool load_practice_plan(const alliance::TrustedProfile& profile, const std::string& battle_id,
                        const std::string& actor, account::Reply& output)
{
    account::Request input;
    input.operation = "battle_detail";
    input.battle_id = battle_id;
    if(!team_request(profile, input, "Checking the latest plan and readiness…", output)) return false;
    if(current_session().token.empty() || current_session().profile.account_id != actor) {
        message("Your player changed while opening practice. Open Alliance again before continuing.");
        return false;
    }
    if(!output.battle || output.battle->id != battle_id) {
        message("The latest battle plan could not be verified. No practice was started. Open My battle plans and try again.");
        return false;
    }
    if(!can_practice_plan(*output.battle, current_session().profile)) {
        message("No practice was started.\n" + practice_availability_text(*output.battle, current_session().profile));
        return false;
    }
    return true;
}

std::string practice_settings_text(const PracticeSettings& settings)
{
    return std::string("Control: ") + practice_control_name(settings.control) +
        "\nStarting gold: " + std::to_string(settings.starting_gold) + " per side" +
        "\nTurn limit: " + (settings.turn_limit == -1 ? std::string("Unlimited") : std::to_string(settings.turn_limit)) +
        "\nFog: " + (settings.fog ? "On" : "Off");
}

bool choose_practice_settings(PracticeSettings& settings)
{
    while(true) {
        const int choice = select_change("Practice setup", practice_settings_text(settings) +
            "\n\nThese settings affect only this local game. All sides receive the same starting gold. "
            "Your saved lineup and alliances stay fixed. No other devices or players connect. "
            "Review and start opens a separate confirmation; choosing settings does not start a game.",
            {"Control mode", "Starting gold", "Turn limit", "Fog", "Reset to defaults", "Review and start"});
        if(choice < 0) return false;
        if(choice == 0) {
            const int mode = select_change("Practice control",
                "My side: control your assigned side; every other side is computer-controlled.\n"
                "Command my team: control every side on your team; the opposing team is computer-controlled.\n"
                "Local hotseat: take turns controlling every side on this device. You can pass the device between local players. "
                "This is not remote multiplayer and does not sign in to other players' accounts.",
                {"My side", "Command my team", "Local hotseat"});
            if(mode >= 0 && mode < 3)
                settings.control = std::array<PracticeControl, 3>{PracticeControl::own_side,
                    PracticeControl::own_team, PracticeControl::hotseat}[mode];
        } else if(choice == 1) {
            const int gold = select_change("Practice starting gold",
                "Every side receives the same starting gold. Village income and recruitment costs use the normal game rules.",
                {"75 gold per side", "100 gold per side", "150 gold per side", "200 gold per side"});
            if(gold >= 0 && gold < 4) settings.starting_gold = std::array<int, 4>{75, 100, 150, 200}[gold];
        } else if(choice == 2) {
            const int turns = select_change("Practice turn limit",
                "A turn includes each side's move. If no team wins before a finite limit, the stock game ends practice "
                "and shows Defeat. This does not record an Alliance loss or change account experience, team experience or rating.",
                {"20 turns", "30 turns", "50 turns", "Unlimited"});
            if(turns >= 0 && turns < 4) settings.turn_limit = std::array<int, 4>{20, 30, 50, -1}[turns];
        } else if(choice == 3) {
            const int fog = select_change("Practice fog",
                "Fog on hides enemies outside your team's current vision. Fog off shows the battlefield. "
                "Local hotseat players share this device; fog is not a privacy boundary between them.",
                {"Fog on", "Fog off"});
            if(fog >= 0 && fog < 2) settings.fog = fog == 0;
        } else if(choice == 4) {
            settings = PracticeSettings{};
        } else if(choice == 5) {
            return valid_practice_settings(settings);
        }
    }
}

std::string practice_lineup_text(const PracticePlan& plan)
{
    std::string text = "Local control of saved lineup copies:";
    for(const auto& seat : plan.seats) {
        text += "\nSide " + std::to_string(seat.side) + ": " + seat.username + " — " +
            hero_form_text(seat.hero_id,seat.form_id,seat.hero_level) + " — " + seat.team_name + " — ";
        text += seat.human ? "Local player on this device" : "Computer";
    }
    return text;
}

void practice_plan(const alliance::TrustedProfile& profile, const account::Battle& battle)
{
    const std::string actor = current_session().profile.account_id;
    if(current_session().token.empty() || !can_practice_plan(battle, current_session().profile)) return;
    const std::string battle_id = battle.id;
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
    account::Reply preview;
    if(!load_practice_plan(profile, battle_id, actor, preview)) return;
    PracticeSettings settings;
    if(!choose_practice_settings(settings)) return;
    PracticePlan local_plan;
    try {
        local_plan = make_practice_plan(*preview.battle, output.profile, settings);
    } catch(...) {
        message("Local practice could not be prepared. No practice was started. Refresh the plan to check its current state.");
        return;
    }
    std::string review = "Battle plan ID: " + local_plan.battle_id + "\nMap: " + local_plan.map_name +
        "\nValid draft; both captains approved; you are Ready on side " + std::to_string(local_plan.controlled_side) +
        ".\n\n" + practice_settings_text(settings) + "\n\n" + practice_lineup_text(local_plan) +
        "\n\nAll sides use local copies of the saved leaders and alliances. Turns begin with side 1, "
        "which may be a computer side; local sides take turns on this device. Hotseat shares this device and its visible turns. "
        "Other players do not join this game or need to be Ready.\n\n"
        "The plan and your readiness will be checked again after you confirm. "
        "This does not start or change the shared battle plan. No players or teams are reserved. "
        "Practice grants no account experience, team experience or rating.";
    if(settings.turn_limit != -1)
        review += "\nIf neither team wins within " + std::to_string(settings.turn_limit) +
            " turns, practice ends with the stock Defeat screen; it records no Alliance loss.";
    if(select_change("Confirm local team practice", review,
                     {"Start local practice"}) != 0) return;
    account::Reply latest;
    if(!load_practice_plan(profile, battle_id, actor, latest)) return;
    if(!same_practice_snapshot(*preview.battle, *latest.battle, actor)) {
        message("The battle plan changed while you were confirming practice. No practice was started. "
            "Open the plan again to review its current lineups and readiness.");
        return;
    }
    try {
        // Queue data only. The title-screen caller launches after every modal
        // account window has unwound; no combat runs inside this dialog.
        queue_practice(*latest.battle, output.profile, settings);
    } catch(...) {
        message("Local practice could not be prepared. No practice was started. Refresh the plan to check its current state.");
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

#include "alliance_game/progression_ui.inc"
#include "alliance_game/team_extension_ui.inc"

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
        bool captain = false, approved = false, selected = false, hero_selected = false, ready = false, both_approved = true, all_ready=true;
        for(const auto& team : battle.teams) {
            both_approved = both_approved && team.approved;
            if(team.captain_id == actor) { captain = true; approved = team.approved; }
            for(const auto& player : team.players) {all_ready=all_ready&&player.ready; if(player.id == actor) { selected = true; hero_selected = player.selection_present; ready = player.ready; }}
        }
        std::vector<std::string> actions{"View fixed lineups", "Refresh plan"};
        const int practice = can_practice_plan(battle, current_session().profile)
            ? static_cast<int>(actions.size()) : -1;
        if(practice >= 0) actions.push_back("Practice this plan");
        const int join=selected&&((battle.state=="draft"&&battle.validity=="valid"&&both_approved&&all_ready)||battle.state=="running")
            ?static_cast<int>(actions.size()):-1;
        if(join>=0) actions.push_back(battle.state=="running"?"Reconnect to my side":"Join shared battle");
        bool pending_result=false,available_replay=false;
        try {
            for(const auto& record:inspect_pending_shared(actor)) if(record.run_id==battle.id&&record.status=="pending") pending_result=true;
            if(battle.state=="completed") {(void)verified_shared_replay(actor,battle.id);available_replay=true;}
        } catch(...) { /* Unusable local files never enable replay or result actions. */ }
        const int upload=pending_result&&(battle.state=="running"||battle.state=="completed")?static_cast<int>(actions.size()):-1;
        if(upload>=0) actions.push_back("Send saved battle result");
        const int replay=available_replay?static_cast<int>(actions.size()):-1;
        if(replay>=0) actions.push_back("Watch completed battle replay");
        const int download=!available_replay&&battle.state=="completed"&&(selected||captain)?static_cast<int>(actions.size()):-1;
        if(download>=0) actions.push_back("Download completed battle replay");
        const int result_review=(battle.state=="running"||battle.state=="completed")?static_cast<int>(actions.size()):-1;
        if(result_review>=0) actions.push_back("Review shared battle result");
        const bool draft = battle.state == "draft";
        const bool all_heroes=std::all_of(battle.teams.begin(),battle.teams.end(),[](const auto& team){return std::all_of(team.players.begin(),team.players.end(),[](const auto& player){return player.selection_present;});});
        const int approve = draft && captain && !approved && all_heroes && battle.validity == "valid"
            ? static_cast<int>(actions.size()) : -1;
        if(approve >= 0) actions.push_back("Approve this plan");
        const int hero_choice = draft && selected ? static_cast<int>(actions.size()) : -1;
        if(hero_choice >= 0) actions.push_back(hero_selected ? "Change my hero" : "Choose my hero");
        const int readiness = draft && selected && hero_selected && (ready || (both_approved && battle.validity == "valid"))
            ? static_cast<int>(actions.size()) : -1;
        if(readiness >= 0) actions.push_back(ready ? "Set myself not ready" : "Set myself ready");
        const int cancel = draft && captain ? static_cast<int>(actions.size()) : -1;
        if(cancel >= 0) actions.push_back("Cancel this battle plan");
        const std::string consent = battle.state == "cancelled"
            ? "This plan is cancelled and inactive. Readiness is saved history; captain approval is inactive."
            : battle.state == "completed"
            ? "This battle is complete and inactive. Readiness is saved history; captain approval is inactive."
            : battle.state == "running"
            ? "This team battle has already started. Its saved planning consent cannot be changed here."
            : draft && battle.validity != "valid"
            ? "This draft is not eligible for a shared battle. Captain approval and saved readiness are inactive."
            : "Both captains must approve the saved lineups before selected players can become ready.";
        const int choice = select_change("Battle plan", battle.teams[0].name + " vs " + battle.teams[1].name +
            "\nBattle plan ID: " + battle.id +
            "\n" + battle.map.name + " — " + battle.state + "\n" + battle_validity_text(battle.validity) +
            "\n" + consent + "\n" + practice_availability_text(battle, current_session().profile) + (practice >= 0
                ? "\nPractice this plan lets you choose My side, Command my team or Local hotseat, plus local game settings. No account rewards are awarded." : ""), actions);
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
        if(choice==join||choice==upload) {join_shared_battle(profile,battle,choice==upload);if(shared_pending())return;continue;}
        if(choice==download) {download_shared_replay(profile,battle);if(shared_pending())return;continue;}
        if(choice==replay) {
            if(select_change("Completed battle replay","Watch this player's saved recording. This never grants experience.",{"Watch replay"})==0&&same_extension_actor(actor)) {
                queue_alliance_replay(AllianceReplay::shared,actor,battle.id);return;
            }
            continue;
        }
        if(choice == result_review) {shared_result_review(profile,battle);continue;}
        if(choice == practice) { practice_plan(profile, battle); if(practice_pending() || progression_pending() || shared_pending() || replay_pending()) return; continue; }
        if(choice == hero_choice) {
            account::Request change;change.operation="battle_select_hero";change.battle_id=battle.id;
            if(!choose_hero(profile,change.hero_id,change.form_id))continue;
            if(!same_extension_actor(actor))return;
            bool same=false;
            for(const auto& team:battle.teams)for(const auto& player:team.players)
                if(player.id==actor&&player.selection_present&&player.hero_id==change.hero_id&&player.form_id==change.form_id)
                    for(const auto& hero:current_session().profile.heroes)
                        if(hero.hero_id==change.hero_id&&hero.revision==player.hero_revision)same=true;
            if(same){message("That hero and form are already selected for your side.");continue;}
            if(select_change("Confirm hero selection","Changing your hero clears this plan's readiness and both captain approvals. Everyone reviews the updated lineups before Ready.",{"Save my hero"})!=0)continue;
            if(!same_extension_actor(actor))return;
            account::Reply changed;
            if(!team_request(profile,change,"Saving your selected hero…",changed))return;
            if(!changed.battle||changed.battle->id!=battle.id)throw std::runtime_error("Hero selection acknowledgement changed.");
            bool own=false;
            for(const auto& team:changed.battle->teams){
                if(team.approved)throw std::runtime_error("Hero selection kept approval.");
                for(const auto& player:team.players){
                    if(player.ready)throw std::runtime_error("Hero selection kept readiness.");
                    if(player.id==actor&&player.selection_present&&player.hero_id==change.hero_id&&player.form_id==change.form_id)own=true;
                }
            }
            if(!own)throw std::runtime_error("Hero selection changed actor.");
            message("Your selected hero was saved. Captain approvals and readiness must be confirmed again.");continue;
        }
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
        if(practice_pending() || progression_pending() || shared_pending() || replay_pending()) return;
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
                    std::to_string(member.hero_count) + " heroes";
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
            "For a shared battle, every selected player must be Ready and join their fixed seat. "
            "For personal practice, a selected player needs a valid draft, both captain approvals and only their own Ready. "
            "Choose Set myself ready first, then choose Practice this plan separately. "
            "Control your side, command your whole team against the computer, or play all sides locally in hotseat. "
            "Cancelled and completed plans cannot start practice. "
            "Practice does not change the plan or award account experience or rating.", {"My battle plans", "Propose battle"});
        if(choice < 0) return;
        if(choice == 0) my_battles(profile);
        if(choice == 1) propose_battle(profile);
        if(practice_pending() || progression_pending() || shared_pending() || replay_pending()) return;
    }
}

void teams(const alliance::TrustedProfile& profile)
{
    while(!current_session().token.empty()) {
        const int action = select("Teams",
            "Build your team in an area. Apply to join a team, or review applications as captain.",
            {"Browse areas and teams", "Create team", "My team", "My applications", "Review applications", "Application decisions",
             "Transfer captain", "Leave team", "Area rankings", "Battle plans", "Captain orders"});
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
        else if(action == 10) captain_orders(profile);
        if(practice_pending() || progression_pending() || shared_pending() || replay_pending()) return;
    }
}



void logout(const alliance::TrustedProfile& profile, bool notify = true)
{
    account::Request input;
    input.operation = "logout";
    input.token = current_session().token;
    account::Reply output;
    const bool completed = request(profile, input, "Logging out…", output);
    current_session().clear();
    if(!notify) return;
    if(completed && output.ok) {
        message("Logged out. The server has revoked this session.");
    } else {
        message("You are signed out on this device. The server did not confirm "
                "session revocation; the previous session will expire on the server.");
    }
}
} // namespace

PlayerSession::~PlayerSession() { clear_secret(token); }

std::optional<PlayerSession> player_session()
{
    const auto& session=current_session();
    if(session.token.empty())return std::nullopt;
    const char* path=std::getenv("WESNOTH_ALLIANCE_PROFILE");
    if(!path||!*path)return std::nullopt;
    const auto trusted=account::read_public_profile(path);
    const auto identity=trusted.address+":"+std::to_string(trusted.port)+":"+trusted.certificate_sha256;
    if(identity!=session.identity){
        request_session_reauthentication(session.profile.account_id, session.profile.database_generation);
        current_session().clear();return std::nullopt;
    }
    return PlayerSession{trusted,session.profile,session.token};
}

bool player_request(account::Request& input, account::Reply& output)
{
    auto session=player_session();if(!session)return false;
    const auto actor=session->account.account_id,generation=session->account.database_generation;
    input.token=session->token;
    if(!request(session->trusted,input,"Checking your game account…",output))return false;
    if(!output.ok){show_failure(output);return false;}
    if(current_session().token.empty()||current_session().profile.account_id!=actor||current_session().profile.database_generation!=generation)return false;
    return true;
}

bool ensure_player_session(const std::string& required_account,
                           const std::string& required_generation,
                           const std::string& notice)
{
    AccountNetworkActivity foreground;
    static bool active=false;
    if(active)return false;
    struct Guard{bool& flag;Guard(bool& f):flag(f){flag=true;}~Guard(){flag=false;}}guard(active);
    AuthenticationBackdrop background;
    auto bound_account = required_account;
    auto bound_generation = required_generation;
    auto gate_notice = notice;
    try {
        if(auto existing=player_session()) {
            if((!required_account.empty() && existing->account.account_id != required_account) ||
               (!required_generation.empty() && existing->account.database_generation != required_generation)) {
                logout(existing->trusted);
            } else if(reload_player(existing->trusted)) {
                clear_session_reauthentication();
                return true;
            } else {
                // A failed or cancelled foreground verification must return
                // to authentication, never translate into the engine's Quit.
                // Retain the actor even if reload_player cleared the session.
                if(bound_account.empty()) bound_account = existing->account.account_id;
                if(bound_generation.empty()) bound_generation = existing->account.database_generation;
                current_session().clear();
                if(gate_notice.empty()) gate_notice =
                    "Connection to the Windows server was lost. You have been signed out. Log in again.";
            }
        }
        const char* path=std::getenv("WESNOTH_ALLIANCE_PROFILE");
        if(!path||!*path){message("Import the trusted Windows server profile before starting the game.");return false;}
        const auto trusted=account::read_public_profile(path);
        for(;;) {
            while(current_session().token.empty()) {
                const int action=welcome_action(bound_account.empty() && bound_generation.empty(), gate_notice);
                if(action<0)return false;
                if(action==0)login(trusted, bound_account, bound_generation);
                else if(action==1)register_player(trusted);
            }
            clear_session_reauthentication();
            retry_story_results();
            // A queued-result response can revoke an account after login.
            // Keep the same opaque gate open until a current session exists.
            if(!current_session().token.empty()) return true;
        }
    } catch(const account::ProfileReadError& e){message(e.what());}
    catch(...){message("The game could not open its account connection. Check the trusted server and Windows services.");}
    return false;
}

bool choose_owned_hero(std::string& hero_id,std::string& form_id)
{
    auto session=player_session();return session&&choose_hero(session->trusted,hero_id,form_id);
}

void show_account_dialog()
{
    try {
        auto session=player_session();if(!session)return;
        for(;;){
            const int action=select("Heroes","Signed in as "+current_session().profile.username,{"My heroes","Account details"});
            if(action<0)return;
            if(action==0)heroes_dialog(session->trusted);
            else if(action==1)refresh(session->trusted);
            if(current_session().token.empty())return;
        }
    }catch(...){message("The heroes screen could not be opened. Return to the title screen and try again.");}
}
bool has_player_session() noexcept
{
    return !current_session().token.empty();
}

void invalidate_player_session()
{
    current_session().clear();
}

void logout_player()
{
    AccountNetworkActivity foreground;
    AuthenticationBackdrop background;
    clear_session_reauthentication();
    try {
        auto session=player_session();
        if(session)logout(session->trusted, false);
        else current_session().clear();
    }catch(...){
        current_session().clear();
    }
    // Reading a replaced profile can itself latch reauthentication. Explicit
    // logout always ends that old-account intent, including this failure path.
    clear_session_reauthentication();
}
void show_teams_dialog()
{
    if(practice_pending()||progression_pending()||shared_pending()||replay_pending())return;
    try {auto session=player_session();if(session)teams(session->trusted);}
    catch(...){message("The teams screen could not be opened. Return to the title screen and try again.");}
}
void show_training_dialog()
{
    try {auto session=player_session();if(session)campaign_progression(session->trusted);}
    catch(...){message("The training campaign could not be opened. Return to Campaigns and try again.");}
}
} // namespace alliance_game
