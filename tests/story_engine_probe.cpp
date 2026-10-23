// SPDX-License-Identifier: GPL-2.0-or-later
// Real engine fixture; only the account HTTP boundary is replaced.
#include "alliance_game/story_progression.hpp"
#include "alliance_game/account_ui.hpp"
#include "alliance_game/hero_catalog.hpp"
#include "alliance_game/shared_launch.hpp"
#include "alliance_game/progression_result.hpp"
#include "saved_game.hpp"
#include "savegame.hpp"
#include "save_index.hpp"
#include "serialization/parser.hpp"
#include "filesystem.hpp"
#include <boost/json.hpp>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <cstdlib>
extern "C" int __real_main(int,char**);
namespace j=boost::json;namespace fs=std::filesystem;
static const std::string account="30000000-0000-4000-8000-000000000001",generation="40000000-0000-4000-8000-000000000001",runid="20000000-0000-4000-8000-000000000001";
static bool connected=false;
namespace alliance_game {
PlayerSession::~PlayerSession()=default;
std::optional<PlayerSession> player_session(){PlayerSession s;s.account.account_id=account;s.account.username="StoryFixture";s.account.database_generation=generation;return s;}
bool player_request(alliance::account::Request& q,alliance::account::Reply& r){
 if(q.operation=="story_begin"){r.progression_json=j::serialize(j::object{{"run_id",runid},{"account_id",account},{"database_generation",generation},{"campaign_id","Two_Brothers"},{"catalog_version",hero_catalog_version()},{"catalog_sha256",hero_catalog_sha256()},{"next_checkpoint",0},{"nonce",std::string(64,'a')}});return true;}
 if(q.operation=="story_checkpoint") {if(!connected)return false;auto body=j::parse(q.progression_json).as_object();std::ofstream(fs::path(std::getenv("WESNOTH_ALLIANCE_PROFILE")).parent_path()/"acknowledged.json")<<q.progression_json;r.progression_json=j::serialize(j::object{{"run_id",body.at("run_id")},{"checkpoint_id",body.at("checkpoint_id")},{"next_checkpoint",body.at("sequence").as_int64()+1},{"outcome",body.at("outcome")},{"rewards",j::array{}},{"heroes",j::array{}}});return true;}throw std::runtime_error("Unexpected fixture request.");
}
void retry_training_results() noexcept {}
void observe_progression_completion(saved_game&,level_result::type) noexcept {}
void observe_shared_completion(saved_game&,level_result::type) noexcept {}
bool shared_pending() noexcept{return false;}void run_pending_shared(){}
bool shared_save(const saved_game&) noexcept{return false;}
}
static void check(bool c,const char*m){if(!c)throw std::runtime_error(m);}
static void lua(config& c,const char*name,const std::string&code){auto&e=c.add_child("event");e["name"]=name;e.add_child("lua")["code"]=code;}
static fs::path recordpath(){return fs::path(std::getenv("WESNOTH_ALLIANCE_PROFILE")).parent_path()/"unified-story"/generation/account/(runid+".json");}
static j::object record(){std::ifstream in(recordpath());return j::parse(std::string((std::istreambuf_iterator<char>(in)),{})).as_object();}
extern "C" int __wrap_main(int argc,char**argv){try{
 if((argc==3||argc==4)&&std::string(argv[1])=="--generate-story"){
  const std::string variant=argc==4?argv[3]:"win";filesystem::set_user_data_dir((fs::path(std::getenv("WESNOTH_ALLIANCE_PROFILE")).parent_path()/"fixture-userdata").string());saved_game state;state.classification().campaign="Two_Brothers";check(alliance_game::begin_story_campaign(state),"registration failed");
  config level{"id","01_Rooting_Out_a_Mage","name","Story tracking fixture","map_data","Gg,Gg,Gg,Gg,Gg,Gg\nGg,Gg,Gg,Gg,Gg,Gg\nGg,Gg,Gg,Gg,Gg,Gg\nGg,Gg,Gg,Gg,Gg,Gg\nGg,Gg,Gg,Gg,Gg,Gg","victory_when_enemies_defeated",false,"turns",3,"next_scenario",""};
  level.add_child("variables",state.to_config().child_or_empty("carryover_sides_start").child_or_empty("variables"));
  level.add_child("time",config{"id","day","lawful_bonus",0});
  auto&s=level.add_child("side",config{"side",1,"controller","human","is_local",true,"team_name","one","no_leader",true,"gold",0,"fog",false,"shroud",false});
  auto&u=s.add_child("unit",config{"type","Knight","id","Arvith","x",2,"y",2,"canrecruit",true,"random_traits",false,"experience",0,"max_experience",8,"advances_to","Paladin"});
  auto&obj=u.add_child("modifications").add_child("object");obj.add_child("effect",config{"apply_to","attack","set_damage",100,"set_attacks",1,"set_accuracy",100});
  // A never-deployed named hero in recall must earn nothing.
  s.add_child("unit",config{"type","Red Mage","id","Baran","x",variant=="dead"?"2":"recall","y",variant=="dead"?"3":"recall","random_traits",false});
  auto&e=level.add_child("side",config{"side",2,"controller","idle","team_name","two","no_leader",true,"gold",0});
  lua(level,"start","local u=wesnoth.units.get('Arvith'); assert(u.type=='Knight' and u.level==2 and u.experience==0); assert(u.variables.alliance_hero_level==nil,'story received account scaling'); std_print('STORY ORIGINAL START VERIFIED')");
  auto&turn=level.add_child("event");turn["name"]="side 1 turn 2";
  turn.add_child("unit",config{"side",2,"type","Spearman","id","victim","x",3,"y",2,"hitpoints",1,"random_traits",false});
  turn.add_child("unit",config{"side",2,"type","Spearman","id","script_victim","x",4,"y",2,"hitpoints",1,"random_traits",false});
  auto&attack=turn.add_child("do_command").add_child("attack",config{"weapon",0,"defender_weapon",-1});attack.add_child("source",config{"x",2,"y",2});attack.add_child("destination",config{"x",3,"y",2});
  turn.add_child("modify_unit",config{"experience","$($this_unit.experience + 5)","filter",config{"id","Arvith"}});
  turn.add_child("harm_unit",config{"amount",100,"experience",true,"kill",true,"fire_event",true,"filter",config{"id","script_victim"},"filter_second",config{"id","Arvith"}});
  // Absolute XP restoration does not create long-term earned experience.
  turn.add_child("modify_unit",config{"experience",1,"filter",config{"id","Arvith"}});
  turn.add_child("lua")["code"]="local u=wesnoth.units.get('Arvith'); assert(u.type=='Paladin','native advancement did not occur'); assert(u.variables.unified_story_earned==21,'earned XP double counted or lost: '..tostring(u.variables.unified_story_earned)); std_print('STORY COMBAT SCRIPT ADVANCEMENT VERIFIED')";
  turn.add_child("lua")["code"]=R"LIBERTY(
local meta = wml.variables["unified_story"]
local ledger = wml.variables["unified_story_ledger"]
wesnoth.wml_actions.unit{type="Bandit",id="Baldras",side=1,x=1,y=1,experience=0,random_traits=false}
wml.variables["unified_story.campaign_id"]="Liberty"
wml.variables["unified_story.scenario_id"]="01_The_Raid"
assert(wesnoth.units.get("Baldras").x==1 and wesnoth.units.get("Baldras").y==1)
local T=wml.tag
wesnoth.wml_actions.store_unit{T.filter{id="Baldras"},variable="stored_unit"}
wesnoth.wml_actions.set_variable{name="stored_unit.experience",add=2}
wesnoth.wml_actions.unstore_unit{variable="stored_unit"}
assert(wesnoth.units.get("Baldras").variables.unified_story_earned==2)
wml.variables["unified_story.scenario_id"]="03_A_Strategy_of_Hope"
wesnoth.wml_actions.store_unit{T.filter{id="Baldras"},variable="stored_unit"}
wesnoth.wml_actions.set_variable{name="stored_unit.experience",add=6}
wesnoth.wml_actions.unstore_unit{variable="stored_unit"}
assert(wesnoth.units.get("Baldras").experience==8)
assert(wesnoth.units.get("Baldras").variables.unified_story_earned==8)
wesnoth.wml_actions.kill{id="Baldras",fire_event=false}
wml.variables["unified_story"]=meta
wml.variables["unified_story_ledger"]=ledger
wml.variables["stored_unit"]=nil
std_print("STORY LIBERTY STORED AWARDS VERIFIED")
)LIBERTY";
  if(variant=="dead")turn.add_child("kill",config{"id","Baran","fire_event",true});
  turn.add_child("endlevel",config{"result",variant=="loss"?"defeat":"victory","linger_mode",false,"replay_save",false,"carryover_report",false});
  state.set_scenario(level);check(alliance_game::prepare_story_scenario(state,false),"prepare failed");config out;out.add_child("multiplayer",state.get_starting_point());fs::create_directories(fs::path(argv[2]));std::ofstream f(fs::path(argv[2])/"_main.cfg");f<<"#ifdef MULTIPLAYER\n";io::write(f,out);f<<"#endif\n";std::cout<<"STORY FIXTURE GENERATED\n";return 0;
 }
 if(argc==2&&std::string(argv[1])=="--verify-branch"){
  filesystem::set_user_data_dir((fs::path(std::getenv("WESNOTH_ALLIANCE_PROFILE")).parent_path()/"fixture-userdata").string());
  const auto r=record();const auto old=r.at("completed").as_array()[0].as_object();
  saved_game state;state.classification().campaign="Two_Brothers";config scenario{"id","02_The_Chase"};
  auto& meta=scenario.add_child("variables").add_child("unified_story");meta["run_id"]=runid;meta["account_id"]=account;meta["database_generation"]=generation;meta["campaign_id"]="Two_Brothers";meta["scenario_id"]="01_Rooting_Out_a_Mage";meta["checkpoint_id"]=std::string(old.at("checkpoint_id").as_string());meta["sequence"]=0;
  // A genuine same-ID transition clears only carryover; replaying the old
  // checkpoint remains deduplicated. A later different scenario still advances.
  config carry{"next_scenario","01_Rooting_Out_a_Mage"};carry.add_child("variables",scenario.child_or_empty("variables"));state.set_carryover_sides_start(carry);check(alliance_game::advance_story_scenario(state),"transition preparation failed");const auto boundary=state.to_config().child_or_empty("carryover_sides_start").child_or_empty("variables").child_or_empty("unified_story");check(!boundary["checkpoint_id"].empty()&&boundary["scenario_id"].str()=="01_Rooting_Out_a_Mage"&&boundary["sequence"].to_int()==1,"start-save checkpoint not allocated");
  savegame::scenariostart_savegame saver(state,compression::format::gzip);check(saver.save_game_automatic(false,"StoryBoundary"),"scenario-start save failed");saved_game loaded(savegame::read_save_file(filesystem::get_saves_dir(),"StoryBoundary.gz"));check(loaded.to_config().child_or_empty("carryover_sides_start").child_or_empty("variables").child_or_empty("unified_story")==boundary,"scenario-start disk save lost its checkpoint");state.set_carryover_sides_start(loaded.to_config().child_or_empty("carryover_sides_start"));
  config loop{"id","01_Rooting_Out_a_Mage"};loop.add_child("variables",state.to_config().child_or_empty("carryover_sides_start").child_or_empty("variables"));state.set_scenario(loop);check(alliance_game::prepare_story_scenario(state,false),"same-ID transition refused");const auto loopmeta=state.get_starting_point().child_or_empty("variables").child_or_empty("unified_story");check(loopmeta["checkpoint_id"].str()==boundary["checkpoint_id"].str()&&loopmeta["checkpoint_id"].str()!=std::string(old.at("checkpoint_id").as_string())&&loopmeta["sequence"].to_int()==1,"same-ID transition reused checkpoint");
  state.set_scenario(scenario);check(alliance_game::prepare_story_scenario(state,false),"branch refused");const auto& next=state.get_starting_point().child_or_empty("variables").child_or_empty("unified_story");check(next["scenario_id"].str()=="02_The_Chase"&&next["sequence"].to_int()==1&&next["checkpoint_id"].str()!=std::string(old.at("checkpoint_id").as_string()),"branch reused a checkpoint");
  saved_game failed;failed.classification().campaign="Two_Brothers";auto bad=carry;bad.mandatory_child("variables").mandatory_child("unified_story")["checkpoint_id"]="20000000-0000-4000-8000-000000000099";failed.set_carryover_sides_start(bad);check(!alliance_game::advance_story_scenario(failed),"transition accepted a missing durable result");
  bad=carry;bad.mandatory_child("variables").mandatory_child("unified_story")["sequence"]=1;failed.set_carryover_sides_start(bad);check(!alliance_game::advance_story_scenario(failed),"transition accepted unchanged sequence");
  std::cout<<"STORY SCENARIO TRANSITION VERIFIED\n";return 0;
 }
 if((argc==2||argc==3)&&std::string(argv[1])=="--verify-story"){
  const std::string variant=argc==3?argv[2]:"win";const auto r=record();const auto&p=r.at("pending").as_array();check(p.size()==1,"one durable result expected");const auto&c=p[0].as_object();check(c.at("outcome")==j::value(variant=="loss"?"loss":"win"),"wrong outcome");const auto&hs=c.at("heroes").as_array();check(hs.size()==(variant=="loss"?0:variant=="dead"?2:1),"participation differs");if(variant!="loss"){const auto&h=hs[0].as_object();check(h.at("hero_id")=="arvith"&&h.at("earned_experience")==21,"earned XP mismatch");check(h.at("unlocked_forms").as_array().size()==2,"advanced forms not retained");if(variant=="dead")check(hs[1].as_object().at("hero_id")=="baran"&&hs[1].as_object().at("earned_experience")==0,"dead companion missing");}check(c.at("replay_sha256").as_string().size()==64,"native replay missing");
  connected=true;alliance_game::retry_story_results();check(record().at("pending").as_array().empty(),"durable retry failed");alliance_game::retry_story_results();check(record().at("completed").as_array().size()==1,"retry duplicated checkpoint");std::cout<<"STORY ACTUAL ENGINE RESULT/RETRY VERIFIED\n";return 0;
 }
 return __real_main(argc,argv);
}catch(const std::exception&e){std::cerr<<"STORY FIXTURE ERROR: "<<e.what()<<'\n';return 1;}}
