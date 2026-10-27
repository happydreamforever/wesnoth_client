// Actual GUI registry checks for the scoped menu resolver (no mocked styles).
#pragma once
#include "alliance_game/native_menu_theme.hpp"
#include <stdexcept>

inline void verify_emerald_auth_geometry(gui2::window& window) {
    const auto bounds=window.get_rectangle();
    for(const auto* id:{"auth_crest","auth_title","auth_heading_separator","login_label",
            "username_label","user_name","password_label","password","remember_password",
            "confirmation_label","confirmation","ok","auth_action_separator","signup","cancel"}) {
        const auto* widget=window.find(id,false);
        if(!widget)throw std::runtime_error("Authentication presentation lost a control");
        if(widget->get_visible()!=gui2::widget::visibility::visible)continue;
        const auto box=widget->get_rectangle();
        if(box.x<bounds.x || box.y<bounds.y || box.x+box.w>bounds.x+bounds.w || box.y+box.h>bounds.y+bounds.h)
            throw std::runtime_error("Authentication presentation exceeds its panel");
    }
    const auto title=window.find_widget<gui2::label>("auth_title").get_rectangle();
    const auto user=window.find_widget<gui2::text_box>("user_name").get_rectangle();
    const auto password=window.find_widget<gui2::text_box>("password").get_rectangle();
    const auto eye=window.find_widget<gui2::toggle_button>("remember_password").get_rectangle();
    const auto primary=window.find_widget<gui2::button>("ok").get_rectangle();
    const auto& alternate=window.find_widget<gui2::button>("signup");
    const auto secondary=alternate.get_rectangle();
    if(title.h<42 || title.y+title.h>user.y || user.y+user.h>password.y ||
        eye.w<48 || eye.h<48 || eye.y!=password.y || eye.h!=password.h || password.x+password.w>eye.x ||
        primary.w<400 || primary.h<52 || primary.y<password.y+password.h ||
        (alternate.get_visible()==gui2::widget::visibility::visible && (secondary.h<52 || primary.w!=secondary.w || primary.y+primary.h>secondary.y)))
        throw std::runtime_error("Authentication heading, fields or broad actions overlap or are undersized");
    const auto& confirmation=window.find_widget<gui2::text_box>("confirmation");
    if(confirmation.get_visible()==gui2::widget::visibility::visible &&
        (confirmation.get_rectangle().y<password.y+password.h || confirmation.get_rectangle().y+confirmation.get_height()>primary.y))
        throw std::runtime_error("Signup confirmation overlaps another field or action");
}

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

