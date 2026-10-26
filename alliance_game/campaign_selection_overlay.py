"""Add scenario reward previews to the pinned native campaign selector."""
from hashlib import sha256
SOURCE_SHA256 = "658c36516151575deca8cdca232ac6ebb5f3f53089a24366a5a832bae014a824"

def generate_campaign_selection_overlay(source: bytes) -> bytes:
    if sha256(source).hexdigest() != SOURCE_SHA256:
        raise ValueError("Pinned campaign selector differs; original preserved.")
    text = source.decode()
    anchor = '#include "gui/dialogs/campaign_selection.hpp"'
    text = text.replace(anchor, anchor + '\n#include "alliance_game/campaign_rewards.hpp"', 1)
    text = text.replace(': modal_dialog(window_id())', ': modal_dialog(alliance_game::prepare_campaign_window())', 1)
    anchor = '\t\tpages.add_page({'
    if text.count(anchor) != 1:
        raise ValueError("Campaign reward insertion anchor differs.")
    text = text.replace(anchor, '\t\tauto& reward_page=pages.add_page({', 1)
    anchor = '\t\tpage_ids_.push_back(campaign["id"]);'
    text = text.replace(anchor, '\t\talliance_game::attach_campaign_rewards(reward_page,campaign["id"].str());\n' + anchor, 1)
    return text.encode()
