"""Scoped menu styling in owned sources; original GUI maps/ABI stay unchanged."""
from hashlib import sha256

MODAL_SHA256 = "f1619b00cac2591e0daa6809e360826b0881352225d38daa494218a2017c21a8"
PREFERENCES_SHA256 = "084421f293c263e5d02f36c5621a9bf30196172fdc9853295d4eed39f8d39367"
GUI_SHA256 = "11e25ece783200933e3b2ca370468129c29d2a3b13d84ab9040b462eb1a15917"


def once(source, before, after):
    if source.count(before) != 1:
        raise ValueError("Pinned menu theme anchor differs; original preserved.")
    return source.replace(before, after, 1)


def generate_modal_theme_overlay(source: bytes) -> bytes:
    if sha256(source).hexdigest() != MODAL_SHA256:
        raise ValueError("Pinned modal source differs; original preserved.")
    source = b'#include "alliance_game/native_menu_theme.hpp"\n' + source
    # The temporary context survives the entire window base construction.
    # show() supplies the same context for dynamic rows and nested popups.
    source = once(source, b': window(get_window_builder(window_id))',
        b': window(alliance_game::MenuThemeScope(window_id).builder(window_id))')
    return once(source, b'bool modal_dialog::show(const unsigned auto_close_time)\n{\n',
        b'bool modal_dialog::show(const unsigned auto_close_time)\n{\n\talliance_game::MenuThemeScope menu_theme(id());\n')


def generate_gui_theme_overlay(source: bytes) -> bytes:
    if sha256(source).hexdigest() != GUI_SHA256:
        raise ValueError("Pinned GUI definition source differs; original preserved.")
    source = b'#include "alliance_game/native_menu_theme.hpp"\n' + source
    source = once(source, b'\treturn get_best_resolution(resolutions, [&](const resolution_definition_ptr& ptr) {',
        b'\tconst auto selected = get_best_resolution(resolutions, [&](const resolution_definition_ptr& ptr) {')
    return once(source, b'\t});\n}\n\nconst builder_window::window_resolution& get_window_builder',
        b'\t});\n\treturn alliance_game::menu_control_resolution(control_type, definition, selected);\n}\n\nconst builder_window::window_resolution& get_window_builder')


def generate_preferences_theme_overlay(source: bytes) -> bytes:
    if sha256(source).hexdigest() != PREFERENCES_SHA256:
        raise ValueError("Pinned Preferences source differs; original preserved.")
    source = b'#include "alliance_game/native_menu_theme.hpp"\n' + source
    # Preferences populates lists in its derived constructor, after the base
    # window construction context has ended and before modal_dialog::show().
    return once(source, b'\n{\n\tinitialize_callbacks();\n}',
        b'\n{\n\talliance_game::MenuThemeScope menu_theme(id());\n\tinitialize_callbacks();\n}')
