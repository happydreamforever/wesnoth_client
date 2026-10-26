"""Execute production Team acknowledgement and admission guards with scripted transport."""
from pathlib import Path
import subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
def extract(source,name):
 start=source.index(name);opening=source.index('\n{',start)+1;end=opening+1;depth=1
 while depth:depth+=(source[end]=='{')-(source[end]=='}');end+=1
 return source[start:end]
class PlanUi(unittest.TestCase):
 def test_current_actor_revision_slot_settings_and_admission(self):
  source=(ROOT/'alliance_game/account_ui.cpp').read_text()
  production='\n'.join(extract(source,n) for n in ('bool plan_acknowledges(', 'bool battle_mutation(', 'void enter_flexible_plan('))
  production+='\n'+extract((ROOT/'alliance_game/team_extension_ui.inc').read_text(),'void download_shared_replay(')+'\n'+extract(source,'bool flexible_plan_action(')
  fixture=r'''
#include "alliance_native/account_client.hpp"
#include "alliance_game/shared_launch.hpp"
#include "alliance_game/team_views.hpp"
#include "alliance_game/replay_launch.hpp"
#include <algorithm>
#include <functional>
#include <iostream>
#include <stdexcept>
namespace account=alliance::account;
namespace alliance_game {
void need(bool v){if(!v)throw std::runtime_error("assertion");}
struct Session{std::string token="token";account::Profile profile;}session;
Session& current_session(){return session;}
account::Reply response;std::function<void()> race;int queued=0;SharedAdmission admission;
void message(const std::string&){}void show_failure(const account::Reply&){}
bool request(const alliance::TrustedProfile&,account::Request&,const std::string&,account::Reply& output){output=response;if(race)race();return true;}
bool same_extension_actor(const std::string& actor){return !session.token.empty()&&session.profile.account_id==actor;}
void queue_shared(SharedAdmission a){++queued;admission=std::move(a);}
bool detail_allowed=true,directory_available=true;int roster_calls=0,workspace_calls=0;bool seen_terminal=false,seen_pending=false;
struct LocalRecord {std::string run_id,status;};std::vector<LocalRecord> records;
std::vector<LocalRecord> inspect_pending_shared(const std::string&){return records;}
int select_change(const std::string&,const std::string&,const std::vector<std::string>&){return 0;}
bool team_request(const alliance::TrustedProfile&,account::Request& request,const std::string&,account::Reply& output){if(!detail_allowed)return false;output=response;if(request.operation=="my_team"){account::Team team;team.id="a";output.team=team;}return true;}
bool load_plan_teams(const alliance::TrustedProfile&,const account::Battle&,std::vector<account::Team>&){++roster_calls;return directory_available;}
TeamPlanActionResult show_team_plan_workspace(const account::Battle&,const std::string&,const std::string&,const std::vector<account::Team>&,bool terminal,bool pending){++workspace_calls;seen_terminal=terminal;seen_pending=pending;return {};}
bool edit_team_plan(const std::vector<account::BattleMap>&,const std::vector<account::Team>&,const std::string&,TeamPlanDraft&){return false;}
bool choose_hero(const alliance::TrustedProfile&,std::string&,std::string&){return false;}
void shared_result_review(const alliance::TrustedProfile&,const account::Battle&){}
void join_shared_battle(const alliance::TrustedProfile&,const account::Battle&,bool){}
bool shared_pending()noexcept{return queued>0;}
std::string verified_shared_replay(const std::string&,const std::string&){throw std::runtime_error("No local replay");}
void queue_alliance_replay(AllianceReplay,const std::string&,const std::string&){}
// PRODUCTION
}
int main(){using namespace alliance_game;try{
 session.profile.account_id="actor";session.profile.database_generation="generation";
 account::Battle battle;battle.id="battle";battle.format=2;battle.revision=6;battle.generation="generation";battle.state="draft";battle.room_state="preparing";battle.map.id="map";
 account::BattleTeam a;a.id="a";a.captain_id="actor";a.approved=true;a.slots={{1,"reserved","actor",{},{}},{2,"ai",{},"Rebels","Elvish Captain"}};
 account::BattleTeam b;b.id="b";b.captain_id="rival";b.approved=true;b.slots={{3,"open",{},{},{}},{4,"closed",{},{},{}}};
 account::BattlePlayer p;p.id="actor";p.side=1;p.ready=true;p.selection_present=true;p.hero_id="hero";p.form_id="form";a.players={p};battle.teams={a,b};
 account::Request q;q.battle_id=battle.id;q.revision=5;q.side=1;q.ready=true;q.hero_id="hero";q.form_id="form";
 for(const auto* op:{"approve_plan","ready_plan","join_plan","select_plan_hero"}){q.operation=op;need(plan_acknowledges(q,battle,"actor"));auto wrong=battle;wrong.teams[0].players.clear();wrong.teams[0].approved=false;need(!plan_acknowledges(q,wrong,"actor"));}
 q.operation="leave_plan";need(!plan_acknowledges(q,battle,"actor"));auto left=battle;left.teams[0].players.clear();need(plan_acknowledges(q,left,"actor"));
 q.operation="ready_plan";q.ready=false;need(!plan_acknowledges(q,battle,"actor"));q.ready=true;
 q.operation="join_plan";q.side=3;need(!plan_acknowledges(q,battle,"actor"));q.side=1;
 q.operation="select_plan_hero";q.form_id="other";need(!plan_acknowledges(q,battle,"actor"));q.form_id="form";
 q.operation="edit_plan";q.map_id="map";q.settings=battle.settings;q.slots=a.slots;q.slots.insert(q.slots.end(),b.slots.begin(),b.slots.end());need(plan_acknowledges(q,battle,"actor"));
 for(int n=0;n<5;++n){auto wrong=battle;if(n==0)wrong.settings.gold++;if(n==1)wrong.settings.base_income++;if(n==2)wrong.map.id="other";if(n==3)wrong.teams[0].slots[0].account_id="rival";if(n==4)wrong.teams[0].slots[1].ai_leader="Red Mage";need(!plan_acknowledges(q,wrong,"actor"));}
 q.operation="publish_plan";q.team_id="a";q.opponent_team_id="b";need(plan_acknowledges(q,battle,"actor"));q.opponent_team_id="other";need(!plan_acknowledges(q,battle,"actor"));
 q.operation="ready_plan";response.ok=true;response.battle=battle;account::Battle confirmed;alliance::TrustedProfile trusted{};need(battle_mutation(trusted,q,"Ready",confirmed)&&confirmed.id==battle.id);
 for(int n=0;n<3;++n){session.token="token";session.profile.account_id="actor";session.profile.database_generation="generation";race=[n]{if(n==0)session.token="new-token";if(n==1)session.profile.account_id="replacement";if(n==2)session.profile.database_generation="new-generation";};need(!battle_mutation(trusted,q,"Ready",confirmed));}
 race={};session.token="token";session.profile.account_id="actor";session.profile.database_generation="generation";response.battle->generation="unrelated-room-uuid";need(battle_mutation(trusted,q,"Ready",confirmed));response.battle->generation.clear();need(battle_mutation(trusted,q,"Ready",confirmed));
 enter_flexible_plan(trusted,battle);need(queued==1&&!admission.observer&&admission.token=="token"&&admission.profile.port==15002);
 battle.teams[0].players[0].ready=false;enter_flexible_plan(trusted,battle);need(queued==1);
 battle.state="running";battle.settings.observers=true;enter_flexible_plan(trusted,battle,true);need(queued==1);battle.teams[0].players.clear();enter_flexible_plan(trusted,battle,true);need(queued==2&&admission.observer);battle.settings.observers=false;enter_flexible_plan(trusted,battle,true);need(queued==2);
 queued=0;battle.state="draft";battle.room_state="";battle.generation.clear();battle.teams[0].players={p};battle.teams[0].players[0].ready=false;
 need(!flexible_plan_action(trusted,battle)&&workspace_calls==1&&roster_calls==1&&queued==0);
 battle.room_state="preparing";battle.generation="independent-room-generation";battle.teams[0].players[0].ready=true;need(!flexible_plan_action(trusted,battle)&&queued==1&&workspace_calls==1);
 directory_available=false;battle.state="completed";battle.room_state="finished";need(!flexible_plan_action(trusted,battle)&&workspace_calls==2&&roster_calls==1);
 battle.state="cancelled";need(!flexible_plan_action(trusted,battle)&&workspace_calls==3&&roster_calls==1);
 battle.state="running";records={{battle.id,"pending"}};need(!flexible_plan_action(trusted,battle)&&workspace_calls==4&&seen_terminal&&seen_pending&&queued==1);records={{battle.id,"acknowledged"}};need(!flexible_plan_action(trusted,battle)&&workspace_calls==5&&seen_terminal&&!seen_pending&&queued==1);
 battle.state="completed";response.battle=battle;session.profile.account_id="current-ordinary-teammate";download_shared_replay(trusted,battle);need(queued==2&&admission.download_only&&admission.account_id==session.profile.account_id);
 detail_allowed=false;session.profile.account_id="actor";download_shared_replay(trusted,battle);need(queued==2);
 std::cout<<"PASS empty-draft and independent-room generations; archived-opponent history; terminal result views; current-member/departed-participant replay admission\n";
 std::cout<<"PASS Team UI acknowledgement, identity race, seat and observer guards\n";return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
'''.replace('// PRODUCTION',production)
  with tempfile.TemporaryDirectory() as d:
   cpp=Path(d)/'plan.cpp';exe=Path(d)/'plan';cpp.write_text(fixture)
   built=subprocess.run(['g++','-std=c++17','-fsanitize=address,undefined','-g','-I'+str(ROOT),str(cpp),'-o',str(exe)],capture_output=True,text=True)
   self.assertEqual(built.returncode,0,built.stderr)
   ran=subprocess.run([str(exe)],capture_output=True,text=True,timeout=20);self.assertEqual(ran.returncode,0,ran.stdout+ran.stderr);print(ran.stdout,end='')
if __name__=='__main__':unittest.main()
