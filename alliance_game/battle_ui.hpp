// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <string>
#include <vector>

namespace alliance_game {
// Data remains plain text. Only artwork from the pinned game data is used.
struct BattleRow {
    std::string title, subtitle, detail, image, map_data;
    int progress_percent = -1;
    std::string progress_label = {};
};
struct BattleAction {
    std::string label;
    bool enabled = true;
    bool requires_selection = false;
};
struct BattleView {
    std::string title, subtitle;
    std::vector<BattleRow> rows;
    std::vector<BattleAction> actions;
    int selected = 0;
    bool selected_action = false;
};
struct BattleResult {
    int row = -1;
    int action = -1;
};

// Selecting a row only updates its preview; only explicit buttons accept.
// Back/Escape never invokes an action. Empty lists remain usable for creation.
BattleResult show_battle_view(const BattleView& view);
std::string battle_unit_image(const std::string& unit_type, bool portrait = true);
std::string battle_map_preview(const std::string& scenario_id);
std::string battle_map_data(const std::string& relative_file);
}
