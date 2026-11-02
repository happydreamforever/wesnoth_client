// SPDX-License-Identifier: GPL-2.0-or-later
// Native GUI2 render fixture; synthetic SDL touch and authenticated-response fixtures.
// Compile alongside the generated Android-only Court workspace.
#define COURT_MOBILE_FRAME_PROBE
#include "court_mobile_workspace.hpp"
#include "tools/android-presentation/mobile_home.hpp"
#include "tools/android-presentation/court_help.hpp"
#include "tools/android-presentation/court_touch_actions.hpp"
#include "gui/dialogs/drop_down_menu.hpp"
#include "gui/widgets/scrollbar_panel.hpp"
#include <cassert>
#include <sys/resource.h>
#include "alliance_game/native_menu_theme.hpp"
#include "game_launcher.hpp"
#include "game_config_manager.hpp"
#include "SDL3/SDL_surface.h"
#include "SDL3/SDL_timer.h"
#include <fstream>
#include <set>
#include <iostream>
namespace c=alliance_game::court_ui;
namespace {gui2::window* watched=nullptr;int ticks=0;Uint64 armed=0;std::string phase,scenario,click;int actions=0;std::atomic<int> mutations{0};c::State* active_state=nullptr;std::array<int,11> home_dispatch{};std::ofstream geo("/render/geometry.txt");
std::ofstream frames("/render/frames.csv");
alliance_game::court_mobile::Clock::time_point input_at{},last_frame{};int motion_step=0;bool awaiting=false;double pinch_initial_zoom=1;
std::set<std::string> initial_maps;
alliance_game::court_mobile::Camera saved_world,saved_area;
void press(const char* id){auto& b=watched->find_widget<gui2::button>(id);b.fire(gui2::event::LEFT_BUTTON_CLICK,b);}
void require_camera(const alliance_game::court_mobile::Camera& saved,const alliance_game::court_mobile::Camera& current,const char* context){
 if(std::abs(saved.cx-current.cx)>1e-6||std::abs(saved.cy-current.cy)>1e-6||std::abs(saved.scale()-current.scale())>1e-5)throw std::runtime_error(std::string(context)+" moved or rescaled camera");
}
void require_top_row(){
 int y=-1;std::vector<rect> boxes;
 for(const char* id:{"court_back","court_inbox","court_team","court_refresh","court_search_toggle","court_view","court_zoom_out","court_centre","court_zoom_in","court_collapse","court_actions","court_primary","court_secondary"}){
  const auto& control=watched->find_widget<gui2::widget>(id);if(control.get_visible()!=gui2::widget::visibility::visible)continue;
  auto b=control.get_rectangle();if(b.w<48||b.h<48)throw std::runtime_error(std::string("Small map control: ")+id);
  if(y<0)y=b.y+b.h/2;if(std::abs(y-(b.y+b.h/2))>1)throw std::runtime_error(std::string("Map control outside top row: ")+id);
  if(b.x<0||b.x+b.w>video::game_canvas_size().x)throw std::runtime_error(std::string("Map control clips: ")+id);boxes.push_back(b);
  geo<<phase<<" top-control "<<id<<' '<<b<<'\n';
 }
 std::sort(boxes.begin(),boxes.end(),[](const auto& a,const auto& b){return a.x<b.x;});for(std::size_t n=1;n<boxes.size();++n)if(boxes[n-1].x+boxes[n-1].w>boxes[n].x)throw std::runtime_error("Map controls overlap");
 auto map=watched->find_widget<gui2::drawing>("court_map").get_rectangle();if(y>=map.y)throw std::runtime_error("Map controls were not above map");
}
c::json::object fixture(const std::string&);
void push_touch(SDL_EventType type,SDL_FingerID id,double x,double y){auto size=video::game_canvas_size();SDL_Event e{};e.type=type;e.tfinger.type=type;e.tfinger.windowID=SDL_GetWindowID(video::get_window());e.tfinger.pressure=1;e.tfinger.touchID=1;e.tfinger.fingerID=id;e.tfinger.x=x/size.x;e.tfinger.y=y/size.y;SDL_SetEventEnabled(type,true);if(!SDL_PushEvent(&e))throw std::runtime_error(SDL_GetError());}
class Monitor:public events::pump_monitor{void process()override{if(!watched)return;++ticks;
if(scenario=="map-controls"){
 if(ticks<15||SDL_GetTicks()-armed<400)return;
 auto& world=active_state->world_camera;auto& area=active_state->area_cameras["A001"];
 if(actions==0){require_top_row();if(!watched->find_widget<gui2::button>("court_zoom_out").get_label().empty())throw std::runtime_error("Zoom out still depends on font glyph");const double before=world.zoom;press("court_zoom_out");if(!(world.zoom<before))throw std::runtime_error("Zoom-out did not reduce initial width fit");}
 else if(actions==1){press("court_centre");if(std::abs(world.zoom-1)>1e-8)throw std::runtime_error("Centre did not restore full overview");}
 else if(actions==2){press("court_zoom_in");saved_world=world;}
 else if(actions==3){dynamic_cast<c::Workspace*>(watched)->accept_snapshot("areas",fixture("areas"));require_camera(saved_world,world,"Refresh");press("court_view");}
 else if(actions==4){if(active_state->map)throw std::runtime_error("List did not open");press("court_view");}
 else if(actions==5){require_camera(saved_world,world,"List return");press("court_search_toggle");}
 else if(actions==6){if(active_state->map||watched->find_widget<gui2::text_box>("court_search").get_visible()!=gui2::widget::visibility::visible)throw std::runtime_error("Search did not expose input and list");press("court_view");}
 else if(actions==7){require_camera(saved_world,world,"Search return");press("court_team");}
 else if(actions==8){if(active_state->depth!=1||active_state->area!="A001")throw std::runtime_error("My Team did not open Area");require_top_row();press("court_zoom_in");saved_area=area;}
 else if(actions==9){press("court_back");}
 else if(actions==10){if(active_state->depth!=0)throw std::runtime_error("Area Back did not open World");require_camera(saved_world,world,"World return");press("court_team");}
 else if(actions==11){require_camera(saved_area,area,"Area return");require_top_row();auto* close=watched;watched=nullptr;auto pixels=video::read_pixels();if(!SDL_SaveBMP(pixels,("/render/"+phase+".bmp").c_str()))throw std::runtime_error("Map controls capture failed");geo.flush();close->set_retval(gui2::retval::CANCEL);return;}
 ++actions;armed=SDL_GetTicks();ticks=0;return;
}
if(scenario=="stress"){
 if(ticks<15||SDL_GetTicks()-armed<400)return;
 static long baseline=0;
 if(motion_step<512){active_state->selected=alliance_game::court_map::world_nodes[motion_step%128].id;dynamic_cast<c::Workspace*>(watched)->accept_snapshot("areas",fixture("areas"));struct rusage usage{};getrusage(RUSAGE_SELF,&usage);if(motion_step==256)baseline=usage.ru_maxrss;if(motion_step==511){geo<<phase<<" warmed_peak_rss_kib "<<baseline<<" final_peak_rss_kib "<<usage.ru_maxrss<<'\n';if(usage.ru_maxrss-baseline>16*1024)throw std::runtime_error("Repeated selections grew memory beyond bound");}++motion_step;return;}
 auto* close=watched;watched=nullptr;close->set_retval(gui2::retval::CANCEL);return;
}
if(scenario.rfind("motion-",0)==0){
 if(ticks<15||SDL_GetTicks()-armed<400)return;
 auto b=watched->find_widget<gui2::drawing>("court_map").get_rectangle();double x=b.x+b.w/2.,y=b.y+b.h/2.;
 if(motion_step==0){push_touch(SDL_EVENT_FINGER_DOWN,1,x-60,y);push_touch(SDL_EVENT_FINGER_DOWN,2,x+60,y);}
 else if(motion_step<=35){push_touch(SDL_EVENT_FINGER_MOTION,1,x-60-motion_step*3,y+motion_step*.5);push_touch(SDL_EVENT_FINGER_MOTION,2,x+60+motion_step*3,y+motion_step*.5);}
 else if(motion_step==36)push_touch(SDL_EVENT_FINGER_UP,1,x-165,y+17.5);
 else if(motion_step==40){const auto before=scenario=="motion-area"?active_state->area_cameras["A001"]:active_state->world_camera;auto* workspace=dynamic_cast<c::Workspace*>(watched);workspace->accept_snapshot(scenario=="motion-area"?"area":"areas",fixture(scenario=="motion-area"?"area":"areas"),scenario=="motion-area"?c::json::object{{"area_id","A001"}}:c::json::object{});const auto after=scenario=="motion-area"?active_state->area_cameras["A001"]:active_state->world_camera;if(before.zoom!=after.zoom||before.cx!=after.cx||before.cy!=after.cy)throw std::runtime_error("Refresh moved camera");}
 else if(motion_step<75)push_touch(SDL_EVENT_FINGER_MOTION,2,x+165+(motion_step-36)*2,y+17.5+(motion_step-36));
 else if(motion_step==75){push_touch(SDL_EVENT_FINGER_UP,2,x+243,y+56.5);}
 else if(motion_step<100){++motion_step;return;}
 else {auto* close=watched;watched=nullptr;close->set_retval(gui2::retval::CANCEL);return;}
 input_at=alliance_game::court_mobile::Clock::now();awaiting=true;++motion_step;return;
}

if(scenario.rfind("tap-",0)==0&&actions==0&&ticks>=15&&SDL_GetTicks()-armed>=400){
 press("court_centre");auto b=watched->find_widget<gui2::drawing>("court_map").get_rectangle();
 const auto& node=scenario=="tap-area"?alliance_game::court_map::castle_nodes[0]:alliance_game::court_map::world_nodes[scenario=="tap-inactive"?120:0];
 auto& camera=scenario=="tap-area"?active_state->area_cameras["A001"]:active_state->world_camera;auto p=camera.project({node.x,node.y});
 push_touch(SDL_EVENT_FINGER_DOWN,3,b.x+p.x,b.y+p.y);push_touch(SDL_EVENT_FINGER_UP,3,b.x+p.x,b.y+p.y);actions=1;armed=SDL_GetTicks();ticks=0;return;
}
if(actions==0 && ticks>=15 && SDL_GetTicks()-armed>=400 && (scenario.rfind("form-",0)==0||scenario=="purchase"||scenario=="plan-submit")){
 actions=1; if(scenario=="form-cash"||scenario=="form-team")watched->find_widget<gui2::menu_button>("court_actions").set_selected(1,true);
 else {auto& button=watched->find_widget<gui2::button>("court_primary");button.fire(gui2::event::LEFT_BUTTON_CLICK,button);}armed=SDL_GetTicks();ticks=0;return;}
if(scenario=="team-large-bottom"&&actions==0&&ticks>=15&&SDL_GetTicks()-armed>=400){watched->find_widget<gui2::listbox>("court_list").set_vertical_scrollbar_item_position(1000);actions=1;armed=SDL_GetTicks();ticks=0;return;}
if(actions==0 && ticks>=15 && SDL_GetTicks()-armed>=400 && scenario=="pinch"){
 const auto box=watched->find_widget<gui2::drawing>("court_map").get_rectangle();const auto size=video::game_canvas_size();
 auto push=[&](SDL_EventType type,SDL_FingerID finger,float x){SDL_Event e{};e.type=type;e.tfinger.type=type;e.tfinger.windowID=SDL_GetWindowID(video::get_window());e.tfinger.pressure=1;e.tfinger.touchID=1;e.tfinger.fingerID=finger;e.tfinger.x=x/size.x;e.tfinger.y=float(box.y+box.h/2)/size.y;SDL_SetEventEnabled(type,true);if(!SDL_PushEvent(&e))throw std::runtime_error(SDL_GetError());};
 pinch_initial_zoom=active_state->world_camera.zoom;const float x=box.x+box.w/2;push(SDL_EVENT_FINGER_DOWN,1,x-50);push(SDL_EVENT_FINGER_DOWN,2,x+50);push(SDL_EVENT_FINGER_MOTION,2,x+150);push(SDL_EVENT_FINGER_UP,2,x+150);push(SDL_EVENT_FINGER_UP,1,x-50);actions=1;armed=SDL_GetTicks();ticks=0;return;
}
if((scenario=="form-settings"||scenario=="form-settings-bottom"||scenario=="plan-submit")&&actions==1&&SDL_GetTicks()-armed>=400){watched->find_widget<gui2::menu_button>("court_actions").set_selected(1,true);actions=2;armed=SDL_GetTicks();ticks=0;return;}
if(scenario=="form-settings-bottom"&&actions==2&&SDL_GetTicks()-armed>=400){watched->find_widget<gui2::scrollbar_panel>("court_form_scroll").set_vertical_scrollbar_item_position(1000);actions=3;armed=SDL_GetTicks();ticks=0;return;}
if(scenario=="plan-submit"&&actions==2&&SDL_GetTicks()-armed>=400){watched->find_widget<gui2::text_box>("court_input_0").set_value("7");watched->find_widget<gui2::text_box>("court_input_1").set_value("55");watched->find_widget<gui2::menu_button>("court_pick_2").set_selected(3,true);watched->find_widget<gui2::menu_button>("court_pick_3").set_selected(1,true);watched->find_widget<gui2::menu_button>("court_pick_4").set_selected(1,true);watched->find_widget<gui2::menu_button>("court_pick_5").set_selected(1,true);actions=3;armed=SDL_GetTicks();ticks=0;return;}
if(scenario=="plan-submit"&&actions>=3&&actions<5&&SDL_GetTicks()-armed>=400){auto& b=watched->find_widget<gui2::button>("court_primary");b.fire(gui2::event::LEFT_BUTTON_CLICK,b);++actions;armed=SDL_GetTicks();ticks=0;return;}
if(scenario=="purchase"&&actions>0&&actions<3&&SDL_GetTicks()-armed>=400){auto& b=watched->find_widget<gui2::button>("court_primary");b.fire(gui2::event::LEFT_BUTTON_CLICK,b);++actions;armed=SDL_GetTicks();ticks=0;return;}
if((scenario=="home"||scenario=="home-empty")&&actions==0&&ticks>=15&&SDL_GetTicks()-armed>=400){
 for(const auto& action:alliance_game::mobile_home::actions){auto& b=watched->find_widget<gui2::button>(action.id);b.fire(gui2::event::LEFT_BUTTON_CLICK,b);}
 const auto b=watched->find_widget<gui2::label>("home_player").get_rectangle();push_touch(SDL_EVENT_FINGER_DOWN,3,b.x+b.w/2.,b.y+b.h/2.);actions=1;armed=SDL_GetTicks();ticks=0;return;
}
if((scenario=="home"||scenario=="home-empty")&&actions==1&&SDL_GetTicks()-armed>=750){auto b=watched->find_widget<gui2::label>("home_player").get_rectangle();push_touch(SDL_EVENT_FINGER_UP,3,b.x+b.w/2.,b.y+b.h/2.);for(int n:home_dispatch)if(n!=1)throw std::runtime_error("Home dispatch/profile changed an action");actions=2;armed=SDL_GetTicks();ticks=0;return;}
if(ticks<30||SDL_GetTicks()-armed<900)return;
if(scenario.rfind("tap-",0)==0){const auto expected=scenario=="tap-area"?std::string("A001-")+alliance_game::court_map::castle_nodes[0].id:alliance_game::court_map::world_nodes[scenario=="tap-inactive"?120:0].id;if(!active_state||active_state->selected!=expected)throw std::runtime_error("One tap did not select location");if(watched->find_widget<gui2::button>("court_collapse").get_label().str()!="Hide")throw std::runtime_error("Tap did not open details");}
if((scenario=="purchase"||scenario=="plan-submit")&&mutations.load()!=1)throw std::runtime_error("Reviewed purchase did not submit exactly once");
if(scenario=="pinch"&&(!active_state||std::abs(active_state->world_camera.zoom-std::min(8.,pinch_initial_zoom*2))>0.01))throw std::runtime_error("Raw SDL pinch did not zoom native map: "+std::to_string(active_state?active_state->world_camera.zoom:-1));if(active_state&&active_state->map&&active_state->view.empty()&&active_state->depth<2)require_top_row();auto* w=watched;watched=nullptr;const auto bounds=w->get_rectangle();if(bounds.x<0||bounds.y<0||bounds.x+bounds.w>video::game_canvas_size().x||bounds.y+bounds.h>video::game_canvas_size().y)throw std::runtime_error("Window clipped");geo<<phase<<" window "<<bounds<<'\n';for(const auto* id:{"court_back","court_primary","court_secondary","court_team","court_refresh"}){if(scenario=="home"||scenario=="home-empty"||scenario.rfind("help-",0)==0||scenario=="touch-item")break;const auto& control=w->find_widget<gui2::button>(id);if(control.get_visible()!=gui2::widget::visibility::visible)continue;const auto b=control.get_rectangle();if(b.w<48||b.h<48)throw std::runtime_error("Small touch target");geo<<id<<' '<<b<<'\n';}if(scenario=="home"||scenario=="home-empty"){
 for(const auto& action:alliance_game::mobile_home::actions){const auto b=w->find_widget<gui2::button>(action.id).get_rectangle();if(b.w<48||b.h<48)throw std::runtime_error("Small home action");}

 const auto panel=w->find_widget<gui2::widget>("home_profile").get_rectangle();int left=10000,right=0;for(const char* id:{"home_player","home_heroes","home_cash"}){auto b=w->find_widget<gui2::label>(id).get_rectangle();left=std::min(left,b.x);right=std::max(right,b.x+b.w);}if(std::abs((left-panel.x)-(panel.x+panel.w-right))>1)throw std::runtime_error("Profile padding differs");
 if(w->find_widget<gui2::label>("home_cash").get_label().str()!=(scenario=="home-empty"?"Crowns: 0":"Crowns: 42"))throw std::runtime_error("Live wallet did not refresh");
}
if(scenario=="team-large"||scenario=="team-large-bottom"){if(w->find_widget<gui2::listbox>("court_list").get_item_count()!=10)throw std::runtime_error("Ten-member roster missing");}
if(scenario=="world-teamless"){
 int find_count=0;for(const char* id:{"court_team","court_primary","court_secondary"}){const auto& button=w->find_widget<gui2::button>(id);if(button.get_visible()==gui2::widget::visibility::visible&&button.get_label().str()=="Find Team")++find_count;}
 const auto& create=w->find_widget<gui2::button>("court_secondary");
 if(find_count!=1||create.get_visible()!=gui2::widget::visibility::visible||create.get_label().str()!="New Team")throw std::runtime_error("Teamless World needs one Find Team and New Team");
}
if(scenario=="touch-item"){auto& list=w->find_widget<gui2::listbox>("list");const auto row=list.get_row_grid(0)->get_rectangle();if(row.w<48||row.h<48)throw std::runtime_error("Court item touch target too small");geo<<"item-row "<<row<<"\n";}
if(scenario.rfind("form-",0)==0&&w->find_widget<gui2::label>("court_status").get_label().str()!="Your changes")throw std::runtime_error("Form did not open in place");auto pixels=video::read_pixels();if(!pixels||!SDL_SaveBMP(pixels,("/render/"+phase+".bmp").c_str()))throw std::runtime_error("Capture failed");geo.flush();if(scenario.rfind("form-",0)==0){auto& back=w->find_widget<gui2::button>("court_secondary");back.fire(gui2::event::LEFT_BUTTON_CLICK,back);}w->set_retval(gui2::retval::CANCEL);}};
c::json::array areas(){c::json::array a;for(std::size_t n=0;n<128;++n){const auto& node=alliance_game::court_map::world_nodes[n];a.push_back(c::json::object{{"id",node.id},{"name",node.name},{"enabled",n<8},{"team_count",n<8?12:0},{"revision",1}});}return a;}
c::json::array castles(){c::json::array a;for(std::size_t n=0;n<64;++n){const auto& node=alliance_game::court_map::castle_nodes[n];a.push_back(c::json::object{{"id",std::string("A001-")+node.id},{"name",node.name},{"enabled",n<32},{"role",n==27?"competition":n==9?"plan":"normal"},{"owner_team_name",n%3?"Koichi":"Rival"},{"owner_team_id",n%3?"team-one":"team-two"},{"state",n%4==0?"protected":n%4==1?"fighting":"owned"},{"revision",1}});}return a;}
c::json::object fixture(const std::string& op){c::json::object team{{"id","team-one"},{"name","Koichi"},{"area_id","A001"},{"captain_id","actor"},{"level",3},{"revision",1},{"wallet",c::json::object{{"available",200}}},{"members",c::json::array{c::json::object{{"account_id","actor"},{"username","A very long commander name"},{"status","online"}},c::json::object{{"account_id","second"},{"username","UniRecruit"},{"status","offline"}}}}};
 if(scenario=="team-large"||scenario=="team-large-bottom"){auto members=c::array(team,"members");for(int n=2;n<10;++n)members.push_back(c::json::object{{"account_id","member-"+std::to_string(n)},{"username","CommanderTwentyFourName"+std::to_string(n)},{"status",n%2?"online":"offline"}});team["members"]=members;}
 if(scenario=="form-team"||scenario=="world-teamless")team={};
 if(op=="snapshot")return {{"team",team},{"areas",areas()},{"wallet",c::json::object{{"available",100}}},{"settings",c::json::object{{"revision",1},{"prices",c::json::object{{"hero",100},{"item",5},{"plan",10},{"create_team",50}}}}},{"notifications_unread",2}};
 if(op=="areas")return {{"items",areas()}};
 if(op=="area")return {{"area",c::json::object{{"id","A001"},{"name","Verdant Marches"}}},{"castles",castles()},{"teams",c::json::array{team,c::json::object{{"id","team-two"},{"name","Rival"}}}}};
 if(op=="castle")return {{"castle",castles()[9]}};
 if(op=="my_team")return {{"team",team}};
 if(op=="competitions"){c::json::object event{{"id","event-one"},{"name","Emerald Cup"},{"state","running"},{"revision",1},{"entries",c::json::array{c::json::object{{"team_id","one"},{"team_name","Koichi"}},c::json::object{{"team_id","two"},{"team_name","Rival"}},c::json::object{{"team_id","three"},{"team_name","Northern Watch"}},c::json::object{{"team_id","four"},{"team_name","Silver Legion"}}}}};event["rounds"]=c::json::array{c::json::object{{"number",1},{"matches",c::json::array{c::json::object{{"team_ids",c::json::array{"one","two"}},{"state","completed"},{"winner_team_id","one"}},c::json::object{{"team_ids",c::json::array{"three","four"}},{"state","running"}}}}},c::json::object{{"number",2},{"matches",c::json::array{c::json::object{{"team_ids",c::json::array{"one"}},{"state","waiting"}}}}}};return {{"items",c::json::array{event}}};}
 if(op=="wallet")return {{"wallet",c::json::object{{"available",scenario=="home-empty"?0:42},{"held",10}}}};
 if(op=="shop")return {{"heroes",c::json::array{c::json::object{{"hero_id","deoran"},{"name","Deoran"},{"owned",scenario=="purchase"&&mutations.load()>0}}}},{"items",c::json::array{c::json::object{{"id","healing_draught"},{"name","Healing draught"}},c::json::object{{"id","remedy"},{"name","Remedy"}}}}};
 if(op=="plans"&&scenario=="form-force"){c::json::object match{{"id","match-one"},{"state","scheduled"},{"revision",1}};match["seats"]=c::json::array{c::json::object{{"account_id","actor"},{"ready",false}}};return {{"items",c::json::array{match}}};}
 return {{"items",c::json::array{}}};}
}
void mobile_native_arm(gui2::window& w){if(phase.empty())return;watched=&w;ticks=0;actions=0;armed=SDL_GetTicks();}
namespace alliance_game { bool account_session_interaction_active()noexcept{return false;} }
namespace { class Home:public gui2::dialogs::modal_dialog {
 c::Hooks hooks_;const std::string& window_id()const override{static const std::string id="alliance_mobile_home";return id;}
 void pre_show()override{set_click_dismiss(false);set_enter_disabled(true);home_dispatch.fill(0);for(std::size_t i=0;i<alliance_game::mobile_home::actions.size();++i){auto& button=find_widget<gui2::button>(alliance_game::mobile_home::actions[i].id);button.set_retval(gui2::retval::NONE);gui2::event::connect_signal_mouse_left_click(button,[i](auto&&...){++home_dispatch[i];});}alliance_game::mobile_home::set_profile(*this,scenario=="home-empty"?"UniPlayer":"TwentyFourCharacterNameXY",scenario=="home-empty"?0:117);alliance_game::mobile_home::bind(*this);alliance_game::mobile_home::bind_cash(*this,hooks_,[]{});}
 public:Home(c::Hooks hooks):modal_dialog(alliance_game::mobile_home::prepare_window()),hooks_(hooks){set_show_even_without_video(true);}
 };}
