// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <string>
#include <vector>

namespace alliance_game {
// Data remains plain text. Artwork comes from the pinned game data or the verified APK overlay.
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
// Owned layouts use pinned artwork and do not replace the immutable data pack.
std::string prepare_home_window();
std::string prepare_login_window();
struct HeroGalleryEntry {
    std::string id,name,biography,availability,image;
    bool owned=false;
    int progress_percent=-1;
    std::string progress_label,card_progress;
    std::vector<BattleRow> forms;
};
struct HeroGalleryResult { int hero=-1,form=-1; };
HeroGalleryResult show_hero_gallery(const std::vector<HeroGalleryEntry>& entries,bool select_owned=false);
std::string battle_unit_image(const std::string& unit_type, bool portrait = true);
std::string battle_map_preview(const std::string& scenario_id);
std::string battle_map_data(const std::string& relative_file);
}
