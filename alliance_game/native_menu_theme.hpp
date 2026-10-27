// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "alliance_game/product_ui_layout.hpp"
#include "alliance_game/native_touch_layout.hpp"
#include "gui/widgets/button.hpp"
#include "gui/widgets/chatbox.hpp"
#include "gui/widgets/combobox.hpp"
#include "gui/widgets/horizontal_scrollbar.hpp"
#include "gui/widgets/label.hpp"
#include "gui/widgets/listbox.hpp"
#include "gui/widgets/menu_button.hpp"
#include "gui/widgets/multiline_text.hpp"
#include "gui/widgets/multimenu_button.hpp"
#include "gui/widgets/panel.hpp"
#include "gui/widgets/progress_bar.hpp"
#include "gui/widgets/repeating_button.hpp"
#include "gui/widgets/rich_label.hpp"
#include "gui/widgets/scroll_label.hpp"
#include "gui/widgets/scroll_text.hpp"
#include "gui/widgets/scrollbar_panel.hpp"
#include "gui/widgets/slider.hpp"
#include "gui/widgets/spinner.hpp"
#include "gui/widgets/tab_container.hpp"
#include "gui/widgets/text_box.hpp"
#include "gui/widgets/toggle_button.hpp"
#include "gui/widgets/toggle_panel.hpp"
#include "gui/widgets/tree_view.hpp"
#include "gui/widgets/unit_preview_pane.hpp"
#include "gui/widgets/vertical_scrollbar.hpp"
#include "gui/widgets/window.hpp"
#include <map>
#include <tuple>

