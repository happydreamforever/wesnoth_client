"""Generate one owned title-screen source without editing the pinned engine."""

from hashlib import sha256

SOURCE_SHA256 = "8ef360459131a123dfccac51ce07898ca9577c9dab8aa9dee8b8f470f1a727a5"

_INCLUDE = b'#include "gui/dialogs/title_screen.hpp"\n'
_OLD = b'''	register_button("addons", hotkey::TITLE_SCREEN__ADDONS, [this]() {
		if(manage_addons()) {
			set_retval(RELOAD_GAME_DATA);
		}
	});
'''
_NEW = b'''	find_widget<button>("addons").set_label("Heroes");
	find_widget<button>("addons").set_tooltip("Your collected campaign heroes and account");
	register_button("addons", hotkey::TITLE_SCREEN__ADDONS, [this]() {
		alliance_game::show_account_dialog();
        if(!alliance_game::player_session()) { set_retval(RELOAD_UI); return; }
		if(alliance_game::practice_pending()) {
			set_retval(alliance_game::practice_title_result);
		} else if(alliance_game::progression_pending()) {
			set_retval(alliance_game::progression_title_result);
		} else if(alliance_game::shared_pending()) {
			set_retval(alliance_game::shared_title_result);
		} else if(alliance_game::replay_pending()) {
			set_retval(alliance_game::replay_title_result);
		}
	});
'''


def generate_title_overlay(source: bytes) -> bytes:
    """Accept only the pinned original; change the Add-ons entry in a copy."""
    if sha256(source).hexdigest() != SOURCE_SHA256:
        raise ValueError("Pinned title-screen source differs; original preserved.")
    if source.count(_INCLUDE) != 1 or source.count(_OLD) != 1:
        raise ValueError("Pinned title-screen anchors differ; original preserved.")
    result = source.replace(
        _INCLUDE, _INCLUDE + b'#include "alliance_game/account_ui.hpp"\n#include "alliance_game/battle_launch.hpp"\n#include "alliance_game/progression_plan.hpp"\n#include "alliance_game/shared_launch.hpp"\n#include "alliance_game/replay_launch.hpp"\n'
    ).replace(_OLD, _NEW)

    community = b'\tregister_button("community", hotkey::HOTKEY_NULL,\n\t\t[] { dialogs::game_version::display(4); }); // shows the 5th tab, community\n'
    replacement = _NEW.replace(b'find_widget<button>("addons").set_label("Heroes")', b'find_widget<button>("community").set_label("Teams")').replace(b'find_widget<button>("addons").set_tooltip("Your collected campaign heroes and account")', b'find_widget<button>("community").set_tooltip("Teams, battle plans and captain orders")').replace(b'register_button("addons", hotkey::TITLE_SCREEN__ADDONS', b'register_button("community", hotkey::HOTKEY_NULL').replace(b'show_account_dialog()', b'show_teams_dialog()')
    if result.count(community) != 1:
        raise ValueError("Pinned team-menu source differs; original preserved.")
    result = result.replace(community, replacement)
    campaign = b'\tregister_button("campaign", hotkey::TITLE_SCREEN__CAMPAIGN, [this]() {\n\t\ttry{'
    campaign_menu = b'''\tregister_button("campaign", hotkey::TITLE_SCREEN__CAMPAIGN, [this]() {
        gui2::dialogs::simple_item_selector choice("Campaigns",
            "Play a story to collect its named heroes, or train a hero you already own.",
            {"Story campaigns", "Hero training"});
        choice.set_allow_plugin_skip(false);
        if(!choice.show()) return;
        if(choice.selected_index()==1) {
            alliance_game::show_training_dialog();
            if(!alliance_game::player_session()) { set_retval(RELOAD_UI); return; }
            if(alliance_game::progression_pending()) set_retval(alliance_game::progression_title_result);
            else if(alliance_game::replay_pending()) set_retval(alliance_game::replay_title_result);
            return;
        }
        if(choice.selected_index()!=0) return;
\t\ttry{'''
    if result.count(campaign) != 1:
        raise ValueError("Pinned campaign-menu source differs; original preserved.")
    result = result.replace(campaign, campaign_menu)
    start = result.index(b'void title_screen::button_callback_multiplayer()\n{')
    end = result.index(b'\nvoid title_screen::button_callback_cores()', start)
    result = result[:start] + b'''void title_screen::button_callback_multiplayer()
{
    if(!alliance_game::ensure_player_session()) { set_retval(QUIT_GAME); return; }
    const auto session = alliance_game::player_session();
    if(!session) return;
    game_.select_mp_server(session->trusted.address + ":15000");
    set_retval(MP_CONNECT);
}
''' + result[end:]
    return result
