"""Native GUI2 home rendering and touch checks; generated hooks are audit-only."""
from pathlib import Path
import argparse
import sys

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
out = parser.parse_args().output
out.mkdir(parents=True, exist_ok=True)
(out / 'render_probe.cpp').write_text(r'''
#include "tools/android-presentation/mobile_home.hpp"
#include "tests/mobile_home_render_cases.hpp"
#include "alliance_game/native_menu_theme.hpp"
#include "game_launcher.hpp"
#include "game_config_manager.hpp"
#include "gui/dialogs/modal_dialog.hpp"
#include "gui/dialogs/help_browser.hpp"
#include "gui/widgets/tree_view_node.hpp"
#include "help/help.hpp"
#include "events.hpp"
#include "video.hpp"
#include "SDL3/SDL_surface.h"
#include "SDL3/SDL_timer.h"
#include <fstream>
#include <iostream>
#include <set>
namespace {
namespace home=alliance_game::mobile_home;
gui2::window* watched=nullptr;
int ticks=0;std::string phase,click;std::set<std::string> captures;
std::string name;std::size_t count=0;
std::unique_ptr<mobile_home_checks::InputChecks> inputs;
Uint64 held=0;
void arm(gui2::window& w){watched=&w;ticks=0;}
std::ofstream geometry("/render/geometry.txt");
class Monitor:public events::pump_monitor {
 void process()override {
  if(!watched)return;++ticks;
  if(phase.find("input-")==0&&ticks==3){inputs->short_checks();inputs->begin_long_press();held=SDL_GetTicks();}
  if(phase.find("help-browser-")==0&&ticks==3){
   auto& topic=watched->find_widget<gui2::tree_view_node>("+gameplay").find_widget<gui2::widget>("tree_view_node_label");const auto box=topic.get_rectangle();
   SDL_WarpMouseInWindow(video::get_window(),box.x+box.w/2,box.y+box.h/2);
   watched->fire(gui2::event::SDL_MOUSE_MOTION,*watched,point{box.x+box.w/2,box.y+box.h/2});
   topic.fire(gui2::event::LEFT_BUTTON_DOWN,topic);topic.fire(gui2::event::LEFT_BUTTON_UP,topic);watched->mouse_capture(false);topic.fire(gui2::event::LEFT_BUTTON_CLICK,topic);
  }
  if(ticks<12)return;
  if(phase.find("input-")==0&&SDL_GetTicks()-held<850)return;
  auto* w=watched;watched=nullptr;
  const auto bounds=w->get_rectangle();
  if(bounds.x<0||bounds.y<0||bounds.x+bounds.w>video::game_canvas_size().x||bounds.y+bounds.h>video::game_canvas_size().y)throw std::runtime_error("Clipped native window");
  geometry<<phase<<" window "<<bounds.x<<' '<<bounds.y<<' '<<bounds.w<<' '<<bounds.h<<'\n';
  if(phase.find("help-browser-")==0){
   if(w->find_widget<gui2::label>("topic_title").get_label()!="Gameplay")throw std::runtime_error("Touch topic selection did not navigate native Help");
   const auto close=w->find_widget<gui2::button>("ok").get_rectangle();if(close.w<48||close.h<48)throw std::runtime_error("Small Help Close target");
  }else{
   mobile_home_checks::geometry(*w,name,count);
   const auto quit=w->find_widget<gui2::button>("quit").get_rectangle(),help=w->find_widget<gui2::button>("help").get_rectangle();
   if(help.x!=quit.x+quit.w+home::px(8)||help.y!=quit.y)throw std::runtime_error("Help is not adjacent to Quit");
   for(const auto& action:home::actions){const auto box=w->find_widget<gui2::button>(action.id).get_rectangle();
    if(box.w<48||box.h<48)throw std::runtime_error("Small touch target");
    geometry<<phase<<' '<<action.id<<' '<<box.x<<' '<<box.y<<' '<<box.w<<' '<<box.h<<'\n';}
   const auto profile=w->find_widget<gui2::widget>("home_profile").get_rectangle();geometry<<phase<<" profile "<<profile.x<<' '<<profile.y<<' '<<profile.w<<' '<<profile.h<<'\n';
  }
  auto pixels=video::read_pixels();if(!pixels||!SDL_SaveBMP(pixels,("/render/"+phase+".bmp").c_str()))throw std::runtime_error("Capture failed");
  captures.insert(phase);geometry.flush();
  if(phase.find("input-")==0){inputs->finish_long_press();w->set_retval(gui2::retval::CANCEL);return;}
  if(phase.find("help-return-")==0){w->set_retval(gui2::retval::CANCEL);return;}
  if(phase.find("help-browser-")==0){
   auto& close=w->find_widget<gui2::button>("ok");const auto box=close.get_rectangle();SDL_WarpMouseInWindow(video::get_window(),box.x+box.w/2,box.y+box.h/2);
   w->fire(gui2::event::SDL_MOUSE_MOTION,*w,point{box.x+box.w/2,box.y+box.h/2});
   close.fire(gui2::event::LEFT_BUTTON_DOWN,close);close.fire(gui2::event::LEFT_BUTTON_UP,close);w->mouse_capture(false);close.fire(gui2::event::LEFT_BUTTON_CLICK,close);return;
  }
  auto& b=w->find_widget<gui2::button>(click);const auto at=b.get_rectangle();w->fire(gui2::event::SDL_MOUSE_MOTION,*w,point{at.x+at.w/2,at.y+at.h/2});
  b.fire(gui2::event::LEFT_BUTTON_DOWN,b);b.fire(gui2::event::LEFT_BUTTON_UP,b);w->mouse_capture(false);b.fire(gui2::event::LEFT_BUTTON_CLICK,b);
 }
};
class Home:public gui2::dialogs::modal_dialog {
 std::string theme_,activated_;
 const std::string& window_id()const override{static const std::string id="alliance_mobile_home";return id;}
 void pre_show()override {
  set_click_dismiss(false);set_enter_disabled(true);set_escape_disabled(true);
  home::set_profile(*this,name,count);home::bind(*this);
  if(phase.find("input-")==0)inputs=std::make_unique<mobile_home_checks::InputChecks>(*this);
  else for(const auto& action:home::actions){auto& button=find_widget<gui2::button>(action.id);button.set_retval(gui2::retval::NONE);
   gui2::event::connect_signal_mouse_left_click(button,[this,id=std::string(action.id)](auto&&...){
    activated_=id;if(id=="help"){
     phase="help-browser-"+theme_;help::show_help();
     phase="help-return-"+theme_;home::set_profile(*this,name,count);arm(*this);
    }else set_retval(gui2::retval::CANCEL);
   });}
  arm(*this);
 }
 public:explicit Home(std::string theme):modal_dialog(home::prepare_window()),theme_(std::move(theme)){}
 const std::string& activated()const{return activated_;}
};
}
void mobile_native_arm(gui2::window& w){if(phase.find("help-browser-")==0)arm(w);}
bool auth_render_fixture(game_launcher&) {
 Monitor monitor;game_config_manager::get()->load_game_config_for_create(true);
 for(const std::string theme:{"default","modern"}){
  gui2::current_gui=gui2::guis.find(theme);gui2::current_gui->second.activate();
  for(const auto& action:home::actions){phase="home-"+theme+"-"+action.id;click=action.id;
   name=std::string(action.id)=="campaign"?"TwentyFourCharacterNameXY":"UniPlayer";count=std::string(action.id)=="campaign"?117:0;
   Home window(theme);window.show();if(window.activated()!=action.id)throw std::runtime_error("Touch action dispatch differs");}
  phase="input-"+theme;click="quit";name="UniPlayer";count=0;{Home window(theme);window.show();inputs.reset();}
 }
 if(captures.size()!=26)throw std::runtime_error("Unexpected number of rendered cases");
 std::cout<<"MOBILE HOME REAL RENDER PASS: 26 captures\n";return true;
}
''')
main = (root / 'engine/src/wesnoth.cpp').read_text()
main = main.replace('#include "game_launcher.hpp"', 'extern bool auth_render_fixture(class game_launcher&);\n#include "game_launcher.hpp"')
main = main.replace('\t\tif(game->play_test() == false) {', '\t\tif(auth_render_fixture(*game)) return 0;\n\t\tif(game->play_test() == false) {')
(out / 'render_wesnoth.cpp').write_text(main)
sys.path.insert(0, str(root))
from alliance_game.menu_theme_overlay import generate_modal_theme_overlay, generate_gui_theme_overlay
modal = generate_modal_theme_overlay((root / 'engine/src/gui/dialogs/modal_dialog.cpp').read_bytes()).decode()
modal = modal.replace('static lg::log_domain log_display', 'extern void mobile_native_arm(gui2::window&);\nstatic lg::log_domain log_display')
modal = modal.replace('window::show(auto_close_time);', '::mobile_native_arm(*this);\n\t\twindow::show(auto_close_time);')
(out / 'modal_dialog.cpp').write_text(modal)
(out / 'gui_definition.cpp').write_bytes(generate_gui_theme_overlay((root / 'engine/src/gui/core/gui_definition.cpp').read_bytes()))
tooltip = (root / 'engine/src/gui/dialogs/tooltip.cpp').read_text()
tooltip = tooltip.replace('static std::unique_ptr<tooltip> tip;', 'static std::unique_ptr<tooltip> tip;\nwindow* citadel_current_tooltip(){return tip.get();}')
(out / 'render_tooltip.cpp').write_text(tooltip)