inline void verify_native_preferences_geometry(gui2::window& window,bool check_tabs_at_top=true) {
    const auto bounds=window.get_rectangle();
    const auto close=window.find_widget<gui2::button>("ok").get_rectangle();
    const auto title=window.find_widget<gui2::label>("title").get_rectangle();
    const auto pages=window.find_widget<gui2::scrollbar_panel>("alliance_preferences_scroll").get_rectangle();
    for(const auto& control:{title,pages,close}) {
        if(control.w<=0 || control.h<=0 || control.x<bounds.x || control.y<bounds.y ||
            control.x+control.w>bounds.x+bounds.w || control.y+control.h>bounds.y+bounds.h)
            throw std::runtime_error("Preferences title, pages or Close exceeds its viewport");
    }
    if(check_tabs_at_top) {
        auto& navigation=window.find_widget<gui2::tab_container>("pager").get_grid().find_widget<gui2::listbox>("_tab_list");
        if(navigation.get_item_count()!=6)throw std::runtime_error("Preferences navigation lost a tab");
        for(unsigned index=0;index<6;++index) {
            const auto tab=navigation.get_row_grid(index)->get_rectangle();
            if(tab.w<48 || tab.h<48 || tab.x<pages.x || tab.y<pages.y ||
                tab.x+tab.w>pages.x+pages.w || tab.y+tab.h>pages.y+pages.h)
                throw std::runtime_error("Preferences tab is undersized or outside its initial viewport");
        }
    }
    auto& multiplayer=window.find_widget<gui2::tab_container>("multiplayer_pager");
    const auto strip=multiplayer.find_widget<gui2::listbox>("_tab_list").get_rectangle();
    if(window.find_widget<gui2::tab_container>("pager").get_active_tab_index()==4 && strip.h<48)
        throw std::runtime_error("Preferences nested tabs are clipped below touch height");
    const auto tab=window.find_widget<gui2::tab_container>("pager").get_active_tab_index();
    if(tab==5) {
        auto& advanced=window.find_widget<gui2::listbox>("advanced_prefs");
        if(advanced.get_item_count()==0 || advanced.get_row_grid(0)->find_widget<gui2::toggle_button>("value_toggle").get_height()<48)
            throw std::runtime_error("Preferences constructor-built Advanced target is undersized");
    }
    if(tab==1) {
        auto& hotkeys=window.find_widget<gui2::listbox>("list_hotkeys");
        if(hotkeys.get_item_count()==0 || hotkeys.get_row_grid(0)->get_height()<48)
            throw std::runtime_error("Preferences constructor-built Hotkey target is undersized");
    }
    if(close.w<48 || close.h<48 || pages.y<title.y+title.h || pages.y+pages.h>close.y)
        throw std::runtime_error("Preferences scroll content covers its fixed title or Close");
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
        const auto title=gui2::get_control("label","title");
        const auto title_family=title->text_font_family;const config title_canvas=title->state.front().canvas_cfg_;
        const auto chat=gui2::get_control("chatbox","default");
        const auto tabs=gui2::get_control("tab_container","horizontal_no_image");
        const auto tabs_grid=std::static_pointer_cast<gui2::tab_container_definition::resolution>(tabs)->grid;
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
        const auto& original_preferences=gui2::get_window_builder("preferences_dialog");
        const auto original_preferences_grid=original_preferences.grid;
        {
            MenuThemeScope preferences("preferences_dialog");
            const auto& scoped=preferences.builder("preferences_dialog");
            require(&scoped!=&original_preferences && scoped.grid!=original_preferences_grid,
                "Preferences scrolling reused the shared native builder");
        }
        require(gui2::get_window_builder("preferences_dialog").grid==original_preferences_grid,
            "Preferences scrolling changed the shared native builder");
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
            const auto menu_title=gui2::get_control("label","title");
            require(menu_title!=title && menu_title->text_font_family!=font::family_class::script,
                "Native menu title retained the old script family");
            for(const auto& state:menu_title->state)for(const auto& text:state.canvas_cfg_.child_range("text"))
                require(text["font_family"].str()!="script","Native title canvas retained the old script family");
            require(gui2::get_control("chatbox","default")!=chat,"Private native chatbox was not cloned");
            const auto scoped_tabs=gui2::get_control("tab_container","horizontal_no_image");
            require(scoped_tabs!=tabs && std::static_pointer_cast<gui2::tab_container_definition::resolution>(scoped_tabs)->grid!=tabs_grid,
                "Horizontal menu tabs reused their shared native grid");
            for(const auto* definition:{"no_label","radio_no_label","radio"}) {
                const auto checkbox=gui2::get_control("toggle_button",definition);
                for(const auto& state:checkbox->state)for(const auto& image:state.canvas_cfg_.child_range("image"))
                    if(image["name"].str().compare(0,20,"buttons/modern/check")==0 ||
                        image["name"].str().compare(0,16,"buttons/checkbox")==0 || image["name"].str().compare(0,16,"buttons/radiobox")==0)
                        require(image["y"].str()=="((height - image_height) / 2)",
                            "Menu checkbox/radio is not centred within its touch-sized target");
                if(std::string(definition)=="radio")for(const auto& state:checkbox->state)
                    for(const auto& circle:state.canvas_cfg_.child_range("circle"))
                        require(circle["y"].str()=="(height / 2)" && circle["fill_color"].str()!="28, 45, 64, 255",
                            "Celes radio retained its small blue native glyph");
            }
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
        require(gui2::get_control("tab_container","horizontal_no_image")==tabs &&
            std::static_pointer_cast<gui2::tab_container_definition::resolution>(tabs)->grid==tabs_grid,
            "Menu tabs changed their shared native grid");
        require(gui2::get_control("label","title")==title && title->text_font_family==title_family && title->state.front().canvas_cfg_==title_canvas,
            "Menu typography changed a shared native title");
        require(button->min_width==original_width && button->min_height==original_height,
            "Menu touch sizing changed the original gameplay geometry");
        if(previous_native && previous_native!=button)require(previous_themed!=themed,
            "Switching to Celes reused another theme's resolution clone");
        previous_native=button;previous_themed=themed;
    }
    try {MenuThemeScope menu("alliance_exception");throw 1;}catch(int){}
    require(menu_theme_depth()==0,"Exception unwinding leaked a menu context");
}