namespace alliance_game {
// This is a construction/show context, never a selected GUI theme. The GUI
// registries, preferences and pinned definitions are not modified. A window
// retains its own cloned resolutions after the context has unwound.
inline unsigned& menu_theme_depth() { static thread_local unsigned depth=0; return depth; }
inline bool menu_theme_root(const std::string& id) {
    return id.compare(0,9,"alliance_")==0 || id=="title_screen" ||
        id=="campaign_selection" || id=="game_load" || id=="achievements_dialog" ||
        id=="preferences" || id=="preferences_dialog";
}
inline gui2::builder_grid_ptr menu_load_grid(const gui2::builder_grid_ptr& original) {
    if(!original)return {};
    auto copy=std::make_shared<gui2::builder_grid>(*original);
    for(auto& item:copy->widgets) {
        if(const auto nested=std::dynamic_pointer_cast<gui2::builder_grid>(item)) {
            item=menu_load_grid(nested);
        } else if(item->id=="savegame_list") {
            // An empty native list measures its content, not its header width.
            // Retain its native rows/sort callbacks while reserving a readable
            // column for the two touch-sized Name/Date headings. The stacked
            // spacer is the engine's standard minimum-size layout technique.
            config cfg;cfg["definition"]="default";
            for(int layer=0;layer<2;++layer) {
                auto& row=cfg.add_child("layer").add_child("row");row["grow_factor"]=1;
                auto& column=row.add_child("column");column["grow_factor"]=1;
                column["horizontal_grow"]=true;column["vertical_grow"]=true;
                auto& spacer=column.add_child("spacer");spacer["definition"]="default";
                if(layer==0){spacer["width"]=320;spacer["height"]=48;}
            }
            auto stack=std::make_shared<gui2::implementation::builder_stacked_widget>(cfg);
            stack->stack.at(1).widgets.at(0)=item;item=std::move(stack);
        }
    }
    return copy;
}
inline gui2::builder_grid_ptr menu_tab_grid(const gui2::builder_grid_ptr& original,bool horizontal) {
    if(!original)return {};
    auto copy=std::make_shared<gui2::builder_grid>(*original);
    for(std::size_t index=0;index<copy->widgets.size();++index) {
        auto& item=copy->widgets[index];
        if(const auto nested=std::dynamic_pointer_cast<gui2::builder_grid>(item)) {
            item=menu_tab_grid(nested,horizontal);
        } else if(item->id=="_tab_list") {
            if(!horizontal) {
                // Six 82px native tabs fit the 532px Preferences viewport.
                // Pin them to its top instead of centring in the taller page.
                copy->flags[index]=(copy->flags[index]&~gui2::grid::VERTICAL_MASK)|gui2::grid::VERTICAL_ALIGN_TOP;
                continue;
            }
            // Horizontal native lists can shrink below their touch-sized rows.
            // Keep the tab strip reachable while the surrounding page scrolls.
            config cfg;cfg["definition"]="default";
            for(int layer=0;layer<2;++layer) {
                auto& row=cfg.add_child("layer").add_child("row");row["grow_factor"]=1;
                auto& column=row.add_child("column");column["grow_factor"]=1;
                column["horizontal_grow"]=true;column["vertical_grow"]=true;
                auto& spacer=column.add_child("spacer");spacer["definition"]="default";
                if(layer==0)spacer["height"]=48;
            }
            auto stack=std::make_shared<gui2::implementation::builder_stacked_widget>(cfg);
            stack->stack.at(1).widgets.at(0)=item;item=std::move(stack);
        }
    }
    return copy;
}
inline gui2::builder_grid_ptr menu_preferences_grid(const gui2::builder_grid_ptr& original) {
    if(!original)return {};
    auto copy=std::make_shared<gui2::builder_grid>(*original);
    for(auto& item:copy->widgets) {
        if(const auto nested=std::dynamic_pointer_cast<gui2::builder_grid>(item)) {
            item=menu_preferences_grid(nested);
        } else if(item->id=="pager") {
            // Touch-sized preferences exceed the native 650px window cap.
            // Scroll only the existing pager, leaving the native title and
            // Close footer reachable. All page widgets/callbacks stay native.
            config cfg;cfg["id"]="alliance_preferences_scroll";cfg["definition"]="default";
            cfg["horizontal_scrollbar_mode"]="never";cfg["vertical_scrollbar_mode"]="initial_auto";
            auto& row=cfg.add_child("definition").add_child("row");row["grow_factor"]=1;
            auto& column=row.add_child("column");column["grow_factor"]=1;
            column["horizontal_grow"]=true;column["vertical_grow"]=true;
            column.add_child("spacer")["definition"]="default";
            auto scroll=std::make_shared<gui2::implementation::builder_scrollbar_panel>(cfg);
            scroll->grid_->widgets.at(0)=item;item=std::move(scroll);
        }
    }
    return copy;
}
class MenuThemeScope {
    bool enabled_;
public:
    explicit MenuThemeScope(const std::string& id)
        : enabled_(menu_theme_depth()!=0 || menu_theme_root(id)) { if(enabled_)++menu_theme_depth(); }
    ~MenuThemeScope() { if(enabled_)--menu_theme_depth(); }
    MenuThemeScope(const MenuThemeScope&)=delete;
    MenuThemeScope& operator=(const MenuThemeScope&)=delete;
    const gui2::builder_window::window_resolution& builder(const std::string& id) const {
        const auto& original=gui2::get_window_builder(id);
        if(id!="game_load" && id!="preferences_dialog")return original;
        using Resolution=gui2::builder_window::window_resolution;
        static std::map<const Resolution*,Resolution> copies;
        const auto found=copies.find(&original);if(found!=copies.end())return found->second;
        auto copy=original;copy.grid=id=="game_load"?menu_load_grid(original.grid):menu_preferences_grid(original.grid);
        return copies.emplace(&original,std::move(copy)).first->second;
    }
};

inline bool native_menu_surface_image(const std::string& name) {
    return name.find("buttons/button_")!=std::string::npos ||
        name.find("buttons/button-physical-")!=std::string::npos ||
        (name.find("buttons/modern/")!=std::string::npos && name.find("background")!=std::string::npos) ||
        name.find("dialogs/selection-")!=std::string::npos ||
        name.find("dialogs/background")!=std::string::npos ||
        name.find("dialogs/translucent")!=std::string::npos;
}
inline void native_menu_image_colour(config& shape,bool disabled) {
    const auto name=shape["name"].str();
    // Celes assumes a 20px checkbox and leaves its tick at y=0. Centre that
    // known image with its box when the scoped touch target is made taller.
    if(name.compare(0,20,"buttons/modern/check")==0)
        shape["y"]="((height - image_height) / 2)";
    // Keep positioner/checkbox state artwork and all other native formulas.
    // Only known chrome is tinted: unit portraits, minimaps and colour-coded
    // faction/side icons must retain their actual colours.
    if(name.compare(0,19,"buttons/scrollbars/")==0 ||
        name.compare(0,16,"buttons/sliders/")==0 ||
        name.compare(0,16,"buttons/checkbox")==0 ||
        name.compare(0,20,"buttons/modern/check")==0)
        shape["name"]=name+(disabled?"~GS()~CS(12,6,-6)":"~GS()~CS(55,30,-15)");
    else if(name.compare(0,25,"icons/arrows/short_arrow_")==0)
        shape["name"]=name+(disabled?"~BLEND(179,169,151,1)":"~BLEND(214,180,110,1)");
}
inline void native_menu_colours(config& canvas,bool disabled) {
    for(auto child:canvas.all_children_range()) {
        auto& shape=child.cfg;
        if(child.key=="text") {
            shape["color"]=disabled?"179,169,151,255":product_ui::ivory;
            shape["font_size"]=std::max(18u,shape["font_size"].to_unsigned(18));
        }
        else if(child.key=="line")shape["color"]=shape["color"].str().find("cursor_alpha")!=std::string::npos?
            "([214,180,110,cursor_alpha])":product_ui::gold;
        else if(child.key=="rectangle") {
            if(shape.has_attribute("fill_color"))shape["fill_color"]=
                shape["w"].str().find("selection_width")!=std::string::npos?"91,116,110,255":product_ui::teal;
            if(shape.has_attribute("border_color"))shape["border_color"]=product_ui::gold;
        }
        else if(child.key=="image")native_menu_image_colour(shape,disabled);
        native_menu_colours(shape,disabled);
    }
}
inline config native_menu_action_canvas(const config& original,std::size_t state,bool toggle) {
    config draw;
    const bool disabled=toggle?(state%3==1):(state==1);
    const bool selected=toggle?(state>=3):(state==2);
    const bool focus=toggle?(state%3==2):(state==3);
    product_ui::rectangle(draw,selected?"111,87,49,255":disabled?"58,54,46,255":product_ui::stone,
        focus||selected?product_ui::gold:"140,118,77,255");
    // Retain text, arrow/icon identity and geometry. Only the native blue
    // frame/background shapes are replaced; no callback/value is changed.
    for(const auto child:original.all_children_range()) {
        if(child.key=="text" || (child.key=="image" && !native_menu_surface_image(child.cfg["name"].str()))) {
            auto copy=child.cfg; native_menu_colours(copy,disabled);
            if(child.key=="image")native_menu_image_colour(copy,disabled);
            if(child.key=="text") {
                copy["color"]=disabled?"179,169,151,255":product_ui::ivory;
                copy["font_size"]=std::max(18u,copy["font_size"].to_unsigned(18));
            }
            draw.add_child(child.key,copy);
        }
    }
    return draw;
}
template<class Definition>
inline gui2::resolution_definition_ptr clone_menu_resolution(const gui2::resolution_definition_ptr& original) {
    return std::make_shared<typename Definition::resolution>(
        *std::static_pointer_cast<typename Definition::resolution>(original));
}
inline gui2::resolution_definition_ptr menu_control_resolution(const std::string& type,
        const std::string& definition,const gui2::resolution_definition_ptr& original) {
    if(menu_theme_depth()==0 || !original)return original;
    // Product styles already use this palette and have authored artwork.
    if(definition.compare(0,9,"alliance_")==0 && definition.compare(0,15,"alliance_touch_")!=0)return original;
    using Key=std::tuple<std::string,std::string,gui2::resolution_definition_ptr>;
    static std::map<Key,gui2::resolution_definition_ptr> copies;
    const Key key{type,definition,original};
    const auto found=copies.find(key);if(found!=copies.end())return found->second;
    gui2::resolution_definition_ptr copy;
#define ALLIANCE_MENU_CLONE(Name) if(type==#Name)copy=clone_menu_resolution<gui2::Name##_definition>(original)
    ALLIANCE_MENU_CLONE(button); else ALLIANCE_MENU_CLONE(menu_button);
    else ALLIANCE_MENU_CLONE(multimenu_button); else ALLIANCE_MENU_CLONE(repeating_button);
    else ALLIANCE_MENU_CLONE(toggle_button); else ALLIANCE_MENU_CLONE(toggle_panel);
    else ALLIANCE_MENU_CLONE(text_box); else ALLIANCE_MENU_CLONE(combobox);
    else ALLIANCE_MENU_CLONE(multiline_text); else ALLIANCE_MENU_CLONE(label);
    else ALLIANCE_MENU_CLONE(rich_label); else ALLIANCE_MENU_CLONE(panel);
    else ALLIANCE_MENU_CLONE(window); else ALLIANCE_MENU_CLONE(slider);
    else ALLIANCE_MENU_CLONE(progress_bar); else ALLIANCE_MENU_CLONE(horizontal_scrollbar);
    else ALLIANCE_MENU_CLONE(vertical_scrollbar); else ALLIANCE_MENU_CLONE(listbox);
    else if(type=="horizontal_listbox"||type=="grid_listbox")copy=clone_menu_resolution<gui2::listbox_definition>(original);
    else ALLIANCE_MENU_CLONE(scroll_label); else ALLIANCE_MENU_CLONE(scroll_text);
    else ALLIANCE_MENU_CLONE(scrollbar_panel); else ALLIANCE_MENU_CLONE(chatbox);
    else ALLIANCE_MENU_CLONE(tab_container); else ALLIANCE_MENU_CLONE(tree_view);
    else ALLIANCE_MENU_CLONE(spinner); else ALLIANCE_MENU_CLONE(unit_preview_pane);
#undef ALLIANCE_MENU_CLONE
    if(!copy)return original; // Minimap, portraits, drawings and gameplay data remain native.
    const bool touch=type=="button" || type=="menu_button" || type=="multimenu_button" ||
        type=="repeating_button" || type=="toggle_button" || type=="toggle_panel" ||
        type=="text_box" || type=="combobox" || type=="multiline_text" || type=="slider";
    if(touch) {
        copy->min_width=std::max(48u,copy->min_width);
        copy->default_width=std::max(48u,copy->default_width);
        if(copy->max_width)copy->max_width=std::max(48u,copy->max_width);
        copy->min_height=std::max(48u,copy->min_height);
        copy->default_height=std::max(48u,copy->default_height);
        if(copy->max_height)copy->max_height=std::max(48u,copy->max_height);
    }
    copy->text_font_size=gui2::typed_formula<unsigned>(copy->text_font_size.has_formula()?18u:
        std::max(18u,copy->text_font_size()));
    if(type=="tab_container") {
        auto tabs=std::static_pointer_cast<gui2::tab_container_definition::resolution>(copy);
        tabs->grid=menu_tab_grid(tabs->grid,definition=="horizontal" || definition=="horizontal_no_image");
    }
    if(type=="chatbox") {
        auto chat=std::static_pointer_cast<gui2::chatbox_definition::resolution>(copy);
        chat->grid=native_touch_grid(chat->grid);
    }
    if(type=="rich_label") {
        auto label=std::static_pointer_cast<gui2::rich_label_definition::resolution>(copy);
        label->font_size=std::max(18,label->font_size);
        label->text_color_enabled=color_t::from_argb_bytes(0xff000000u|product_theme::ivory_rgb);
        label->text_color_disabled=color_t{179,169,151,255};
        label->link_color=color_t::from_argb_bytes(0xff000000u|product_theme::gold_rgb);
    }
    if(type=="label")std::static_pointer_cast<gui2::label_definition::resolution>(copy)->link_color=
        color_t::from_argb_bytes(0xff000000u|product_theme::gold_rgb);
    for(std::size_t index=0;index<copy->state.size();++index) {
        auto& canvas=copy->state[index].canvas_cfg_;
        const bool disabled=(type=="toggle_button"||type=="toggle_panel")?index%3==1:index==1;
        if(type=="window") {
            canvas.clear();
            if(index==0)product_ui::background_canvas(canvas,true);
            product_ui::rectangle(canvas,index==0?"47,43,34,216":"0,0,0,0",product_ui::gold);
        } else if(type=="button"||type=="menu_button"||type=="multimenu_button"||type=="repeating_button") {
            canvas=native_menu_action_canvas(canvas,index,false);
        } else if(type=="toggle_panel") {
            canvas.clear();
            product_ui::rectangle(canvas,disabled?"58,54,46,150":index>=3?"111,87,49,235":"47,43,34,150",
                index>=3?product_ui::gold:"140,118,77,110");
        } else if(type=="panel") {
            canvas.clear();
            if(index==0)product_ui::rectangle(canvas,"47,43,34,190","140,118,77,120");
        } else native_menu_colours(canvas,disabled);
    }
    copies.emplace(key,copy);return copy;
}
} // namespace alliance_game