bool auth_render_fixture(game_launcher&){game_config_manager::get()->load_game_config_for_create(true);Monitor monitor;c::Hooks hooks;hooks.journal=false;hooks.actor_id="actor";hooks.generation="generation";hooks.same_actor=[] {return true;};hooks.heroes={c::json::object{{"id","deoran"},{"name","Deoran"},{"forms",c::json::array{c::json::object{{"id","deoran-one"},{"name","Cavalryman"}}}}}};hooks.request=[](bool mutation,const std::string& op,c::json::object payload,alliance::CancellationToken&){if(mutation){if(scenario=="plan-submit"){const auto& settings=c::object(payload,"settings");if(op!="create_plan"||c::number(settings,"base_income")!=7||c::number(settings,"turns")!=55||!c::flag(settings,"fog")||!c::flag(settings,"shroud")||!c::flag(settings,"observers")||!c::flag(settings,"items")||c::number(settings,"hero_cap")!=15||c::number(settings,"size")!=1)throw std::runtime_error("Plan settings payload differs");}else if(scenario!="purchase"||op!="buy_hero"||c::text(payload,"hero_id")!="deoran"||c::text(payload,"confirmation")!="confirmed"||c::number(payload,"price_revision")!=1||c::text(payload,"command_id").size()!=36)throw std::runtime_error("Wrong reviewed purchase payload");++mutations;}return c::Reply{true,{},{},{{"version",1},{"actor_id","actor"},{"database_generation","generation"},{"server_time","2026-11-01T10:00:00Z"},{"data",fixture(op)}}};};
int captures=0;
for(const std::string theme:{"default","modern"}){gui2::current_gui=gui2::guis.find(theme);gui2::current_gui->second.activate();
 for(const auto& name:{"map-controls","motion-world","motion-area","stress","tap-world","tap-area","tap-inactive","world","world-teamless","team-large","team-large-bottom","pinch","area","castle","team","shop","home","home-empty","form-hero","form-cash","form-team","form-plan","form-settings","form-settings-bottom","form-force","competition","purchase","plan-submit","help-world","help-items","touch-item"}){scenario=name;initial_maps.clear();motion_step=0;awaiting=false;last_frame={};if(const auto* subset=std::getenv("COURT_RENDER_SUBSET");subset&&std::string(subset)!=""&&std::string(subset).find("|"+scenario+"|")==std::string::npos)continue;phase=theme+"-"+name;mutations=0;
 if(scenario=="home"||scenario=="home-empty"){Home home(hooks);home.show();++captures;continue;}
 if(scenario.rfind("help-",0)==0){if(scenario=="help-world")alliance_game::mobile_help::show();else {alliance_game::MenuThemeScope scope("alliance_court_help");auto manager=help::help_manager::get_instance();auto root=manager->regenerate();root.sections.push_front(alliance_game::mobile_help::topics());gui2::dialogs::help_browser::display(root,"court_items");}++captures;continue;}
 if(scenario=="touch-item"){std::vector<config> original{config{"id","recruit"},config{"id","recall"}},items{config{"id","recruit","label","Recruit"}};auto expand=[](auto& rows){rows.emplace_back("id","wml_menu:court_item_1","label","Use Healing Draught","icon","items/potion-red.png");rows.emplace_back("id","wml_menu:court_item_9");rows.emplace_back("id","wml_menu:unrelated");};auto allowed=[](const auto&){return true;};alliance_game::mobile_actions::append(original,items,false,expand,allowed);assert(items.size()==2&&items.front()["id"].str()=="wml_menu:court_item_1");alliance_game::mobile_actions::append(original,items,false,expand,allowed);assert(items.size()==2);auto empty=std::vector<config>{};alliance_game::mobile_actions::append(original,empty,true,expand,allowed);assert(empty.empty());alliance_game::mobile_actions::append({},empty,false,expand,allowed);assert(empty.empty());alliance_game::mobile_actions::append(original,empty,false,expand,[](const auto&){return false;});assert(empty.empty());alliance_game::MenuThemeScope scope("alliance_court_actions");gui2::dialogs::drop_down_menu menu(rect{400,150,1,1},items,-1,true,false);menu.show();++captures;continue;}
 c::State state;if(scenario=="area"||scenario=="castle"||scenario=="form-plan"||scenario=="form-settings"||scenario=="form-settings-bottom"||scenario=="plan-submit"||scenario=="form-force"){state.depth=scenario=="area"?1:2;state.area="A001";state.castle="A001-C010";}
 if(scenario=="team"||scenario=="team-large"||scenario=="team-large-bottom")state.view="my_team";if(scenario=="competition"){state.depth=2;state.area="A001";state.castle="A001-C028";state.view="competitions";}
 if(scenario=="shop"||scenario=="form-hero"||scenario=="form-cash"||scenario=="purchase")state.view="shop";
 if(scenario=="form-team")state.view="teams";
 if(scenario=="form-plan"||scenario=="form-settings"||scenario=="form-settings-bottom"||scenario=="plan-submit"||scenario=="form-force")state.view="plans";
 if(scenario=="motion-area"||scenario=="tap-area"){state.depth=1;state.area="A001";}active_state=&state;c::Workspace dialog(hooks,state);dialog.accept_snapshot("snapshot",fixture("snapshot"));dialog.accept_snapshot("areas",fixture("areas"));dialog.accept_snapshot("area",fixture("area"),{{"area_id","A001"}});dialog.accept_snapshot("castle",fixture("castle"),{{"castle_id","A001-C010"}});dialog.show();++captures;
 }}
 std::cout<<"COURT REAL NATIVE RENDER PASS: "<<captures<<" captures\n";return true;}

