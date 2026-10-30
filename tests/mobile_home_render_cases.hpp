// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "tools/android-presentation/mobile_home.hpp"
#include "gui/core/event/distributor.hpp"
#include <stdexcept>

// The actual renderer exposes its native tooltip window without replacing its
// behavior, so focus/hold assertions inspect what is really displayed.
namespace gui2::dialogs::tip { gui2::window* citadel_current_tooltip(); }

namespace mobile_home_checks {
inline void require(bool value,const char* message){if(!value)throw std::runtime_error(message);}

// Call after real GUI2 layout, for both default and Celes at each resolution.
inline void geometry(gui2::window& window,const std::string& username,std::size_t count) {
    namespace home=alliance_game::mobile_home;
    const auto screen=window.get_rectangle();int previous_right=-1;
    for(std::size_t i=0;i<home::actions.size();++i){const auto& action=home::actions[i];auto& button=window.find_widget<gui2::button>(action.id);const auto box=button.get_rectangle();
        require(box.w==home::px(action.width)&&box.h==home::px(action.height),"Home hitbox differs from its authored plaque or utility dimensions");
        require(box.x>=screen.x&&box.y>=screen.y&&box.x+box.w<=screen.x+screen.w&&box.y+box.h<=screen.y+screen.h,"Home action leaves the screen");
        require(button.tooltip()==action.name,"Home action lacks its accessible tooltip");
        if(i>=5){require(box.y+box.h==screen.y+screen.h-home::px(24),"Home plaque baseline moved");
            if(previous_right>=0)require(box.x-previous_right==home::px(12),"Home navigation gap moved");previous_right=box.x+box.w;
            require(button.get_label()==action.name,"Home plaque lost its product action label");}
    }
    const auto profile=window.find_widget<gui2::widget>("home_profile").get_rectangle();
    require(profile.w==home::px(200)&&profile.h==home::px(60),"Home profile dimensions changed");
    require(profile.x+profile.w==screen.x+screen.w-home::px(24),"Home profile right inset changed");
    require(window.find_widget<gui2::label>("home_player").get_label()==username,"Home profile name is stale");
    require(window.find_widget<gui2::label>("home_player").tooltip()==username,"Long profile name loses full tooltip");
    require(window.find_widget<gui2::label>("home_heroes").get_label()=="Heroes: "+std::to_string(count),"Home hero count is stale");
    const auto title=window.find_widget<gui2::label>("home_title").get_rectangle();
    require(title.y>=profile.y+profile.h&&title.x>=screen.x&&title.x+title.w<=screen.x+screen.w,"Home title overlaps upper controls");
}

// Attach to a real home window without closing destination callbacks. These
// are native GUI2 events, so they test the production interception/order as
// well as the pure gesture state. The fixture continues to pump SDL timers.
class InputChecks {
    gui2::window& window_;std::shared_ptr<std::array<int,10>> calls_=std::make_shared<std::array<int,10>>();
    gui2::button& button(std::size_t i){return window_.find_widget<gui2::button>(alliance_game::mobile_home::actions[i].id);}
    void mouse(std::size_t i,gui2::event::ui_event event){auto& b=button(i);
        // Native button DOWN captures the mouse and therefore requires the
        // same distributor focus a real SDL pointer motion would establish.
        if(event==gui2::event::LEFT_BUTTON_DOWN){const auto box=b.get_rectangle();window_.fire(gui2::event::SDL_MOUSE_MOTION,window_,point{box.x+box.w/2,box.y+box.h/2});}
        b.fire(event,b);
        if(event==gui2::event::LEFT_BUTTON_UP)window_.mouse_capture(false);
    }
    void key(std::size_t i,SDL_Keycode key){auto& b=button(i);b.fire(gui2::event::SDL_KEY_DOWN,b,key,SDL_KMOD_NONE,"");}
public:
    explicit InputChecks(gui2::window& window):window_(window){
        for(std::size_t i=0;i<calls_->size();++i){
            // The native builder assigns Quit its standard CANCEL retval by
            // ID. This counter-only fixture must stay open through ordinary
            // activations; the long-press case restores the native retval.
            button(i).set_retval(gui2::retval::NONE);
            gui2::event::connect_signal_mouse_left_click(button(i),[counts=calls_,i](auto&&...){++(*counts)[i];});
        }
    }
    void short_checks(){
        for(std::size_t i=0;i<calls_->size();++i){
            mouse(i,gui2::event::LEFT_BUTTON_DOWN);mouse(i,gui2::event::LEFT_BUTTON_UP);mouse(i,gui2::event::LEFT_BUTTON_CLICK);
            require((*calls_)[i]==1,"Short home press did not activate exactly once");
            window_.keyboard_capture(&button(i));key(i,SDLK_RETURN);require((*calls_)[i]==2,"Home Enter activation failed");
            key(i,SDLK_SPACE);require((*calls_)[i]==3,"Home Space activation failed");
            mouse(i,gui2::event::LEFT_BUTTON_DOWN);mouse(i,gui2::event::MOUSE_LEAVE);mouse(i,gui2::event::LEFT_BUTTON_UP);mouse(i,gui2::event::LEFT_BUTTON_CLICK);
            require((*calls_)[i]==3,"Cancelled home drag activated a destination");
            button(i).set_active(false);mouse(i,gui2::event::LEFT_BUTTON_CLICK);key(i,SDLK_RETURN);
            require((*calls_)[i]==3,"Disabled home action activated");button(i).set_active(true);
        }
        // Arrow navigation routes subsequent keyboard activation to the target.
        window_.keyboard_capture(&button(7));key(7,SDLK_LEFT);
        require(window_.get_distributor().keyboard_focus()==&button(6),"Left arrow did not focus Room");
        key(6,SDLK_RETURN);
        require((*calls_)[6]==4,"Home directional navigation lost activation");
        window_.fire(gui2::event::SDL_KEY_DOWN,window_,SDLK_TAB,SDL_KMOD_NONE,"");
        require(window_.get_distributor().keyboard_focus()==&button(7),"Tab did not focus Campaign");
        require(!gui2::dialogs::tip::citadel_current_tooltip(),"Labeled footer focus opened a redundant tooltip");
        window_.keyboard_capture(&button(0));
        auto* tip=gui2::dialogs::tip::citadel_current_tooltip();
        require(tip&&tip->find_widget<gui2::label>("label").get_label()=="Quit","Utility focus lost its action-name tooltip");
        window_.keyboard_capture(&button(7));
        require(!gui2::dialogs::tip::citadel_current_tooltip(),"Returning focus to a labeled footer left the utility tooltip visible");
    }
    void begin_long_press(){
        auto& quit=button(0);quit.set_retval(gui2::retval::CANCEL);
        // Move SDL's real pointer as well as distributor focus; otherwise a
        // native MOUSE_LEAVE can cancel the timer after synthetic DOWN.
        gui2::dialogs::tip::remove();quit.set_wants_mouse_hover(false);
        const auto box=quit.get_rectangle();
        SDL_WarpMouseInWindow(video::get_window(),box.x+box.w/2,box.y+box.h/2);
        mouse(0,gui2::event::LEFT_BUTTON_DOWN);
    }
    // Call after >=600ms of the real modal event loop, without blocking it.
    void finish_long_press(){
        const int before=(*calls_)[0];mouse(0,gui2::event::LEFT_BUTTON_UP);mouse(0,gui2::event::LEFT_BUTTON_CLICK);
        require((*calls_)[0]==before,"Long-press tooltip activated Quit on release");
        require(window_.get_retval()==gui2::retval::NONE,"Long-press tooltip invoked the native Quit close action");
        button(0).set_retval(gui2::retval::NONE);
        mouse(0,gui2::event::LEFT_BUTTON_DOWN);mouse(0,gui2::event::LEFT_BUTTON_UP);mouse(0,gui2::event::LEFT_BUTTON_CLICK);
        require((*calls_)[0]==before+1,"A long press disabled the next ordinary press");
    }
};
}
