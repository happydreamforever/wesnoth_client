"""Real GUI2 Citadel rendering fixture; no production account/service writes.

Extends the retained product fixture with home edge cases and native menus.
The window pump/capture hook exists only in generated audit sources.
"""
from pathlib import Path
import argparse
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
options = parser.parse_args()
root = Path(__file__).resolve().parents[1]
out = options.output
subprocess.run([sys.executable, str(root / 'tests/generate_product_ui_render.py'),
                '--output', str(out)], check=True)
p = out / 'render_probe.cpp'
source = p.read_text()
source = '''#include "alliance_game/home_ui.hpp"
#include "alliance_game/native_menu_theme.hpp"
#include "tests/native_menu_theme_checks.hpp"
#include "tests/citadel_home_render_cases.hpp"
#include "SDL3/SDL_timer.h"
#include "gui/dialogs/game_load.hpp"
#include "gui/dialogs/preferences_dialog.hpp"
#include "gui/dialogs/achievements_dialog.hpp"
#include "gui/dialogs/message.hpp"
#include "gui/dialogs/drop_down_menu.hpp"
#include <set>
namespace gui2::dialogs::tip { gui2::window* citadel_current_tooltip(); }
''' + source
source = source.replace('"home_title","plan_title"',
                        '"home_title","home_player","home_heroes","home_profile","plan_title"')
source = source.replace('void arm(gui2::window& w){', 'std::unique_ptr<citadel_home_checks::InputChecks> input_checks;bool input_checks_finished=false,profile_hold_finished=false;Uint64 hold_started=0,profile_tip_seen=0,action_tip_seen=0;std::set<std::string> captured_phases;\nvoid arm(gui2::window& w){')
source = source.replace(' if(ticks<12)return;', '''
 if(ticks==3&&phase.find("home-input-")==0){input_checks->short_checks();input_checks->begin_long_press();hold_started=SDL_GetTicks();action_tip_seen=0;}
 if(phase.find("home-input-")==0&&ticks>=12){
   const auto elapsed=SDL_GetTicks()-hold_started;if(elapsed<650)return;
   auto* tip=gui2::dialogs::tip::citadel_current_tooltip();
   if(!tip){if(elapsed<2000)return;throw std::runtime_error("Quit tooltip was not visible after its hold");}
   if(tip->find_widget<gui2::label>("label").get_label()!="Quit")throw std::runtime_error("Quit hold displayed the wrong action name");
   const auto bounds=tip->get_rectangle(),quit=watched->find_widget<gui2::button>("quit").get_rectangle();
   if(bounds.w!=alliance_game::home_ui::px(260)||bounds.y<quit.y+quit.h||bounds.x<0||bounds.y<0||bounds.x+bounds.w>video::game_canvas_size().x||bounds.y+bounds.h>video::game_canvas_size().y)throw std::runtime_error("Quit hold tooltip is clipped or detached from its control");
   if(!action_tip_seen){action_tip_seen=SDL_GetTicks();return;}
   if(SDL_GetTicks()-action_tip_seen<100)return;
 }
 if(ticks==3&&phase.find("home-profile-")==0){
   auto& label=watched->find_widget<gui2::label>("home_player");const auto box=label.get_rectangle();const point at{box.x+box.w/2,box.y+box.h/2};
   gui2::dialogs::tip::remove();profile_tip_seen=0;label.set_wants_mouse_hover(false);
   SDL_WarpMouseInWindow(video::get_window(),at.x,at.y);watched->fire(gui2::event::SDL_MOUSE_MOTION,*watched,at);
 }
 if(ticks==6&&phase.find("home-profile-")==0){
   auto& label=watched->find_widget<gui2::label>("home_player");const auto box=label.get_rectangle();const point at{box.x+box.w/2,box.y+box.h/2};hold_started=SDL_GetTicks();
   if(phase.find("home-profile-hold-")==0)label.fire(gui2::event::LEFT_BUTTON_DOWN,label);
   else label.fire(gui2::event::SHOW_TOOLTIP,label,at);
 }
 if(phase.find("home-profile-")==0&&ticks>=12){
   const auto elapsed=SDL_GetTicks()-hold_started;
   if(phase.find("home-profile-hold-")==0&&elapsed<650)return;
   auto* tip=gui2::dialogs::tip::citadel_current_tooltip();
   if(!tip){if(elapsed<2000)return;throw std::runtime_error("Profile tooltip was not visible after its gesture");}
   if(tip->find_widget<gui2::label>("label").get_label()!="TwentyFourCharacterNameXY")throw std::runtime_error("Profile tooltip lost the complete username");
   const auto bounds=tip->get_rectangle(),profile=watched->find_widget<gui2::widget>("home_profile").get_rectangle();
   if(bounds.w<alliance_game::home_ui::px(520)||bounds.y<profile.y+profile.h||bounds.x<0||bounds.y<0||bounds.x+bounds.w>video::game_canvas_size().x||bounds.y+bounds.h>video::game_canvas_size().y)throw std::runtime_error("Profile tooltip is clipped or overlaps the profile");
   if(!profile_tip_seen){profile_tip_seen=SDL_GetTicks();return;}
   if(SDL_GetTicks()-profile_tip_seen<100)return;
   profile_hold_finished=true;
 }
 if(ticks==3&&phase.find("home-")==0&&phase.find("home-input-")!=0&&phase.find("home-profile-")!=0){
   auto& control=watched->find_widget<gui2::button>(click);
   watched->keyboard_capture(&control);
 }
 if(ticks<12)return;''')
