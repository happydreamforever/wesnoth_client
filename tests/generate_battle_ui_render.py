"""Generate a real GUI2/SDL render probe from the production battle browser.

Run inside the existing pinned native-engine verification environment. The
output folder must be mounted as /render for screenshots and geometry. Compile
render_probe.cpp, render_wesnoth.cpp, team_views.cpp and hero_catalog.cpp against
the completed native engine objects (excluding its original wesnoth.cpp object).
Run the resulting engine with --windowed --resolution 1280x720 (and1920x1080).
Network/account boundaries are fixture data; this is a rendering/input test.
"""
from pathlib import Path
import argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
root=Path(__file__).resolve().parents[1];out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
extra=r'''
#include <boost/json/src.hpp>
#include "serialization/parser.hpp"
#include "alliance_game/hero_unit_data.inc"
#include "events.hpp"
#include "tests/battle_ui_team_render_cases.hpp"
#include "game_launcher.hpp"
#include "video.hpp"
#include "SDL3/SDL_surface.h"
#include <fstream>
#include <iostream>
namespace {
gui2::window* watched=nullptr;int ticks=0,step=0;std::string phase,filter_text,action_id="cancel";bool done_filter=false;
std::ofstream geometry("/render/geometry.txt");
void arm(gui2::window& w){watched=&w;ticks=0;done_filter=false;}
class Monitor:public events::pump_monitor { void process() override {
 if(!watched)return;
 if(++ticks==3&&!filter_text.empty()&&!done_filter){auto& f=watched->find_widget<gui2::text_box>("battle_search");f.set_value(filter_text);f.fire(gui2::event::NOTIFY_MODIFIED,f,nullptr);done_filter=true;}
 if(ticks==5&&phase=="02-search"){
 auto& list=watched->find_widget<gui2::listbox>("battle_list");if(list.get_selected_row()!=0)throw std::runtime_error("Search lost original row identity");
 SDL_Event key{};key.type=SDL_EVENT_KEY_DOWN;key.key.key=SDLK_RETURN;key.key.scancode=SDL_SCANCODE_RETURN;SDL_PushEvent(&key);
 }
 if(ticks<12)return;
 if(phase=="03-empty-search"&&(watched->find_widget<gui2::listbox>("battle_list").get_selected_row()!=-1||watched->find_widget<gui2::button>("battle_action_0").get_active()))throw std::runtime_error("Empty search enabled a selection action");
 auto* w=watched;watched=nullptr;
 const auto bound=w->get_rectangle();if(bound.x<0||bound.y<0||bound.x+bound.w>video::game_canvas_size().x||bound.y+bound.h>video::game_canvas_size().y)throw std::runtime_error("Window outside screen");geometry<<phase<<" window "<<bound.x<<' '<<bound.y<<' '<<bound.w<<' '<<bound.h<<'\n';
 for(const char* id:{"battle_list","battle_search","detail_title","detail_subtitle","detail_text","detail_image","detail_map","detail_progress","battle_action_0","battle_action_2","battle_action_8","battle_action_9","cancel"}){
 auto* child=w->find(id,false);if(!child||child->get_visible()!=gui2::widget::visibility::visible)continue;auto r=child->get_rectangle();geometry<<phase<<' '<<id<<' '<<r.x<<' '<<r.y<<' '<<r.w<<' '<<r.h<<'\n';if(r.x<bound.x||r.y<bound.y||r.x+r.w>bound.x+bound.w||r.y+r.h>bound.y+bound.h)throw std::runtime_error("Widget outside window");}
 auto pixels=video::read_pixels();if(!pixels||!SDL_SaveBMP(pixels,("/render/"+phase+".bmp").c_str()))throw std::runtime_error("Capture failed");geometry.flush();
 auto& button=w->find_widget<gui2::button>(action_id);button.fire(gui2::event::LEFT_BUTTON_CLICK,button);
 }};
}
'''
src=(root/'alliance_game/battle_ui.cpp').read_text();anchor='namespace alliance_game {'
src=src.replace(anchor,extra+'\n'+anchor,1).replace('BattleDialog dialog(view);dialog.show();return dialog.result;','BattleDialog dialog(view);arm(dialog);dialog.show();return dialog.result;')
src+=r'''
bool auth_render_fixture(game_launcher&) {
 using namespace alliance_game;
 Monitor monitor;game_config_manager::get()->load_game_config_for_create(true);
 auto units=game_config_manager::get()->game_config().merged_children_view("units");
 static const auto owned_units=io::read(std::string(hero_units_wml));units.data().push_back(owned_units);unit_types.set_config(units);
 BattleView heroes;heroes.title="Your heroes";heroes.subtitle="Campaign heroes • Earned forms and permanent progression";heroes.selected_action=true;heroes.actions={{"Choose form"}};
 for(int i=0;i<12;++i)heroes.rows.push_back({i==0?"Konrad":"Veteran "+std::to_string(i),"Level 12 • 3 earned forms","BATTLE STRENGTH\nHit points: 62\n\nSword   8 × 4   •   blade / melee\nBow   7 × 3   •   pierce / ranged\n\nGROWTH\nEarn experience through story campaigns and confirmed team victories. Choose a form to inspect its abilities before entering battle.",battle_unit_image(i%2?"Elvish Fighter":"Spearman"),"",42,"Experience: 180 / 735"});
 phase="01-heroes";auto result=show_battle_view(heroes);if(result.action!=-1)throw std::runtime_error("Back invoked action");
 phase="02-search";filter_text="Konrad";show_battle_view(heroes);
 phase="03-empty-search";filter_text="unmatched name";show_battle_view(heroes);filter_text.clear();
 BattleView plan;plan.title="Isar's Cross • Team battle";plan.subtitle="Draft • Koichi vs RivalPlan • 2 / 4 players ready";
 plan.rows.push_back({"Battle overview","Both captains approved • Waiting for two players","Choose your hero, then set yourself Ready. Practice is available when you are Ready; a shared battle waits for every selected player.\n\nKoichi\nSide 1: UniPlayer — Konrad — Ready\nSide 4: UniRecruit — Delfador — Not ready\n\nRivalPlan\nSide 2: UniSolo — Kalenz — Not ready\nSide 3: UniRival — Konrad — Ready","icons/icon-multiplayer.png",battle_map_preview("multiplayer_Isars_Cross")});
 for(int i=1;i<=8;++i)plan.rows.push_back({"Side "+std::to_string(i)+" • PlayerNameLong","Koichi • Hero level 12 • Not ready","Only the owner of this side may change its hero and readiness.\n\nHP: 62\nAttack: 8 × 4\n\nCaptain orders: Hold the northern villages and support the river crossing.",battle_unit_image("Elvish Fighter"),""});
 plan.actions={{"Choose hero"},{"Set myself ready"},{"Captain approval"},{"Practice"},{"Join shared battle",false},{"Captain orders"},{"Review result",false},{"Replay",false},{"Refresh plan"},{"Cancel plan"}};
 phase="04-plan";show_battle_view(plan);
 plan.selected=8;phase="05-last-seat";show_battle_view(plan);
 BattleView empty;empty.title="Teams";empty.subtitle="Find your team or begin a new alliance";empty.actions={{"Browse teams"},{"Create team"},{"Applications"}};phase="06-empty-team";show_battle_view(empty);
 action_id="battle_action_0";heroes.selected=3;phase="07-action";result=show_battle_view(heroes);if(result.action!=0||result.row!=3)throw std::runtime_error("Selection was not preserved");
 action_id="cancel";for(auto& entry:team_render_cases()){phase=entry.first;show_battle_view(entry.second);}
 std::cout<<"BATTLE UI REAL RENDER CHECK PASSED\n";return true;
}
'''
(out/'render_probe.cpp').write_text(src)
main=(root/'engine/src/wesnoth.cpp').read_text()
include='#include "game_launcher.hpp"'
anchor='\t\tif(game->play_test() == false) {'
if main.count(include)!=1 or main.count(anchor)!=1:raise RuntimeError('Unexpected pinned main hook')
main=main.replace(include,'extern bool auth_render_fixture(class game_launcher&);\n'+include).replace(anchor,'\t\tif(auth_render_fixture(*game)) return 0;\n'+anchor)
(out/'render_wesnoth.cpp').write_text(main)
