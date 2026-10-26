"""Actual native Room dashboard/staging renderer; only socket/account boundaries are substituted."""
from pathlib import Path
import sys,json
import argparse
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);args=p.parse_args()
root=Path(__file__).resolve().parents[1];out=args.output;out.mkdir(parents=True,exist_ok=True);sys.path.insert(0,str(root/'alliance_game'))
def write_output(name,text):
 target=out/name
 if not target.exists() or target.read_text()!=text:target.write_text(text)
from ordinary_mp_auth_overlay import generate_lobby_overlay
from ordinary_mp_sides_overlay import generate_side_overlay
src=(root/'alliance_game/individual_rooms.cpp').read_text()
extra=r'''
#include "events.hpp"
#include "game_launcher.hpp"
#include "game_config_manager.hpp"
#include "game_initialization/lobby_info.hpp"
#include "game_initialization/connect_engine.hpp"
#include "game_initialization/playcampaign.hpp"
#include "gui/dialogs/multiplayer/mp_staging.hpp"
#include "gui/dialogs/multiplayer/mp_join_game.hpp"
#include "preferences/preferences.hpp"
#include "saved_game.hpp"
#include "gui/dialogs/multiplayer/lobby.hpp"
#include "gui/widgets/button.hpp"
#include "gui/widgets/image.hpp"
#include "gui/widgets/drawing.hpp"
#include "gui/widgets/menu_button.hpp"
#include "gui/widgets/scroll_label.hpp"
#include "gui/widgets/text_box.hpp"
#include "gui/widgets/listbox.hpp"
#include "gui/widgets/tree_view.hpp"
#include "gui/widgets/toggle_button.hpp"
#include "wesnothd_connection.hpp"
#include "video.hpp"
#include "SDL3/SDL_surface.h"
#include <fstream>
#include <iostream>
namespace {
gui2::window* render_window=nullptr;std::string render_phase;int render_ticks=0,render_count=0;bool render_scrolled=false;std::string render_filter;std::string render_button="cancel";
std::ofstream render_report("/render/geometry.txt");
mp::lobby_info* render_info=nullptr;
void render_arm(gui2::window& window,const std::string& title){render_window=&window;render_ticks=0;render_scrolled=false;render_phase=std::to_string(++render_count)+"-"+title;}
class RenderMonitor : public events::pump_monitor {
 void process() override {
  if(!render_window)return;
  ++render_ticks;
  if(render_ticks==3&&render_phase.find("bottom")==std::string::npos){if(auto* input=render_window->find_widget<gui2::text_box>("chat_input",false,false)){render_window->keyboard_capture(input);SDL_Event event{};event.type=SDL_EVENT_TEXT_INPUT;event.text.text="Ready to play";SDL_PushEvent(&event);}}
  if(render_ticks==2&&!render_filter.empty()) {
   const char* id=render_filter=="open"?"filter_vacant_slots":render_filter=="playing"?"filter_invert":"filter_with_ignored";
   auto& checkbox=render_window->find_widget<gui2::toggle_button>(id);checkbox.set_value(true);
   checkbox.fire(gui2::event::NOTIFY_MODIFIED,checkbox,nullptr);
  }
  if(render_ticks<10)return;
  auto* window=render_window;render_window=nullptr;
  const auto bounds=window->get_rectangle();
  if(auto* input=window->find_widget<gui2::text_box>("chat_input",false,false);input&&input->get_value()!="Ready to play")throw std::runtime_error("Native chat input lost focus or text");
  auto pixels=video::read_pixels();if(!pixels||!SDL_SaveBMP(pixels,("/render/"+render_phase+".bmp").c_str()))throw std::runtime_error("Render failed");
  render_report<<render_phase<<" window "<<bounds.x<<' '<<bounds.y<<' '<<bounds.w<<' '<<bounds.h<<'\n';
  for(const char* id:{"title","message","listbox","ok","cancel","create","join_global","observe_global","server_info","filter_text","gamelist","quit","status_label","filter_with_friends","filter_with_ignored","filter_vacant_slots","filter_invert","leader_image","leader_faction","leader_gender","select_leader","controller","side_team","side_color","chat_input","close_window"}) {
    auto* child=window->find(id,false);if(!child)continue;const auto r=child->get_rectangle();
    render_report<<render_phase<<' '<<id<<' '<<r.x<<' '<<r.y<<' '<<r.w<<' '<<r.h<<" visible="<<int(child->get_visible())<<'\n';
    if(child->get_visible()==gui2::widget::visibility::visible&&(dynamic_cast<gui2::button*>(child)||dynamic_cast<gui2::text_box*>(child)||dynamic_cast<gui2::toggle_button*>(child)||dynamic_cast<gui2::menu_button*>(child))&&(r.h<48||r.w<48))throw std::runtime_error("Primary control under48: "+std::string(id));
    if(child->get_visible()==gui2::widget::visibility::visible&&child->get_drawing_action()==gui2::widget::redraw_action::full&&(r.x<bounds.x||r.y<bounds.y||r.x+r.w>bounds.x+bounds.w||r.y+r.h>bounds.y+bounds.h))throw std::runtime_error("Widget outside dialog");
  }
  if(render_phase.find("Dashboard")!=std::string::npos) {
   for(const char* id:{"games_list_tab_bar","join_queue","leave_queue","view_profile","view_match_history"})
    if(auto* control=window->find(id,false);control&&control->get_visible()==gui2::widget::visibility::visible)throw std::runtime_error("Unsupported control remains visible");
   if(window->find_widget<gui2::button>("server_info").get_label()!="Filters")throw std::runtime_error("Filters label was overwritten");
   const auto expected=render_filter.empty()?3:1;
   if(!render_info||render_info->games_visibility().count()!=static_cast<std::size_t>(expected))throw std::runtime_error("Live dashboard filter count differs");
   render_report<<render_phase<<" visible_rooms "<<expected<<'\n';
  }
  if(render_phase.find("Staging")!=std::string::npos||render_phase.find("Joined")!=std::string::npos){
   if(!window->find_widget<gui2::drawing>("leader_image").get_label().empty())throw std::runtime_error("Unselected fixture hero has random or invented art");
   if(window->find_widget<gui2::label>("leader_type").get_label().str().find("Random")!=std::string::npos)throw std::runtime_error("Random leader label remains visible");
   if(window->find_widget<gui2::label>("leader_faction").parent()->get_visible()!=gui2::widget::visibility::invisible||window->find_widget<gui2::image>("leader_gender").parent()->get_visible()!=gui2::widget::visibility::invisible)throw std::runtime_error("Obsolete random faction/gender remains visible");
  }
  render_report.flush();
  if(auto* message=dynamic_cast<gui2::scroll_label*>(window->find("message",false))) {
   if(!render_scrolled&&!message->vertical_scrollbar_at_end()) {
    render_report<<render_phase<<" scrollable yes"<<'\n';
    message->scroll_vertical_scrollbar(gui2::scrollbar_base::END);render_scrolled=true;
    render_window=window;render_phase+=" bottom";render_ticks=0;return;
   }
  }
  if(auto* sides=dynamic_cast<gui2::tree_view*>(window->find("side_list",false))) {
   if(auto* start=window->find_widget<gui2::button>("ok",false,false);start&&start->get_active())throw std::runtime_error("Individual staging must not offer manual Start");
   if(!render_scrolled&&!sides->vertical_scrollbar_at_end()) {
    render_report<<render_phase<<" sides scrollable yes"<<'\n';
    sides->scroll_vertical_scrollbar(gui2::scrollbar_base::END);render_scrolled=true;
    render_window=window;render_phase+=" bottom";render_ticks=0;return;
   }
  }
  auto& button=window->find_widget<gui2::button>(render_button);button.fire(gui2::event::LEFT_BUTTON_CLICK,button);
 }
};
}
'''
src=src.replace('namespace alliance_game {',extra+'\nnamespace alliance_game {',1).replace('    return dialog.show()?dialog.selected_index():-1;','    render_arm(dialog,title);\n    return dialog.show()?dialog.selected_index():-1;')
entry=r'''
namespace alliance_game {
bool has_player_session() noexcept{return true;}
bool account_session_interaction_active() noexcept{return false;}
bool casual_game_active() noexcept{return true;}
bool choose_casual_side(int){return false;}
std::string casual_side_label(int side,const std::string&){return side==1?"Choose hero, then Ready":"Waiting for player";}
}
config render_room(int n,int id) {
 config room{"id",id,"room_id",id,"name",n==2?"Friendly duel":"Eight player room with a long descriptive title for the reviewed battle settings",
  "individual_room",true,"room_revision",7,"room_members",n==2?1:8,"room_capacity",n,"room_ready",n==2?0:7,"room_status",n==2?"waiting":"full",
  "room_gold",150,"room_base_income",2,"room_turns",30,"mp_scenario","multiplayer_The_Freelands","mp_scenario_name",n==2?"The Freelands":"A map with a deliberately longer readable title for an eight player battle",
  "mp_era","era_default","observer",true,"mp_fog",true,"mp_shroud",false,"mp_countdown",true,"mp_countdown_init_time",300,"mp_countdown_turn_bonus",60,"mp_countdown_reservoir_time",300,"mp_countdown_action_bonus",5,
  "slots",n==2?"1/2":"0/8","human_sides",n,"mp_village_gold",2,"mp_village_support",1,"experience_modifier",100};
 std::ifstream file(n==2?"/gamedata/data/multiplayer/maps/2p_The_Freelands.map":"/gamedata/data/multiplayer/maps/8p_Mokena_Prairie.map");
 std::ostringstream terrain;terrain<<file.rdbuf();room["map_data"]=terrain.str();
 room.add_child("slot_data",config{"vacant",n==2?1:0,"max",n});
 for(int i=1;i<=n;++i)room.add_child("room_side",config{"side",i,"team_name",i%2?"North alliance":"South alliance","player_id","PlayerUsernameLong"+std::to_string(i),"gold",150,"income",0,"base_income",2,"village_gold",2,"village_support",1,"fog",true,"shroud",false,"connected",true});
 return room;
}
config native_room_fixture_level;
bool auth_render_fixture(game_launcher&) {
 RenderMonitor monitor;

 game_config_manager::get()->load_game_config_for_create(true);
 mp::lobby_info info;config games;games.add_child("gamelist").add_child("game",render_room(2,2));games.mandatory_child("gamelist").add_child("game",render_room(8,8));auto running=render_room(2,3);running["room_status"]="playing";running["room_members"]=2;running["name"]="Running duel";running.add_child("turn_data",config{"current",2,"max",30});games.mandatory_child("gamelist").add_child("game",running);auto standard=render_room(2,4);standard["individual_room"]=false;standard["name"]="Standard manual room";games.mandatory_child("gamelist").add_child("game",standard);info.process_gamelist(games);render_info=&info;
 wesnothd_connection connection("127.0.0.1","9");int joined=0;
 for(const std::string theme:{"default","modern"}) {
 auto picked=gui2::guis.find(theme);if(picked==gui2::guis.end())throw std::runtime_error("Missing theme");gui2::current_gui=picked;gui2::current_gui->second.activate();
 for(const std::string filter:{"","open","playing"}) {
  gui2::dialogs::mp_lobby lobby(info,connection,joined);render_button="quit";render_filter=filter;render_arm(lobby,"Dashboard "+theme+" "+filter);lobby.show();
 }
 render_filter.clear();render_button="cancel";prefs::get().set_login("UniPlayer");
 for(int seats:{2,8}) {
  config packet;auto context=render_room(seats,seats);context["id"]=seats==2?"10000000-0000-4000-8000-000000000002":"10000000-0000-4000-8000-000000000008";packet.add_child("alliance_game",context);alliance_game::observe_individual_room(packet);
  saved_game state;state.classification().type=campaign_type::type::multiplayer;state.classification().era_id="era_default";
  state.mp_settings().use_map_settings=true;state.mp_settings().name="Individual room rendering";
  auto selected=game_config_manager::get()->game_config().find_child("multiplayer","id",seats==2?"multiplayer_The_Freelands":"multiplayer_8p_Mokena_Prairie");
  if(!selected)throw std::runtime_error("Missing render map");
  config scenario=*selected;scenario["individual_room"]=true;scenario["turns"]=30;
  if(scenario.child_count("side")!=std::size_t(seats))throw std::runtime_error("Render map seat count does not match fixture");
  int number=0;for(auto& side:scenario.child_range("side")){side["side"]=++number;side["controller"]="human";side["gold"]=150;side["allow_player"]=true;side["team_name"]=std::to_string(number%2+1);}
  state.set_scenario(std::move(scenario));mp_game_metadata metadata(connection);metadata.is_host=true;metadata.queue_id=0;
  ng::connect_engine engine(state,true,&metadata);gui2::dialogs::mp_staging staging(engine,&connection);render_arm(staging,"Staging "+theme+" "+std::to_string(seats));staging.show();
  native_room_fixture_level.clear();native_room_fixture_level.add_child("multiplayer",config{"scenario","Individual room rendering"});auto& joined_scenario=native_room_fixture_level.add_child("scenario",state.get_starting_point());
  for(auto& side:joined_scenario.child_range("side")){side["player_id"]=side["side"].to_int()==1?"UniPlayer":"OtherPlayer";side["allow_changes"]=true;side["income"]=0;}
  gui2::dialogs::mp_join_game joined_view(state,connection);render_arm(joined_view,"Joined "+theme+" "+std::to_string(seats));joined_view.show();
 }
 }
 std::cout<<"INDIVIDUAL REAL RENDER CHECK PASSED"<<std::endl;return true;
}
'''
write_output('render_probe.cpp',src+entry)
wes=(root/'engine/src/wesnoth.cpp').read_text();anchor='\t\tif(game->play_test() == false) {\n';assert wes.count(anchor)==1
wes=wes.replace('#include "game_launcher.hpp" // for game_launcher, etc','#include "game_launcher.hpp" // for game_launcher, etc\nextern bool auth_render_fixture(game_launcher&);').replace(anchor,'\t\tif(auth_render_fixture(*game)) return 0;\n'+anchor)
write_output('render_wesnoth.cpp',wes)
lobby=generate_lobby_overlay((root/'engine/src/gui/dialogs/multiplayer/lobby.cpp').read_bytes()).decode()
start=lobby.index('void mp_lobby::network_handler()');opening=lobby.index('{',start);depth=1;end=opening+1
while depth:
 depth+=(lobby[end]=='{')-(lobby[end]=='}');end+=1