source = source.replace('class Probe:public gui2::dialogs::modal_dialog {', '''
class CitadelHome:public gui2::dialogs::modal_dialog {
 std::string name_;std::size_t count_;std::string activated_;
 const std::string& window_id()const override {static const std::string id="alliance_home";return id;}
 void pre_show()override {
  set_click_dismiss(false);set_enter_disabled(true);set_escape_disabled(true);
  alliance_game::home_ui::set_profile(*this,name_,count_);
  alliance_game::home_ui::bind(*this);
  if(phase.find("home-input-")==0)input_checks=std::make_unique<citadel_home_checks::InputChecks>(*this);
  else for(const auto& action:alliance_game::home_ui::actions){
   auto& button=find_widget<gui2::button>(action.id);
   gui2::event::connect_signal_mouse_left_click(button,[this,id=std::string(action.id)](auto&&...){activated_=id;set_retval(gui2::retval::CANCEL);});
  }
  arm(*this);
 }
 public:CitadelHome(std::string name,std::size_t count):modal_dialog(alliance_game::prepare_home_window()),name_(std::move(name)),count_(count){}
 const std::string& activated()const{return activated_;}
};
class Probe:public gui2::dialogs::modal_dialog {''')
source = source.replace('class Probe:public gui2::dialogs::modal_dialog {', '''
void profile_cases(const std::string& theme){
 for(const std::string gesture:{"hover","hold"}){
  phase="home-profile-"+gesture+"-"+theme;click="quit";CitadelHome home("TwentyFourCharacterNameXY",117);home.show();
  if(!home.activated().empty())throw std::runtime_error("Profile description activated a destination");
  if(!profile_hold_finished)throw std::runtime_error("Profile tooltip capture ended before visibility checks completed");profile_hold_finished=false;
 }
}
class Probe:public gui2::dialogs::modal_dialog {''')
source = source.replace('phase="01-home";click="quit";Probe home(prepare_home_window());home.show();', '''
 if(std::getenv("CITADEL_INPUT_ONLY")){
  for(const std::string theme:{"default","modern"}){
   gui2::current_gui=gui2::guis.find(theme);gui2::current_gui->second.activate();phase="home-input-"+theme;click="quit";
   CitadelHome home("UniPlayer",0);home.show();if(!input_checks_finished)throw std::runtime_error("Home input timer check ended before completion");input_checks.reset();input_checks_finished=false;
  }
  if(captured_phases.size()!=2)throw std::runtime_error("Missing Quit hold visibility capture");
  std::cout<<"CITADEL INPUT VISIBILITY PASS: 2 captures\\n";return true;
 }
 if(std::getenv("CITADEL_PROFILE_ONLY")){
  for(const std::string theme:{"default","modern"}){gui2::current_gui=gui2::guis.find(theme);gui2::current_gui->second.activate();profile_cases(theme);}
  if(captured_phases.size()!=4)throw std::runtime_error("Missing profile visibility capture");
  std::cout<<"CITADEL PROFILE VISIBILITY PASS: 4 captures\\n";return true;
 }
 verify_native_menu_theme_boundaries();
 for(const std::string theme:{"default","modern"}) {
  gui2::current_gui=gui2::guis.find(theme);gui2::current_gui->second.activate();
  const auto original=gui2::get_control("button","default");
  {MenuThemeScope scope("alliance_fixture");
   if(menu_theme_depth()!=1||gui2::get_control("button","default")==original)throw std::runtime_error("Menu theme did not clone native definition");
   {MenuThemeScope nested("message");if(menu_theme_depth()!=2)throw std::runtime_error("Nested dialog lost theme context");}
  }
  if(menu_theme_depth()!=0||gui2::get_control("button","default")!=original)throw std::runtime_error("Menu theme leaked into engine widgets");
  for(const auto& action:home_ui::actions) {
   phase="home-"+theme+"-"+action.id;click=action.id;
   CitadelHome home(action.id==std::string("campaign")?"TwentyFourCharacterNameXY":"UniPlayer",action.id==std::string("campaign")?117:0);
   home.show();if(home.activated()!=action.id)throw std::runtime_error("Home action dispatched incorrectly");
  }
  phase="home-input-"+theme;click="quit";{CitadelHome home("UniPlayer",0);home.show();if(!input_checks_finished)throw std::runtime_error("Home input timer check ended before completion");input_checks.reset();input_checks_finished=false;}
  profile_cases(theme);
 }
 gui2::current_gui=gui2::default_gui;gui2::current_gui->second.activate();''')
