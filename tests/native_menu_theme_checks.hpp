// Actual GUI registry checks for the scoped menu resolver (no mocked styles).
#pragma once
#include "alliance_game/native_menu_theme.hpp"
#include <stdexcept>

inline void verify_native_load_geometry(gui2::window& window) {
    const auto list=window.find_widget<gui2::listbox>("savegame_list").get_rectangle();
    const auto name=window.find_widget<gui2::toggle_button>("sort_0").get_rectangle();
    const auto date=window.find_widget<gui2::toggle_button>("sort_1").get_rectangle();
    for(const auto& heading:{name,date}) {
        if(heading.w<48 || heading.h<48 || heading.x<list.x || heading.y<list.y ||
            heading.x+heading.w>list.x+list.w || heading.y+heading.h>list.y+list.h)
            throw std::runtime_error("Load sorting heading is undersized or clipped");
    }
    if(name.x+name.w>date.x)throw std::runtime_error("Load sorting headings overlap");
}

inline void verify_native_menu_theme_boundaries() {
    using namespace alliance_game;
    auto require=[](bool value,const char* message){if(!value)throw std::runtime_error(message);};
    require(menu_theme_depth()==0,"Menu context was already active before boundary checks");
    const auto initial=gui2::current_gui;
    struct Restore {gui2::gui_theme_map_t::iterator previous;~Restore(){gui2::current_gui=previous;previous->second.activate();}} restore{initial};
    gui2::resolution_definition_ptr previous_native,previous_themed;
    for(const auto* name:{"default","modern"}) {
        const auto theme=gui2::guis.find(name);
        require(theme!=gui2::guis.end(),"A required native theme is missing");
        gui2::current_gui=theme;theme->second.activate();
        const auto button=gui2::get_control("button","default");
        const auto label=gui2::get_control("label","default");
        const auto chat=gui2::get_control("chatbox","default");
        const config button_state=button->state.front().canvas_cfg_;
        const config label_state=label->state.front().canvas_cfg_;
        const auto original_width=button->min_width,original_height=button->min_height;
        const auto& original_load=gui2::get_window_builder("game_load");
        const auto original_load_grid=original_load.grid;
        {
            MenuThemeScope load("game_load");
            const auto& scoped=load.builder("game_load");
            require(&scoped!=&original_load && scoped.grid!=original_load_grid,
                "Load sizing reused the shared native builder");
            require(&scoped==&load.builder("game_load"),"Load builder clone is unstable");
        }
        require(gui2::get_window_builder("game_load").grid==original_load_grid,
            "Load sizing changed the shared native builder");
        const auto default_chat_definition=gui2::default_gui->second.widget_types.at("chatbox").at("default");
        prepare_native_touch_window("campaign_selection");
        require(gui2::default_gui->second.widget_types.at("chatbox").at("default")==default_chat_definition,
            "Preparing a menu replaced the shared default chatbox");
        {
            MenuThemeScope gameplay("unit_attack");
            require(menu_theme_depth()==0 && gui2::get_control("button","default")==button,
                "An unscoped gameplay dialog acquired menu styles");
        }
        gui2::resolution_definition_ptr themed;
        {
            MenuThemeScope menu("alliance_fixture");
            require(menu_theme_depth()==1,"Root menu context did not activate");
            themed=gui2::get_control("button","default");
            require(themed!=button && themed==gui2::get_control("button","default"),
                "Native button clone is absent or unstable");
            require(themed->min_height>=48 && themed->min_width>=48 &&
                themed->default_height>=48 && (!themed->max_height || themed->max_height>=48),
                "The scoped menu action is smaller than its touch target");
            require(themed->state.front().canvas_cfg_!=button_state,"Menu action retained its original surface");
            require(gui2::get_control("label","default")!=label,"Native menu label was not cloned");
            require(gui2::get_control("chatbox","default")!=chat,"Private native chatbox was not cloned");
            {
                MenuThemeScope popup("prompt");
                require(menu_theme_depth()==2 && gui2::get_control("button","default")==themed,
                    "Nested confirmation lost its owning menu theme");
            }
            require(menu_theme_depth()==1,"Nested confirmation did not release its context");
        }
        require(menu_theme_depth()==0 && gui2::current_gui==theme,"Menu scope changed the selected GUI");
        require(gui2::get_control("button","default")==button && gui2::get_control("label","default")==label &&
            gui2::get_control("chatbox","default")==chat,"Original native controls were not restored after menu exit");
        require(button->state.front().canvas_cfg_==button_state && label->state.front().canvas_cfg_==label_state,
            "Menu styling mutated a shared native canvas");
        require(button->min_width==original_width && button->min_height==original_height,
            "Menu touch sizing changed the original gameplay geometry");
        if(previous_native && previous_native!=button)require(previous_themed!=themed,
            "Switching to Celes reused another theme's resolution clone");
        previous_native=button;previous_themed=themed;
    }
    try {MenuThemeScope menu("alliance_exception");throw 1;}catch(int){}
    require(menu_theme_depth()==0,"Exception unwinding leaked a menu context");
}
