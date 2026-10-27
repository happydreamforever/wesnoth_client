// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/battle_ui.hpp"
#include "alliance_game/product_ui_layout.hpp"
#include "alliance_game/home_ui.hpp"
#include "config.hpp"
#include "filesystem.hpp"
#include "game_config_manager.hpp"
#include "gui/core/gui_definition.hpp"
#include "gui/dialogs/modal_dialog.hpp"
#include "gui/widgets/button.hpp"
#include "gui/widgets/image.hpp"
#include "gui/widgets/label.hpp"
#include "gui/widgets/listbox.hpp"
#include "gui/widgets/menu_button.hpp"
#include "gui/widgets/minimap.hpp"
#include "gui/widgets/progress_bar.hpp"
#include "gui/widgets/scroll_label.hpp"
#include "gui/widgets/text_box.hpp"
#include "units/types.hpp"
#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <stdexcept>

namespace alliance_game {
std::string prepare_home_window() { return home_ui::prepare_window(); }
std::string prepare_login_window()
{
    const std::string id="alliance_login";
    auto& windows=gui2::current_gui->second.window_types;if(windows.count(id))return id;
    config definition;auto& grid=product_ui::window(definition,id,true);
    auto& heading_grid=product_ui::cell(product_ui::row(grid),"grid");
    auto& heading=product_ui::row(heading_grid);
    auto& title=product_ui::cell(heading,"label","auth_title");title["definition"]="alliance_title";title["label"]="Wesnoth";
    auto& close=product_ui::cell(heading,"button","cancel",0);close["label"]="×";close["definition"]="alliance_close";close["tooltip"]="Quit";close["return_value"]=13;
    product_ui::text(grid,"login_label","","alliance_caption");
    product_ui::text(grid,"username_label","Username");product_ui::cell(product_ui::row(grid),"text_box","user_name");
    product_ui::text(grid,"password_label","Password");auto& password_grid=product_ui::cell(product_ui::row(grid),"grid");auto& password_row=product_ui::row(password_grid);product_ui::cell(password_row,"text_box","password");auto& eye=product_ui::cell(password_row,"toggle_button","remember_password",0);eye["definition"]="alliance_eye";eye["tooltip"]="Show password";
    product_ui::text(grid,"confirmation_label","Confirm password");product_ui::cell(product_ui::row(grid),"text_box","confirmation");
    auto& actions_grid=product_ui::cell(product_ui::row(grid),"grid");
    auto& actions=product_ui::row(actions_grid);product_ui::cell(actions,"button","ok")["label"]="Log in";
    auto& signup=product_ui::cell(actions,"button","signup");signup["label"]="Sign up";signup["return_value"]=11;
    windows.emplace(id,gui2::builder_window(definition));return id;
}
namespace {
config& row(config& grid, int grow = 0)
{
    auto& result = grid.add_child("row"); result["grow_factor"] = grow; return result;
}
config& cell(config& row, const std::string& type, const std::string& id = "", int grow = 1)
{
    auto& column = row.add_child("column");
    column["grow_factor"] = grow;
    column["horizontal_grow"] = true; column["vertical_grow"] = true;
    column["border"] = "all"; column["border_size"] = 6;
    auto& result = column.add_child(type);
    if(type != "grid") { result["id"] = id; result["definition"] = type=="button"?"alliance_button":type=="text_box"?"alliance_input":type=="progress_bar"?"alliance_progress":"default"; }
    return result;
}
config& label(config& grid, const std::string& id, const char* definition = "default")
{
    auto& result = cell(row(grid), "label", id);
    result["definition"] = definition; result["wrap"] = true;
    result["use_markup"] = false; return result;
}
std::string lower(std::string value)
{
    std::transform(value.begin(),value.end(),value.begin(),[](unsigned char c) {return std::tolower(c);});
    return value;
}
// Build the layout with the same stock GUI2 widgets as Campaigns and Multiplayer.
// The definition lives in this binary; the original data pack is never rewritten.
std::string prepare_window()
{
    const std::string id = "alliance_battle_browser";
    auto& windows = gui2::current_gui->second.window_types;
    if(windows.count(id)) return id;
    config map_style; map_style["id"] = "alliance_preview";
    map_style["description"] = "Battle map preview";
    auto& map_resolution = map_style.add_child("resolution");
    map_resolution["min_width"] = 160; map_resolution["default_width"] = 160;
    map_resolution["min_height"] = 120; map_resolution["default_height"] = 120;
    map_resolution.add_child("state_enabled").add_child("draw");
    gui2::add_single_widget_definition("minimap", "alliance_preview", map_style);
    product_ui::prepare_theme();
    config definition; definition["id"] = id; definition["description"] = "Battle and hero browser";
    auto& resolution = definition.add_child("resolution");
    resolution["definition"] = "alliance_dark";
    resolution["automatic_placement"] = false;
    resolution["width"] = "(min(screen_width - 32, 1440))";
    resolution["height"] = "(screen_height - 32)";
    resolution["x"] = "((screen_width - min(screen_width - 32, 1440)) / 2)";
    resolution["y"] = 16;
    resolution.add_child("tooltip")["id"] = "tooltip";
    resolution.add_child("helptip")["id"] = "tooltip";
    auto& grid = resolution.add_child("grid");
    label(grid,"battle_title","alliance_title");
    label(grid,"battle_subtitle","alliance_caption");
    auto& body = cell(row(grid,1),"grid");
    auto& columns = row(body,1);
    auto& left = cell(columns,"grid", "", 2);
    auto& search = cell(row(left),"text_box","battle_search");
    search["hint_text"] = "Search this list";
    auto& list = cell(row(left,1),"listbox","battle_list");
    list["horizontal_scrollbar_mode"] = "never";
    list["vertical_scrollbar_mode"] = "auto";
    auto& item = cell(row(list.add_child("list_definition")),"toggle_panel");
    auto& item_grid = item.add_child("grid");
    auto& item_row = row(item_grid);
    auto& icon = cell(item_row,"image","row_image",0); icon["definition"] = "centered";
    auto& text = cell(item_row,"grid");
    label(text,"row_title","default_bold");
    label(text,"row_subtitle","gold_small");
    auto& right_panel = cell(columns,"panel","battle_panel",3);
    right_panel["definition"] = "box_display";
    auto& right = right_panel.add_child("grid");
    label(right,"detail_title","gold_large");
    label(right,"detail_subtitle","default_small");
    auto& art = cell(row(right),"image","detail_image"); art["definition"] = "centered";
    cell(row(right),"minimap","detail_map")["definition"] = "alliance_preview";
    cell(row(right),"progress_bar","detail_progress");
    label(right,"detail_progress_label","gold_small");
    auto& detail = cell(row(right,1),"scroll_label","detail_text");
    detail["wrap"] = true; detail["use_markup"] = false;
    auto& actions = cell(row(grid),"grid","battle_actions");
    for(int r=0;r<3;++r) {
        auto& buttons = row(actions);
        for(int c=0;c<4;++c) cell(buttons,"button","battle_action_"+std::to_string(r*4+c));
    }
    auto& footer = cell(row(grid),"grid");
    auto& footer_row = row(footer);
    auto& hint = cell(footer_row,"label","battle_hint");
    hint["definition"] = "default_small"; hint["wrap"] = true;
    auto& back = cell(footer_row,"button","cancel",0); back["label"] = "Back";
    windows.emplace(id,gui2::builder_window(definition));
    return id;
}
class BattleDialog final : public gui2::dialogs::modal_dialog {
public:
    explicit BattleDialog(const BattleView& view) : modal_dialog(prepare_window()), view_(view)
    { set_allow_plugin_skip(false); }
    BattleResult result;
private:
    const BattleView& view_;
    const std::string& window_id() const override
    {
        static const std::string id="alliance_battle_browser";return id;
    }
    void update()
    {
        const int selected=find_widget<gui2::listbox>("battle_list").get_selected_row();
        result.row=selected;
        const BattleRow* value=selected>=0&&static_cast<std::size_t>(selected)<view_.rows.size()?&view_.rows[selected]:nullptr;
        find_widget<gui2::label>("detail_title").set_label(value?value->title:"Nothing to show yet");
        find_widget<gui2::label>("detail_subtitle").set_label(value?value->subtitle:"");
        find_widget<gui2::scroll_label>("detail_text").set_label(value?value->detail:"Try another search or use an action below.");
        auto& art=find_widget<gui2::image>("detail_image");
        auto& map=find_widget<gui2::minimap>("detail_map");
        const bool has_map=value&&!value->map_data.empty();
        art.set_visible(has_map?gui2::widget::visibility::invisible:gui2::widget::visibility::visible);
        art.set_label((value&&!value->image.empty()?value->image:"icons/icon-multiplayer.png")+"~SCALE_INTO(180,96)");
        map.set_visible(has_map?gui2::widget::visibility::visible:gui2::widget::visibility::invisible);
        if(has_map) map.set_map_data(value->map_data);
        auto& progress=find_widget<gui2::progress_bar>("detail_progress");
        auto& progress_label=find_widget<gui2::label>("detail_progress_label");
        const bool has_progress=value&&value->progress_percent>=0;
        progress.set_visible(has_progress?gui2::widget::visibility::visible:gui2::widget::visibility::invisible);
        progress_label.set_visible(has_progress?gui2::widget::visibility::visible:gui2::widget::visibility::invisible);
        if(has_progress) {
            progress.set_percentage(std::clamp(value->progress_percent,0,100));
            progress_label.set_label(value->progress_label);
        }
        for(std::size_t i=0;i<view_.actions.size();++i)
            find_widget<gui2::button>("battle_action_"+std::to_string(i)).set_active(view_.actions[i].enabled&&(!(view_.selected_action||view_.actions[i].requires_selection)||value));
    }
    void filter()
    {
        auto& list=find_widget<gui2::listbox>("battle_list");
        const auto search=lower(find_widget<gui2::text_box>("battle_search").get_value());
        boost::dynamic_bitset<> shown(view_.rows.size());
        for(std::size_t i=0;i<view_.rows.size();++i)
            shown[i]=search.empty()||lower(view_.rows[i].title+" "+view_.rows[i].subtitle).find(search)!=std::string::npos;
        list.set_row_shown(shown);
        update();
    }
    void pre_show() override
    {
        find_widget<gui2::label>("battle_title").set_label(view_.title);
        find_widget<gui2::label>("battle_subtitle").set_label(view_.subtitle);
        find_widget<gui2::label>("battle_hint").set_label("");
        auto& list=find_widget<gui2::listbox>("battle_list");
        for(const auto& value:view_.rows) {
            list.add_row(gui2::widget_data{
                {"row_title",{{"label",value.title},{"use_markup","false"}}},
                {"row_subtitle",{{"label",value.subtitle},{"use_markup","false"}}},
                {"row_image",{{"label",(value.image.empty()?"icons/icon-multiplayer.png":value.image)+"~SCALE_INTO(56,56)"}}}});
        }
        if(view_.selected>=0&&static_cast<std::size_t>(view_.selected)<view_.rows.size())list.select_row(view_.selected);
        gui2::event::connect_signal_notify_modified(list,[this](auto&&...){update();});
        auto& search=find_widget<gui2::text_box>("battle_search");
        search.set_max_input_length(80);
        gui2::event::connect_signal_notify_modified(search,[this](auto&&...){filter();});
        for(int i=0;i<12;++i) {
            auto& button=find_widget<gui2::button>("battle_action_"+std::to_string(i));
            if(static_cast<std::size_t>(i)>=view_.actions.size()) {
                button.set_visible(gui2::widget::visibility::invisible); continue;
            }
            button.set_label(view_.actions[i].label);
            gui2::event::connect_signal_mouse_left_click(button,[this,i](auto&&...){
                if(!view_.actions[i].enabled)return;
                result.row=find_widget<gui2::listbox>("battle_list").get_selected_row();
                if((view_.selected_action||view_.actions[i].requires_selection)&&result.row<0)return;
                result.action=i;set_retval(gui2::retval::OK);
            });
        }
        set_enter_disabled(true);
        update(); keyboard_capture(&list); add_to_keyboard_chain(&search);
    }
    void post_show() override
    {
        if(get_retval()!=gui2::retval::OK)result.action=-1;
    }
};
}
BattleResult show_battle_view(const BattleView& view)
{
    if(view.actions.size()>12)throw std::invalid_argument("Too many battle actions.");
    BattleDialog dialog(view);dialog.show();return dialog.result;
}
std::string battle_unit_image(const std::string& type, bool portrait)
{
    const auto* unit=unit_types.find(type);
    if(!unit)return "icons/icon-random.png";
    if(portrait&&!unit->big_profile().empty())return unit->big_profile();
    return unit->image().empty()?"icons/icon-random.png":unit->image();
}
std::string battle_map_data(const std::string& file)
{
    if(file.empty()||file.front()=='/'||file.find("..")!=std::string::npos||file.find(':')!=std::string::npos)return {};
    const auto path=filesystem::get_wml_location(file);
    return path?filesystem::read_file(*path):std::string{};
}
std::string battle_map_preview(const std::string& id)
{
    // Team browsing is available from the title screen before Multiplayer has
    // loaded its scenarios. These are the same three pinned planning maps.
    if(id=="multiplayer_Isars_Cross")return battle_map_data("multiplayer/maps/4p_Isars_Cross.map");
    if(id=="multiplayer_Hexcake")return battle_map_data("multiplayer/maps/6p_Hexcake.map");
    if(id=="multiplayer_8p_Mokena_Prairie")return battle_map_data("multiplayer/maps/8p_Mokena_Prairie.map");
    const auto* manager=game_config_manager::get();
    if(!manager)return {};
    for(const config& scenario:manager->game_config().child_range("multiplayer"))
        if(scenario["id"].str()==id) {
            const auto data=scenario["map_data"].str();
            return data.empty()?battle_map_data(scenario["map_file"].str()):data;
        }
    return {};
}

#include "alliance_game/hero_gallery.inc"
}