source = source.replace('std::cout<<"PRODUCT UI REAL RENDER PASS', '''
 for(const std::string theme:{"default","modern"}) {
  gui2::current_gui=gui2::guis.find(theme);gui2::current_gui->second.activate();
  phase="native-load-"+theme;click="cancel";savegame::load_game_metadata metadata;metadata.manager=savegame::save_index_class::default_saves_dir();gui2::dialogs::game_load load(metadata);load.show();
  phase="native-achievements-"+theme;gui2::dialogs::achievements_dialog achievements;achievements.show();
  phase="native-preferences-"+theme;gui2::dialogs::preferences_dialog preferences;preferences.show();
  {MenuThemeScope parent("alliance_fixture");
   phase="native-confirmation-"+theme;gui2::show_message("Leave battle?","Your saved campaign remains available.",gui2::dialogs::message::yes_no_buttons);
   phase="native-dropdown-"+theme;std::vector<config> choices={config{"label","Campaign"},config{"label","Room"},config{"label","Team"}};
   gui2::dialogs::drop_down_menu dropdown(rect{600,340,120,48},choices,0,false,false);dropdown.show();
  }
  if(menu_theme_depth()!=0)throw std::runtime_error("Native menu scope remained active");
 }
 for(const std::string theme:{"default","modern"}){
  for(const auto& action:home_ui::actions)if(!captured_phases.count("home-"+theme+"-"+action.id))throw std::runtime_error("Missing home action capture");
  for(const std::string prefix:{"home-input-","home-profile-hover-","home-profile-hold-","native-load-","native-achievements-","native-preferences-","native-confirmation-","native-dropdown-"})
   if(!captured_phases.count(prefix+theme))throw std::runtime_error("Missing final native/input capture: "+prefix+theme);
 }
 for(const std::string expected:{"02-login","03-signup","04-team-editor","05-team-workspace","06-room-create","07-heroes","08-campaign","09-hero-search","10-hero-empty","11-hero-page","12-refumee","13-team-results","14-team-watch","15-team-hub","16-team-roster","17-plan-input","18-room-input","19-room-filters","20-room-capacity","21-room-filtered","22-team-more","23-campaign-celes"})
  if(!captured_phases.count(expected))throw std::runtime_error("Missing product capture: "+expected);
 if(captured_phases.size()!=56)throw std::runtime_error("Unexpected final capture count");
 std::cout<<"CITADEL UI REAL RENDER PASS: 56 captures\\n";
 std::cout<<"PRODUCT UI REAL RENDER PASS''')
source += '''
void citadel_native_arm(gui2::window& window){
 if(phase.compare(0,7,"native-")==0)arm(window);
}
'''
source = source.replace('const auto action=click;', '''if(phase.find("home-input-")==0){input_checks->finish_long_press();input_checks_finished=true;w->set_retval(gui2::retval::CANCEL);return;}
 if(phase.find("home-profile-")==0){auto& label=w->find_widget<gui2::label>("home_player");label.fire(gui2::event::LEFT_BUTTON_UP,label);label.fire(gui2::event::LEFT_BUTTON_CLICK,label);gui2::dialogs::tip::remove();w->set_retval(gui2::retval::CANCEL);return;}
 const auto action=click;''')