void citadel_native_arm(gui2::window& w){mobile_native_arm(w);}

namespace alliance_game::court_mobile {
void court_mobile_frame_probe(const Viewport& view){
 if(active_state&&active_state->map&&active_state->view.empty()&&active_state->depth<2&&view.bracket.empty()&&initial_maps.insert(view.area).second){
  const auto a=view.camera->project({0,0}),b=view.camera->project({1,1});
  geo<<phase<<" initial-width-fit "<<(view.area.empty()?"World":view.area)<<" viewport "<<view.w<<'x'<<view.h<<" artwork-width "<<b.x-a.x<<" zoom "<<view.camera->zoom<<'\n';
  if(std::abs(b.x-a.x-view.w)>1e-5||std::abs(a.x)>1e-5)throw std::runtime_error("First map frame does not fill viewport width");
 }
 if(view.frames==1)for(const auto& marker:view.markers){auto p=view.camera->project({marker.node->x,marker.node->y});auto ids=view.hit(p);if(std::find(ids.begin(),ids.end(),marker.id)==ids.end())throw std::runtime_error("Location is not selectable at its centre: "+marker.id);}
 if(scenario.rfind("motion-",0)!=0)return;auto now=Clock::now();
 const double latency=awaiting?std::chrono::duration<double,std::milli>(now-input_at).count():-1;
 const double interval=last_frame==Clock::time_point{}?0:std::chrono::duration<double,std::milli>(now-last_frame).count();last_frame=now;
 frames<<phase<<','<<motion_step<<','<<view.frames<<','<<view.texture_builds<<','<<latency<<','<<interval<<','<<view.camera->cx<<','<<view.camera->cy<<','<<view.camera->zoom<<'\n';awaiting=false;
 if(motion_step==5||motion_step==6||motion_step==40||motion_step==41||motion_step==55){auto pixels=video::read_pixels();if(!SDL_SaveBMP(pixels,("/render/"+phase+"-"+std::to_string(motion_step)+".bmp").c_str()))throw std::runtime_error("Frame capture failed");}
}
}
