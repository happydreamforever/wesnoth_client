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
_NEW = b'''	find_widget<button>("addons").set_label("Alliance");
	find_widget<button>("addons").set_tooltip("Player account and personal leader");
	register_button("addons", hotkey::TITLE_SCREEN__ADDONS, [this]() {
		alliance_game::show_account_dialog();
		if(alliance_game::practice_pending()) {
			set_retval(alliance_game::practice_title_result);
		}
	});
'''


def generate_title_overlay(source: bytes) -> bytes:
    """Accept only the pinned original; change the Add-ons entry in a copy."""
    if sha256(source).hexdigest() != SOURCE_SHA256:
        raise ValueError("Pinned title-screen source differs; original preserved.")
    if source.count(_INCLUDE) != 1 or source.count(_OLD) != 1:
        raise ValueError("Pinned title-screen anchors differ; original preserved.")
    return source.replace(
        _INCLUDE, _INCLUDE + b'#include "alliance_game/account_ui.hpp"\n#include "alliance_game/battle_launch.hpp"\n'
    ).replace(_OLD, _NEW)