lobby=lobby[:opening]+'{ /* Render fixture: network polling substituted; actual dashboard data/widgets retained. */ }'+lobby[end:]
write_output('render_lobby.cpp',lobby)

staging=generate_side_overlay("gui/dialogs/multiplayer/mp_staging.cpp",(root/"engine/src/gui/dialogs/multiplayer/mp_staging.cpp").read_bytes()).decode()
start=staging.index("void mp_staging::network_handler()");opening=staging.index("{",start);depth=1;end=opening+1
while depth:
 depth+=(staging[end]=="{")-(staging[end]=="}");end+=1
staging=staging[:opening]+"{ /* Only socket polling substituted in render fixture. */ }"+staging[end:]
write_output('render_staging.cpp',staging)

joined=generate_side_overlay("gui/dialogs/multiplayer/mp_join_game.cpp",(root/"engine/src/gui/dialogs/multiplayer/mp_join_game.cpp").read_bytes()).decode()
joined=joined.replace('#include "alliance_game/native_touch_layout.hpp"','#include "alliance_game/native_touch_layout.hpp"\nextern config native_room_fixture_level;').replace(', level_()',', level_(native_room_fixture_level)')
start=joined.index("void mp_join_game::network_handler()");opening=joined.index("{",start);depth=1;end=opening+1
while depth:
 depth+=(joined[end]=="{")-(joined[end]=="}");end+=1
joined=joined[:opening]+"{ /* Only socket polling substituted in render fixture. */ }"+joined[end:]
write_output('render_joined.cpp',joined)
