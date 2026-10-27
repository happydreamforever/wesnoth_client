// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "gui/core/gui_definition.hpp"
#include "gui/widgets/button.hpp"
#include "gui/widgets/chatbox.hpp"
#include "gui/widgets/listbox.hpp"
#include "gui/widgets/label.hpp"
#include "gui/widgets/menu_button.hpp"
#include "gui/widgets/multimenu_button.hpp"
#include "gui/widgets/panel.hpp"
#include "gui/widgets/scrollbar_panel.hpp"
#include "gui/widgets/stacked_widget.hpp"
#include "gui/widgets/text_box.hpp"
#include "gui/widgets/toggle_button.hpp"
#include "gui/widgets/toggle_panel.hpp"
#include "gui/widgets/tree_view.hpp"
#include <algorithm>
#include <stdexcept>
namespace alliance_game {
inline gui2::builder_grid_ptr native_touch_grid(const gui2::builder_grid_ptr& native);
inline gui2::styled_widget_definition_ptr native_control_definition(const std::string& type,const std::string& name) {
    // Match native lookup order: named current, named default, then each
    // theme's default definition. Celes deliberately inherits most controls.
    for(const auto& definition:{name,std::string("default")})
        for(const auto theme:{gui2::current_gui,gui2::default_gui}) {
            const auto types=theme->second.widget_types.find(type);
            if(types==theme->second.widget_types.end())continue;
            const auto found=types->second.find(definition);
            if(found!=types->second.end())return found->second;
        }
    throw std::runtime_error("Missing native touch control: "+type);
}
// Clone native builders and their primary-control resolutions. The
// immutable data pack, native option values and callbacks are preserved.
// Leaf styles are isolated; no shared default definition is replaced.
template<class Builder,class Definition>
inline bool native_touch_control(gui2::builder_widget_ptr& item,const std::string& type) {
    const auto original=std::dynamic_pointer_cast<Builder>(item);if(!original)return false;
    auto replacement=std::make_shared<Builder>(*original);
    auto& styles=gui2::current_gui->second.widget_types[type];const auto id="alliance_touch_"+type+"_"+original->definition;
    if(!styles.count(id)){
        const auto native=native_control_definition(type,original->definition);auto copied=std::make_shared<gui2::styled_widget_definition>(*native);copied->id=id;copied->resolutions.clear();
        for(const auto& resolution:native->resolutions){auto size=std::make_shared<typename Definition::resolution>(*std::static_pointer_cast<typename Definition::resolution>(resolution));
            const auto font_size=size->text_font_size.has_formula()?18u:std::max(18u,size->text_font_size());
            size->text_font_size=gui2::typed_formula<unsigned>(font_size);
            for(auto& state:size->state)for(auto& text:state.canvas_cfg_.child_range("text"))text["font_size"]=std::max(font_size,text["font_size"].to_unsigned(font_size));
            size->min_height=std::max(48u,size->min_height);size->default_height=std::max(48u,size->default_height);if(size->max_height)size->max_height=std::max(48u,size->max_height);
            size->min_width=std::max(48u,size->min_width);size->default_width=std::max(48u,size->default_width);if(size->max_width)size->max_width=std::max(48u,size->max_width);copied->resolutions.push_back(std::move(size));}
        styles.emplace(id,std::move(copied));
    }
    replacement->definition=id;item=std::move(replacement);return true;
}
inline gui2::builder_grid_ptr native_touch_grid(const gui2::builder_grid_ptr& native) {
    if(!native)return {};
    auto grid=std::make_shared<gui2::builder_grid>(*native);
    for(auto& item:grid->widgets){
        if(const auto label=std::dynamic_pointer_cast<gui2::implementation::builder_label>(item);label&&label->label_string.base_str()=="Multiplayer Lobby"){auto copy=std::make_shared<gui2::implementation::builder_label>(*label);copy->label_string="Room";item=std::move(copy);continue;}
        if(const auto nested=std::dynamic_pointer_cast<gui2::builder_grid>(item)){item=native_touch_grid(nested);continue;}
        if(const auto panel=std::dynamic_pointer_cast<gui2::implementation::builder_panel>(item)){auto copy=std::make_shared<gui2::implementation::builder_panel>(*panel);copy->grid=native_touch_grid(panel->grid);item=std::move(copy);continue;}
        if(const auto panel=std::dynamic_pointer_cast<gui2::implementation::builder_toggle_panel>(item)){auto copy=std::make_shared<gui2::implementation::builder_toggle_panel>(*panel);copy->grid=native_touch_grid(panel->grid);item=std::move(copy);continue;}
        if(const auto tree=std::dynamic_pointer_cast<gui2::implementation::builder_tree_view>(item)){auto copy=std::make_shared<gui2::implementation::builder_tree_view>(*tree);for(auto& node:copy->nodes)node.builder=native_touch_grid(node.builder);item=std::move(copy);continue;}
        if(const auto list=std::dynamic_pointer_cast<gui2::implementation::builder_listbox_base>(item)){auto copy=std::make_shared<gui2::implementation::builder_listbox_base>(*list);copy->list_builder=native_touch_grid(list->list_builder);copy->header=native_touch_grid(list->header);copy->footer=native_touch_grid(list->footer);item=std::move(copy);continue;}
        if(const auto panel=std::dynamic_pointer_cast<gui2::implementation::builder_scrollbar_panel>(item)){auto copy=std::make_shared<gui2::implementation::builder_scrollbar_panel>(*panel);copy->grid_=native_touch_grid(panel->grid_);item=std::move(copy);continue;}
        if(const auto stack=std::dynamic_pointer_cast<gui2::implementation::builder_stacked_widget>(item)){auto copy=std::make_shared<gui2::implementation::builder_stacked_widget>(*stack);for(auto& layer:copy->stack)layer=*native_touch_grid(std::make_shared<gui2::builder_grid>(layer));item=std::move(copy);continue;}
        if(const auto chat=std::dynamic_pointer_cast<gui2::implementation::builder_chatbox>(item)){
            auto copy=std::make_shared<gui2::implementation::builder_chatbox>(*chat);const auto id="alliance_touch_chatbox_"+chat->definition;auto& styles=gui2::current_gui->second.widget_types["chatbox"];
            if(!styles.count(id)){const auto original=native_control_definition("chatbox",chat->definition);auto definition=std::make_shared<gui2::styled_widget_definition>(*original);definition->id=id;definition->resolutions.clear();
                for(const auto& resolution:original->resolutions){auto size=std::make_shared<gui2::chatbox_definition::resolution>(*std::static_pointer_cast<gui2::chatbox_definition::resolution>(resolution));size->grid=native_touch_grid(size->grid);definition->resolutions.push_back(std::move(size));}styles.emplace(id,std::move(definition));}
            copy->definition=id;item=std::move(copy);continue;
        }
        if(native_touch_control<gui2::implementation::builder_button,gui2::button_definition>(item,"button"))continue;
        if(native_touch_control<gui2::implementation::builder_menu_button,gui2::menu_button_definition>(item,"menu_button"))continue;
        if(native_touch_control<gui2::implementation::builder_multimenu_button,gui2::multimenu_button_definition>(item,"multimenu_button"))continue;
        if(native_touch_control<gui2::implementation::builder_toggle_button,gui2::toggle_button_definition>(item,"toggle_button"))continue;
        native_touch_control<gui2::implementation::builder_text_box,gui2::text_box_definition>(item,"text_box");
    }
    return grid;
}
inline std::string prepare_native_touch_window(const std::string& source) {
    // The modal theme context styles even private size_lock/chatbox children
    // during construction, without changing a theme's default chatbox.
    auto& windows=gui2::current_gui->second.window_types;const std::string id="alliance_touch_"+source;
    if(!windows.count(id)){
        const auto found=windows.find(source);
        auto copy=found!=windows.end()?found->second:gui2::default_gui->second.window_types.at(source);
        for(auto& resolution:copy.resolutions)resolution.grid=native_touch_grid(resolution.grid);
        windows.emplace(id,std::move(copy));
    }
    return id;
}
}
