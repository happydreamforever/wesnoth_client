"""Compile-time fixture: production GUI2 layouts, native SDL rendering, no service calls."""
from pathlib import Path
import argparse
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);args=p.parse_args()
root=Path(__file__).resolve().parents[1];out=args.output;out.mkdir(parents=True,exist_ok=True)
head=r'''
#include <boost/json.hpp>
#include "events.hpp"
#include "draw.hpp"
#include "draw_manager.hpp"
#include "picture.hpp"
#include "gui/core/top_level_drawable.hpp"
#include "desktop/clipboard.hpp"
#include "font/constants.hpp"
#include "log.hpp"
#include "serialization/unicode.hpp"
#include "serialization/parser.hpp"
#include "alliance_game/hero_unit_data.inc"
#include "alliance_native/account_protocol.hpp"
#include "gui/dialogs/campaign_selection.hpp"
#include "game_initialization/create_engine.hpp"
#include "saved_game.hpp"
#include "map/map.hpp"
#include "gui/widgets/tree_view_node.hpp"
#include "gui/widgets/menu_button.hpp"
#include "gui/widgets/multimenu_button.hpp"
#include "gui/widgets/toggle_button.hpp"
#include "gui/widgets/window.hpp"
#include "game_launcher.hpp"
#include "alliance_game/hero_presentation.hpp"
#include "video.hpp"
#include "SDL3/SDL_surface.h"
#include <fstream>
#include <iostream>
#include <stdexcept>
namespace {gui2::window* watched=nullptr;int ticks=0;std::string phase,click="cancel";
std::ofstream geometry("/render/geometry.txt");
void arm(gui2::window& w){watched=&w;ticks=0;}
class Monitor:public events::pump_monitor {void process()override{
 if(!watched)return;++ticks;
 if(ticks==3&&phase=="02-login"){SDL_Event text{};text.type=SDL_EVENT_TEXT_INPUT;text.text.text="RenderPlayer";SDL_PushEvent(&text);}
 if(ticks==5&&phase=="02-login"){watched->keyboard_capture(&watched->find_widget<gui2::text_box>("password"));SDL_Event text{};text.type=SDL_EVENT_TEXT_INPUT;text.text.text="FixtureSecret42!";SDL_PushEvent(&text);}
 if(ticks==7&&phase=="02-login"){auto& eye=watched->find_widget<gui2::toggle_button>("remember_password");eye.fire(gui2::event::LEFT_BUTTON_CLICK,eye);if(watched->find_widget<gui2::text_box>("password").get_value()!="FixtureSecret42!")throw std::runtime_error("Eye control changed typed password");eye.fire(gui2::event::LEFT_BUTTON_CLICK,eye);}
 if(ticks==3&&(phase=="08-campaign"||phase=="23-campaign-celes"))watched->find_widget<gui2::tree_view_node>("Heir 2 The Throne").select_node();
 if(ticks==3&&(phase=="09-hero-search"||phase=="12-refumee")){auto& f=watched->find_widget<gui2::text_box>("hero_search");f.set_value(phase=="12-refumee"?"Refumee":"Thea");f.fire(gui2::event::NOTIFY_MODIFIED,f,nullptr);}
 if(ticks==3&&phase=="10-hero-empty"){auto& f=watched->find_widget<gui2::text_box>("hero_search");f.set_value("no-such-hero");f.fire(gui2::event::NOTIFY_MODIFIED,f,nullptr);}
 if(ticks==3&&phase=="11-hero-page"){auto& b=watched->find_widget<gui2::button>("hero_next");b.fire(gui2::event::LEFT_BUTTON_CLICK,b);}
 if(ticks==5&&phase=="09-hero-search"){SDL_Event key{};key.type=SDL_EVENT_KEY_DOWN;key.key.key=SDLK_RETURN;key.key.scancode=SDL_SCANCODE_RETURN;SDL_PushEvent(&key);}
 if(ticks==3&&phase=="17-plan-input"){
  auto& kind=watched->find_widget<gui2::menu_button>("kind_1");kind.set_selected(2);kind.fire(gui2::event::NOTIFY_MODIFIED,kind,nullptr);
  auto& ai=watched->find_widget<gui2::menu_button>("occupant_1");ai.set_selected(42);ai.fire(gui2::event::NOTIFY_MODIFIED,ai,nullptr);
  watched->find_widget<gui2::text_box>("gold").set_value("250");watched->find_widget<gui2::text_box>("base_income").set_value("20");
  auto& fog=watched->find_widget<gui2::toggle_button>("fog");fog.fire(gui2::event::LEFT_BUTTON_CLICK,fog);
 }
 if(ticks==3&&phase=="18-room-input"){
  watched->find_widget<gui2::text_box>("turns").set_value("0");auto& button=watched->find_widget<gui2::button>("publish");button.fire(gui2::event::LEFT_BUTTON_CLICK,button);
 }
 if(ticks==6&&phase=="18-room-input"){
  if(watched->find_widget<gui2::label>("room_error").get_label().str().find("Turns 1–1000")==std::string::npos)throw std::runtime_error("Invalid turn limit was accepted");
  watched->find_widget<gui2::text_box>("turns").set_value("Unlimited");watched->find_widget<gui2::text_box>("income").set_value("0");
 }
 if(ticks==3&&phase=="19-room-filters")watched->find_widget<gui2::listbox>("battle_list").select_row(2);
 if(ticks==3&&phase=="20-room-capacity")watched->find_widget<gui2::listbox>("battle_list").select_row(7);
 if(ticks<12)return;auto* w=watched;watched=nullptr;
 if(phase=="02-login"&&(w->find_widget<gui2::text_box>("user_name").get_value()!="RenderPlayer"||w->find_widget<gui2::text_box>("password").get_value().empty()||w->find_widget<gui2::text_box>("password").get_value()=="FixtureSecret42!"))throw std::runtime_error("Login input focus or password masking failed");
 if(phase=="03-signup"&&w->find_widget<gui2::button>("signup").get_label()!="Back")throw std::runtime_error("Signup Back label mismatch");
 if(phase=="09-hero-search"&&w->find_widget<gui2::label>("hero_name").get_label()!="Thea")throw std::runtime_error("Search lost original hero identity");
 if(phase=="10-hero-empty"&&w->find_widget<gui2::button>("hero_use").get_active())throw std::runtime_error("Empty search permits selection");
 const auto box=w->get_rectangle();if(box.x<0||box.y<0||box.x+box.w>video::game_canvas_size().x||box.y+box.h>video::game_canvas_size().y)throw std::runtime_error("Window exceeds viewport: "+phase);
 geometry<<phase<<" window "<<box.x<<' '<<box.y<<' '<<box.w<<' '<<box.h<<'\n';
 for(const auto* id:{"home_title","plan_title","room_title","battle_title","battle_action_0","battle_action_7","campaign","multiplayer","community","load","addons","achievements","editor","preferences","quit","user_name","password","confirmation","ok","signup","cancel","plan_map","plan_minimap","kind_8","occupant_8","hero","ready","approve","save","maps","room_map","publish","remember_password","fog","shroud","observers","card_view_0","hero_use","hero_next","hero_search","hero_form","campaign_heroes","filter_box","sort_name","sort_time","filter_completion","mods_menu","rng_menu","difficulty_menu","proceed"}){
  auto* c=w->find(id,false);if(!c||c->get_visible()!=gui2::widget::visibility::visible)continue;const auto r=c->get_rectangle();geometry<<phase<<' '<<id<<' '<<r.x<<' '<<r.y<<' '<<r.w<<' '<<r.h<<'\n';
  if(r.x<box.x||r.y<box.y||r.x+r.w>box.x+box.w||r.y+r.h>box.y+box.h)throw std::runtime_error("Control exceeds window: "+phase+" "+id);
  if((dynamic_cast<gui2::button*>(c)||dynamic_cast<gui2::text_box*>(c)||dynamic_cast<gui2::toggle_button*>(c)||dynamic_cast<gui2::menu_button*>(c)||dynamic_cast<gui2::multimenu_button*>(c))&&r.h<48)throw std::runtime_error("Small action target: "+phase+" "+id);
 }
 auto pixels=video::read_pixels();if(!pixels||!SDL_SaveBMP(pixels,("/render/"+phase+".bmp").c_str()))throw std::runtime_error("Screenshot failed");geometry.flush();
 const auto action=click;if(phase=="19-room-filters"){phase="20-room-capacity";click="battle_action_0";}else if(phase=="20-room-capacity"){phase="21-room-filtered";click="battle_action_1";}
 if(auto* b=w->find_widget<gui2::button>(action,false,false))b->fire(gui2::event::LEFT_BUTTON_CLICK,*b);else w->set_retval(gui2::retval::CANCEL);
}};
class Probe:public gui2::dialogs::modal_dialog {
 std::string id_;bool login_;
 const std::string& window_id()const override{return id_;}
 void pre_show()override{if(login_){find_widget<gui2::label>("auth_title").set_label(phase=="02-login"?"Welcome back":"Create account");
  find_widget<gui2::label>("login_label").set_label(phase=="02-login"?"":"Choose a username and a password of 12–128 characters.");
  find_widget<gui2::label>("confirmation_label").set_visible(phase=="02-login"?gui2::widget::visibility::invisible:gui2::widget::visibility::visible);
  find_widget<gui2::text_box>("confirmation").set_visible(phase=="02-login"?gui2::widget::visibility::invisible:gui2::widget::visibility::visible);}
  arm(*this);}
 public:Probe(const std::string& id,bool login=false):modal_dialog(id),id_(id),login_(login){}
};
}
'''
s=(root/'alliance_game/battle_ui.cpp').read_text().replace('#include "alliance_game/hero_gallery.inc"',(root/'alliance_game/hero_gallery.inc').read_text().replace('void pre_show()override {','void pre_show()override {arm(*this);'));s=s.replace('void pre_show() override\n    {','void pre_show() override\n    {arm(*this);');s=s.replace('namespace alliance_game {',head+'\nnamespace alliance_game {',1)
s+='\n'+(root/'alliance_game/team_views.cpp').read_text().replace('#include "alliance_game/team_workspace.inc"',(root/'alliance_game/team_workspace.inc').read_text().replace('void pre_show()override {','void pre_show()override {arm(*this);').replace('void pre_show()override{','void pre_show()override{arm(*this);'))
room=(root/'alliance_game/individual_creation.inc').read_text().split('bool create_individual_room(')[0].replace('void pre_show()override {','void pre_show()override {arm(*this);')
s+='\nnamespace alliance_game {\n'+room+'\n}\n'
account=(root/'alliance_game/account_ui.cpp').read_text()
credentials=account[account.index('class CredentialPasswordBox final'):account.index('int credential_action(')]
cards=account[account.index('BattleRow hero_form_card('):account.index('BattleView hero_collection_view(')]+account[account.index('std::vector<HeroGalleryEntry> gallery_entries('):account.index('bool choose_hero(')]
credentials+=cards
credentials=credentials.replace('void pre_show() override {','void pre_show() override {arm(*this);')
s+='\nnamespace alliance_game { namespace { namespace account=alliance::account; void clear_secret(std::string& s){s.clear();}\n'+credentials+'\n} void show_campaign_heroes(const std::string&){} }\nvoid arm_campaign(gui2::window& window){arm(window); }\n'
individual=(root/'alliance_game/individual_rooms.cpp').read_text()
filter_functions=individual[individual.index('int individual_filter_token('):individual.rfind('\n}')]
choose=individual[individual.index('int choose('):individual.index('std::string field(')]
s+='\nnamespace alliance_game { namespace { int filter_gold=0,filter_turns=0,filter_capacity=0;\n'+choose+'\n}\n'+filter_functions+'\n}\n'
s+=r'''
bool auth_render_fixture(game_launcher&) {
 using namespace alliance_game;Monitor monitor;game_config_manager::get()->load_game_config_for_create(true);
 auto units=game_config_manager::get()->game_config().merged_children_view("units");static const auto owned_units=io::read(std::string(hero_units_wml));units.data().push_back(owned_units);unit_types.set_config(units);
 phase="01-home";click="quit";Probe home(prepare_home_window());home.show();
 click="cancel";{AuthenticationBackdrop backdrop;std::string username,password;phase="02-login";{CredentialsDialog login(username,password,"",false,true);login.show();if(login.get_retval()!=13)throw std::runtime_error("Outer X did not return distinct Quit action");}
 phase="03-signup";click="signup";{CredentialsDialog signup(username,password,"",true,true);signup.show();if(signup.get_retval()!=11)throw std::runtime_error("Signup Back did not return navigation action");}}click="cancel";

 alliance::account::Team a;a.id="a";a.name="Northern Guard";a.capacity=8;a.area_name="Northern Marches";a.level=3;a.experience=120;a.rating=1000;a.captain_id="captain";a.members={{"captain","Captain",3},{"member","Ranger",4}};
 alliance::account::Team b;b.id="b";b.name="Silver Legion";b.captain_id="rival";b.members={{"rival","Rival",3}};
 alliance::account::BattleMap map;map.id="mokena-prairie";map.name="Mokena Prairie";map.scenario_id="multiplayer_8p_Mokena_Prairie";map.size=4;map.sides={{1,3,5,8},{2,4,6,7}};
 TeamPlanDraft draft;draft.map_id=map.id;draft.settings.observers=false;
 phase="04-team-editor";edit_team_plan({map},{a,b},"captain",draft);
 alliance::account::Battle battle;battle.format=2;battle.ranked=false;battle.id="fixture";battle.state="draft";battle.validity="valid";battle.map=map;battle.settings.observers=false;
 alliance::account::BattleTeam ta;ta.id=a.id;ta.name=a.name;ta.captain_id=a.captain_id;ta.slots={{1,"reserved","captain",{},{}},{3,"ai",{},"Rebels","Elvish Captain"},{5,"open",{},{},{}},{8,"closed",{},{},{}}};
 alliance::account::BattleTeam tb;tb.id=b.id;tb.name=b.name;tb.captain_id=b.captain_id;tb.slots={{2,"open",{},{},{}},{4,"open",{},{},{}},{6,"ai",{},"Drakes","Drake Flare"},{7,"closed",{},{},{}}};battle.teams={ta,tb};
 phase="05-team-workspace";show_team_plan_workspace(battle,"captain","a");
 phase="06-room-create";saved_game room_state;room_state.classification().type=campaign_type::type::multiplayer;auto room_maps=room_creation_maps(room_state);if(room_maps.size()<3)throw std::runtime_error("Missing pinned room maps");RoomCreationDialog room(room_maps);room.show();
 // The production game_config_manager overlay appends portable hero units on
 // every config refresh. Mirror that after room-map loading in this fixture,
 // whose linked baseline manager intentionally remains unmodified.
 units=game_config_manager::get()->game_config().merged_children_view("units");units.data().push_back(owned_units);unit_types.set_config(units);
 alliance::account::Hero owned;owned.hero_id="abhai";owned.name=hero_name(owned.hero_id);owned.hero_level=4;owned.experience=18;owned.total_experience=198;
 for(const auto& form:hero_forms())if(form.hero_id==owned.hero_id)owned.forms.push_back(form.id);
 if(owned.forms.empty()||!unit_types.find(find_hero_form(owned.hero_id,owned.forms.front())->unit_type))throw std::runtime_error("Missing real Abhai native form");auto heroes=gallery_entries({owned});
 phase="07-heroes";show_hero_gallery(heroes);
 phase="09-hero-search";show_hero_gallery(heroes);
 phase="10-hero-empty";show_hero_gallery(heroes,true);
 phase="11-hero-page";show_hero_gallery(heroes);
 phase="12-refumee";show_hero_gallery(heroes);
 alliance::account::BattlePlayer participant;participant.id="captain";participant.username="Captain";participant.side=1;participant.ready=true;participant.selection_present=true;participant.hero_id=owned.hero_id;participant.form_id=owned.forms.front();battle.teams[0].players={participant};
 battle.state="running";phase="13-team-results";show_team_plan_workspace(battle,"captain","a",{a,b},true,true);
 battle.settings.observers=true;phase="14-team-watch";show_team_plan_workspace(battle,"member","a",{a,b});
 phase="15-team-hub";auto roster=team_roster_view(a,"captain");roster.title="Teams — "+a.name;roster.actions={{"Plans"},{"History"},{"Roster"},{"New battle"},{"More"},{"Refresh"}};show_battle_view(roster);
 phase="22-team-more";roster.title="Team options";roster.actions={{"Captain orders"},{"Applications"},{"Find teams"},{"Area rankings"},{"Manage team"}};show_battle_view(roster);
 phase="16-team-roster";for(int n=2;n<8;++n)a.members.push_back({"member"+std::to_string(n),"Northern Ranger "+std::to_string(n),n});show_battle_view(team_roster_view(a,"captain"));
 phase="17-plan-input";click="save";if(!edit_team_plan({map},{a,b},"captain",draft))throw std::runtime_error("Plan save did not accept");
 if(draft.settings.gold!=250||draft.settings.base_income!=20||draft.settings.turns!=-1||draft.settings.fog)throw std::runtime_error("Plan settings not preserved");
 const auto ai=std::find_if(draft.slots.begin(),draft.slots.end(),[](const auto& slot){return slot.side==1;});if(ai==draft.slots.end()||ai->kind!="ai"||ai->ai_faction!="Dunefolk"||ai->ai_leader!="Dune Apothecary")throw std::runtime_error("AI faction/leader not preserved");
 phase="18-room-input";click="publish";RoomCreationDialog input_room(room_maps);if(!input_room.show()||input_room.turn_limit!=-1||input_room.income!=0)throw std::runtime_error("Room settings not preserved");
 phase="19-room-filters";click="battle_action_0";if(!edit_individual_filters())throw std::runtime_error("Capacity filter was not applied");
 config filtered;filtered["room_capacity"]=8;if(!individual_filter_settings(&filtered)||individual_filter_token(&filtered,"capacity:8")!=1)throw std::runtime_error("Capacity filter rejected matching room");filtered["room_capacity"]=2;if(individual_filter_settings(&filtered)||individual_filter_token(&filtered,"capacity:8")!=0)throw std::runtime_error("Capacity filter accepted other room");reset_individual_filters();
 click="cancel";phase="08-campaign";saved_game state;ng::create_engine engine(state);gui2::dialogs::campaign_selection campaigns(engine);campaigns.show();
 const auto previous_theme=gui2::current_gui;gui2::current_gui=gui2::guis.find("modern");if(gui2::current_gui==gui2::guis.end())throw std::runtime_error("Missing pinned Celes theme");gui2::current_gui->second.activate();
 phase="23-campaign-celes";gui2::dialogs::campaign_selection celes_campaigns(engine);celes_campaigns.show();gui2::current_gui=previous_theme;gui2::current_gui->second.activate();
 std::cout<<"PRODUCT UI REAL RENDER PASS\n";return true;
}
'''
(out/'render_probe.cpp').write_text(s)
main=(root/'engine/src/wesnoth.cpp').read_text()
anchor='\t\tif(game->play_test() == false) {'
main=main.replace('#include "game_launcher.hpp"','extern bool auth_render_fixture(class game_launcher&);\n#include "game_launcher.hpp"').replace(anchor,'\t\tif(auth_render_fixture(*game)) return 0;\n'+anchor)
(out/'render_wesnoth.cpp').write_text(main)

import sys
sys.path.insert(0,str(root))
from alliance_game.campaign_selection_overlay import generate_campaign_selection_overlay
campaign=generate_campaign_selection_overlay((root/'engine/src/gui/dialogs/campaign_selection.cpp').read_bytes()).decode()
campaign=campaign.replace('void campaign_selection::pre_show()\n{','void campaign_selection::pre_show()\n{\n    ::arm_campaign(*this);').replace('#include "alliance_game/campaign_rewards.hpp"','#include "alliance_game/campaign_rewards.hpp"\nextern void arm_campaign(gui2::window&);')
(out/'render_campaign.cpp').write_text(campaign)
