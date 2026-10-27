"""Build the owned nine-destination home screen from the pinned engine source."""
from hashlib import sha256
SOURCE_SHA256 = "8ef360459131a123dfccac51ce07898ca9577c9dab8aa9dee8b8f470f1a727a5"

def generate_title_overlay(source: bytes) -> bytes:
    if sha256(source).hexdigest() != SOURCE_SHA256:
        raise ValueError("Pinned title-screen source differs; original preserved.")
    include = b'#include "gui/dialogs/title_screen.hpp"\n'
    result = source.replace(include, include + b'#include "alliance_game/account_ui.hpp"\n#include "alliance_game/battle_ui.hpp"\n#include "alliance_game/home_ui.hpp"\n#include "alliance_game/battle_launch.hpp"\n#include "alliance_game/progression_plan.hpp"\n#include "alliance_game/shared_launch.hpp"\n#include "alliance_game/replay_launch.hpp"\n')
    result = result.replace(b': modal_dialog(window_id())', b': modal_dialog(alliance_game::prepare_home_window())')
    start = result.index(b'void title_screen::init_callbacks()\n{')
    end = result.index(b'\nvoid title_screen::update_static_labels()', start)
    result = result[:start] + b'''void title_screen::init_callbacks()
{
    set_click_dismiss(false); set_enter_disabled(true); set_escape_disabled(true);
    connect_signal<event::SDL_VIDEO_RESIZE>(std::bind(&title_screen::on_resize, this));
    const auto refresh_profile=[this]() {
        if(!alliance_game::player_session()) { set_retval(RELOAD_UI); return; }
        if(!alliance_game::ensure_player_session()) { set_retval(QUIT_GAME); return; }
        if(auto player=alliance_game::player_session())
            alliance_game::home_ui::set_profile(*this,player->account.username,player->account.heroes.size());
    };
    if(auto player=alliance_game::player_session())
        alliance_game::home_ui::set_profile(*this,player->account.username,player->account.heroes.size());
    register_button("campaign", hotkey::TITLE_SCREEN__CAMPAIGN, [this,refresh_profile]() {
        try { if(game_.new_campaign()) { hide(); set_retval(LAUNCH_GAME); return; } }
        catch(const config::error& e) { gui2::show_error_message(e.what()); }
        refresh_profile();
    });
    register_button("multiplayer", hotkey::TITLE_SCREEN__MULTIPLAYER, std::bind(&title_screen::button_callback_multiplayer,this));
    register_button("community", hotkey::HOTKEY_NULL, [this,refresh_profile]() {
        alliance_game::show_teams_dialog();
        if(!alliance_game::player_session()) { set_retval(RELOAD_UI); return; }
        if(alliance_game::shared_pending()) set_retval(alliance_game::shared_title_result);
        else if(alliance_game::replay_pending()) set_retval(alliance_game::replay_title_result);
        else refresh_profile();
    });
    register_button("load", hotkey::HOTKEY_LOAD_GAME, [this,refresh_profile]() {
        if(game_.load_game_prompt()) { hide(); set_retval(LOAD_GAME); }
        else refresh_profile();
    });
    register_button("addons", hotkey::TITLE_SCREEN__ADDONS, [this,refresh_profile]() {
        alliance_game::show_account_dialog();
        refresh_profile();
    });
    register_button("achievements", hotkey::HOTKEY_ACHIEVEMENTS, [refresh_profile] { dialogs::achievements_dialog::display(); refresh_profile(); });
    register_button("editor", hotkey::TITLE_SCREEN__EDITOR, [this]() { set_retval(MAP_EDITOR); });
    register_button("preferences", hotkey::HOTKEY_PREFERENCES, [this,refresh_profile] { show_preferences(); refresh_profile(); });
    register_button("quit", hotkey::HOTKEY_QUIT_TO_DESKTOP, [this]() { set_retval(QUIT_GAME); });
    alliance_game::home_ui::bind(*this);
}
''' + result[end:]
    start=result.index(b'void title_screen::button_callback_multiplayer()\n{')
    end=result.index(b'\nvoid title_screen::button_callback_cores()',start)
    return result[:start] + b'''void title_screen::button_callback_multiplayer()
{
    if(!alliance_game::ensure_player_session()) { set_retval(QUIT_GAME); return; }
    auto session=alliance_game::player_session(); if(!session)return;
    game_.select_mp_server(session->trusted.address + ":15000"); set_retval(MP_CONNECT);
}
''' + result[end:]
