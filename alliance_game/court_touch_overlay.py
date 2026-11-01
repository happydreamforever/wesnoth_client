"""Android-only Court consumables in the visible native Actions menu."""
from hashlib import sha256
PIN = '25ad835b453ec2c47e7975d6d2b3b01a82faa0a4a700aa3ad487538be352d883'

def generate(source):
    if sha256(source).hexdigest() != PIN:
        raise ValueError('Pinned native Actions source differs; original preserved.')
    anchor = b'\tif(items.empty()) {\n\t\treturn;\n\t}\n\n\tcommand_executor::show_menu(items, menu_loc, context_menu);'
    if source.count(anchor) != 1:
        raise ValueError('Pinned native Actions menu boundary differs.')
    addition = b'''\talliance_game::mobile_actions::append(items_arg, items, context_menu,
        [this](std::vector<config>& available) { expand_wml_commands(available); },
        [this](const std::string& id) { return can_execute_command(hotkey::ui_command(id)); });
    const bool court_controls = std::any_of(items.begin(), items.end(), [](const config& item) { return item["id"].str().rfind("wml_menu:court_item_", 0) == 0; });
    alliance_game::MenuThemeScope court_theme(court_controls ? "alliance_court_actions" : "");
'''
    return b'#include "court_touch_actions.hpp"\n#include "alliance_game/native_menu_theme.hpp"\n' + source.replace(anchor, addition + anchor)