source = source.replace('geometry.flush();', 'geometry.flush();captured_phases.insert(phase);')
source = source.replace('if(!pixels||!SDL_SaveBMP', '''if(phase.find("home-profile-")==0||phase.find("home-input-")==0){
  auto* tip=gui2::dialogs::tip::citadel_current_tooltip();if(!tip)throw std::runtime_error("Tooltip disappeared before capture");const auto box=tip->get_rectangle();
  Uint8 r=0,g=0,b=0,a=0;const auto screen=video::game_canvas_size();const int inset=alliance_game::home_ui::px(3);
  if(!pixels||!SDL_ReadSurfacePixel(pixels,(box.x+inset)*pixels->w/screen.x,(box.y+inset)*pixels->h/screen.y,&r,&g,&b,&a)||r!=81||g!=71||b!=55)throw std::runtime_error("Tooltip surface was not rendered before capture");
 }
 if(!pixels||!SDL_SaveBMP''')
source = source.replace('const auto box=w->get_rectangle();', '''
 if(phase.find("home-")==0){const bool long_profile=phase.find("campaign")!=std::string::npos||phase.find("home-profile-")==0;citadel_home_checks::geometry(*w,long_profile?"TwentyFourCharacterNameXY":"UniPlayer",long_profile?117:0);}
 if(phase.find("native-load-")==0)verify_native_load_geometry(*w);
 if(phase.find("native-preferences-")==0)verify_native_preferences_geometry(*w);
 const auto box=w->get_rectangle();''')
p.write_text(source)
sys.path.insert(0, str(root))
from alliance_game.menu_theme_overlay import (
    generate_modal_theme_overlay, generate_gui_theme_overlay,
    generate_preferences_theme_overlay,
)
modal = generate_modal_theme_overlay((root / 'engine/src/gui/dialogs/modal_dialog.cpp').read_bytes()).decode()
modal = modal.replace('static lg::log_domain log_display', 'extern void citadel_native_arm(gui2::window&);\nstatic lg::log_domain log_display')
modal = modal.replace('window::show(auto_close_time);', '::citadel_native_arm(*this);\n\t\twindow::show(auto_close_time);')
(out / 'modal_dialog.cpp').write_text(modal)
(out / 'gui_definition.cpp').write_bytes(generate_gui_theme_overlay((root / 'engine/src/gui/core/gui_definition.cpp').read_bytes()))
(out / 'render_preferences.cpp').write_bytes(generate_preferences_theme_overlay(
    (root / 'engine/src/gui/dialogs/preferences_dialog.cpp').read_bytes()))
tooltip = (root / 'engine/src/gui/dialogs/tooltip.cpp').read_text()
tooltip = tooltip.replace('static std::unique_ptr<tooltip> tip;',
                          'static std::unique_ptr<tooltip> tip;\nwindow* citadel_current_tooltip(){return tip.get();}')
(out / 'render_tooltip.cpp').write_text(tooltip)

# The pinned host SDL has only the dummy video backend. Supply a current-size
# display mode to the Preferences fixture, which otherwise asserts on an empty
# hardware mode list. Controls/layout/rendering remain the production code.
(out / 'render_display_modes.cpp').write_text(r'''#include "SDL3/SDL_video.h"
#include "video.hpp"
#include <cstring>
extern "C" SDL_DisplayMode** __real_SDL_GetFullscreenDisplayModes(SDL_DisplayID,int*);
extern "C" SDL_DisplayMode** __wrap_SDL_GetFullscreenDisplayModes(SDL_DisplayID display,int* count) {
 auto* actual=__real_SDL_GetFullscreenDisplayModes(display,count);
 if(actual&&*count>0)return actual;
 SDL_free(actual);
 auto* block=static_cast<unsigned char*>(SDL_calloc(1,sizeof(SDL_DisplayMode*)*2+sizeof(SDL_DisplayMode)));
 if(!block)return nullptr;
 auto** modes=reinterpret_cast<SDL_DisplayMode**>(block);
 auto* mode=reinterpret_cast<SDL_DisplayMode*>(block+sizeof(SDL_DisplayMode*)*2);
 const auto canvas=video::game_canvas_size();mode->displayID=display;mode->format=SDL_PIXELFORMAT_XRGB8888;
 mode->w=canvas.x;mode->h=canvas.y;mode->pixel_density=1;mode->refresh_rate=60;mode->refresh_rate_numerator=60;mode->refresh_rate_denominator=1;
 modes[0]=mode;*count=1;return modes;
}
''')
