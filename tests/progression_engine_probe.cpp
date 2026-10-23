// SPDX-License-Identifier: GPL-2.0-or-later
// Linked only into the isolated host fixture, never the Android application.
#include "alliance_game/progression_plan.hpp"
#include "alliance_game/progression_result.hpp"
#include "alliance_game/progression_scenario.hpp"
#include "serialization/parser.hpp"
#include "filesystem.hpp"
#include "saved_game.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <utility>
extern "C" int __real_main(int,char**);
namespace {
void check(bool value,const char* message){if(!value)throw std::runtime_error(message);}
alliance_game::ProgressionRun run(int mission,int variant=0) {
    return {"20000000-0000-4000-8000-0000000000"+std::to_string(mission)+std::to_string(variant),
     "30000000-0000-4000-8000-000000000001","FixturePlayer","alliance_training_"+std::to_string(mission+1),
     alliance_game::progression_mission_version,"alliance-v1","Spearman",std::string(64,'a'),1};
}
void lua(config& level,const std::string& name,const std::string& code) {auto& e=level.add_child("event");e["name"]=name;e.add_child("lua")["code"]=code;}
std::string text(const config& c){std::ostringstream s;io::write(s,c);return s.str();}
}
extern "C" int __wrap_main(int argc,char** argv) {
 try {
  if(argc==3&&std::string(argv[1])=="--storage-contract") {
    const auto registration=run(0,8);alliance_game::register_progression_run(registration);
    saved_game fake;config scenario;scenario["id"]="alliance_progression_"+registration.id;
    auto& vars=scenario.add_child("variables");vars["alliance_progression"]=true;vars["alliance_run_id"]=registration.id;
    vars["alliance_account_id"]=registration.account_id;vars["alliance_mission_id"]=registration.mission_id;
    vars["alliance_mission_version"]=registration.mission_version;vars["alliance_ruleset_version"]=registration.ruleset_version;
    vars["alliance_unit_type"]=registration.unit_type;vars["alliance_leader_level"]=registration.leader_level;
    fake.set_scenario(scenario);check(alliance_game::progression_save(fake),"progression load marker missing");
    // A plausible saved marker cannot capture a result without an active fresh launch.
    alliance_game::observe_progression_completion(fake,level_result::type::victory);
    auto status=[&](){for(const auto& r:alliance_game::inspect_pending_progression(registration.account_id))if(r.run.id==registration.id)return r.status;return std::string();};
    check(status()=="started","arbitrary saved game acquired a reward result");
    {
      alliance_game::ProgressionActiveLaunch active(registration);
      auto changed=scenario;changed.child_or_add("variables")["alliance_account_id"]="wrong-owner";fake.set_scenario(changed);
      alliance_game::observe_progression_completion(fake,level_result::type::victory);
      check(status()=="started","mismatched owner result was stored");
      check(alliance_game::take_progression_notice().find("[progression-save]")!=std::string::npos,"fixed failure notice missing");
      fake.set_scenario(scenario);alliance_game::observe_progression_completion(fake,level_result::type::quit);
      check(status()=="started","quit captured a terminal result");
    }
    alliance_game::acknowledge_progression_cancel(registration.account_id,registration.id);
    alliance_game::acknowledge_progression_cancel(registration.account_id,registration.id);check(status()=="cancelled","cancel acknowledgement not retained");
    filesystem::set_user_data_dir(argv[2]);const auto complete=run(0,0);
    const auto filename=alliance_game::verified_progression_replay(complete.account_id,complete.id);
    const auto path=std::filesystem::path(filesystem::get_saves_dir())/filename;
    std::ifstream original(path,std::ios::binary);const std::string bytes((std::istreambuf_iterator<char>(original)),{});original.close();
    {std::ofstream changed(path,std::ios::binary|std::ios::app);changed<<'x';}
    bool rejected=false;try{(void)alliance_game::verified_progression_replay(complete.account_id,complete.id);}catch(...){rejected=true;}
    {std::ofstream restored(path,std::ios::binary|std::ios::trunc);restored.write(bytes.data(),bytes.size());}
    check(rejected,"tampered replay accepted");check(alliance_game::verified_progression_replay(complete.account_id,complete.id)==filename,"original replay not restored");
    std::cout<<"PROGRESSION STORAGE CONTRACT VERIFIED: no saved-game/quit/mismatched-owner rewards; retained cancellation; replay tamper rejected\n";return 0;
  }
  if(argc>2&&std::string(argv[1])=="--unregistered")return __real_main(argc-2,argv+2);
  if(argc==5&&std::string(argv[1])=="--inspect-result") {
    filesystem::set_user_data_dir(argv[4]);
    const auto registration=run(std::stoi(argv[2]),std::stoi(argv[3]));
    const auto records=alliance_game::inspect_pending_progression(registration.account_id);
    const auto expected=std::stoi(argv[3])==0?"win":"loss";bool found=false;
    for(const auto& record:records)if(record.run.id==registration.id){
      check(record.status=="pending"&&record.outcome==expected,"terminal journal mismatch");
      check(record.replay_bytes>100&&record.replay_sha256.size()==64,"replay journal incomplete");found=true;
    }
    check(found,"terminal journal absent");
    check(alliance_game::verified_progression_replay(registration.account_id,registration.id)=="Alliance-mission-"+registration.id+".gz","verified replay lookup failed");
    bool rejected=false;try{alliance_game::acknowledge_progression_result(registration.account_id,registration.id,std::string(expected)=="win"?"loss":"win");}catch(...){rejected=true;}
    check(rejected,"conflicting acknowledgement accepted");
    alliance_game::acknowledge_progression_result(registration.account_id,registration.id,expected);
    alliance_game::acknowledge_progression_result(registration.account_id,registration.id,expected);
    rejected=false;try{alliance_game::register_progression_run(registration);}catch(...){rejected=true;}check(rejected,"same completed run was registered twice");
    for(const auto& record:alliance_game::inspect_pending_progression(registration.account_id))if(record.run.id==registration.id)check(record.status=="acknowledged","acknowledgement not durable");
    std::cout<<"PROGRESSION ACTUAL RESULT AND IDEMPOTENT ACK VERIFIED\n";return 0;
  }
  if(argc>=2&&std::string(argv[1])=="--registered") {
    check(argc>5,"registered invocation missing arguments");const int mission=std::stoi(argv[2]),variant=std::stoi(argv[3]);
    const auto registration=run(mission,variant);alliance_game::register_progression_run(registration);
    alliance_game::ProgressionActiveLaunch active(registration);
    const int result=__real_main(argc-4,argv+4);
    const auto records=alliance_game::inspect_pending_progression(registration.account_id);
    bool found=false;for(const auto& record:records)if(record.run.id==registration.id) {
      check(record.status=="pending","actual terminal result was not captured");
      check(record.outcome==(variant==0?"win":"loss"),"wrong actual outcome");
      check(record.replay_bytes>100&&record.replay_sha256.size()==64,"real replay missing");found=true;
    }
    check(found,"registered result absent");std::cout<<"PROGRESSION ACTUAL RESULT VERIFIED "<<mission<<" "<<variant<<'\n';return result;
  }
  check(argc==4,"expected preprocessed era and output directory");
  std::ifstream input(argv[1]);const auto data=io::read(input);const auto era=data.find_child("era","id","era_default");check(bool(era),"era missing");
  const std::filesystem::path out(argv[3]);std::filesystem::create_directories(out);
  const std::vector<std::pair<const char*,int>> types={{"Spearman",1},{"Swordsman",2},{"Pikeman",2},{"Javelineer",2},{"Royal Guard",3},{"Halberdier",3},{"Elvish Fighter",1},{"Elvish Captain",2},{"Elvish Hero",2},{"Elvish Marshal",3},{"Elvish Champion",3},{"Orcish Grunt",1},{"Orcish Warrior",2},{"Orcish Warlord",3},{"Dwarvish Fighter",1},{"Dwarvish Steelclad",2},{"Dwarvish Lord",3},{"Drake Fighter",1},{"Drake Warrior",2},{"Drake Blademaster",3},{"Skeleton",1},{"Revenant",2},{"Deathblade",2},{"Draug",3}};
  int checks=0;
  for(int mission=0;mission<3;++mission)for(const auto& type:types) {
    auto r=run(mission);r.unit_type=type.first;r.leader_level=type.second;
    check(alliance_game::valid_progression_run(r),"valid leader rejected");
    const auto s=alliance_game::make_progression_scenario(r,*era);
    check(alliance_game::progression_marker_matches(s,r),"public markers differ");
    check(text(s).find(r.nonce)==std::string::npos,"capability leaked into scenario");
    check(s.child_count("side")==static_cast<std::size_t>(mission==2?3:2),"wrong side count");
    auto wrong=r;wrong.nonce="bad";check(!alliance_game::valid_progression_run(wrong),"invalid nonce accepted");
    wrong=r;wrong.mission_version="old";check(!alliance_game::valid_progression_run(wrong),"old content accepted");
    wrong=r;wrong.leader_level=4;check(!alliance_game::valid_progression_run(wrong),"wrong leader level accepted");
    auto altered=s;altered.child_or_add("variables")["alliance_account_id"]="different";
    check(!alliance_game::progression_marker_matches(altered,r),"wrong owner matched");checks+=8;
  }
  const auto own=run(0);alliance_game::queue_progression(own);check(alliance_game::progression_pending(),"not queued");
  bool refused=false;try{alliance_game::queue_progression(own);}catch(...){refused=true;}check(refused,"double launch allowed");
  check(alliance_game::same_progression_run(*alliance_game::take_pending_progression(),own),"queue changed descriptor");check(!alliance_game::take_pending_progression(),"queue not consumed");checks+=4;
  for(int mission=0;mission<3;++mission)for(int variant=0;variant<2;++variant) {
    const auto r=run(mission,variant);auto scenario=alliance_game::make_progression_scenario(r,*era);
    scenario.clear_children("time");scenario.add_child("time",config{"id","day","lawful_bonus",0});
    // Keep production objectives/events/controllers and inject only assertions
    // plus deterministic fixture actions to reach actual victory/defeat hooks.
    lua(scenario,"start","local u=wesnoth.units.get('alliance_personal_leader'); assert(u and u.type=='Spearman' and u.level==1); assert(u.max_experience==1000000000 and #u.advances_to==0); local copy=wesnoth.units.create(u.__cfg); assert(#copy.advances_to==0 and copy.max_experience==1000000000); assert(wesnoth.sides[1].controller=='human'); assert(wml.variables.alliance_mission_version=='progression-campaign-v1'); std_print('PROGRESSION START VERIFIED')");
    auto& end=scenario.add_child("event");end["name"]="side 1 turn";end["first_time_only"]=false;end.add_child("end_turn");
    lua(scenario,"turn 2","assert(#wesnoth.units.find_on_map(wml.parse('side=2'))>1,'enemy recruited'); std_print('PROGRESSION AI ROUND VERIFIED')");
    auto& finish=scenario.add_child("event");finish["name"]="turn 2";
    if(variant==1)finish.add_child("kill",config{"id","alliance_personal_leader","fire_event",true});
    else {
      if(mission==1) {
        // The real moveto event must establish the crossing condition. Merely
        // killing the commander is insufficient in this mission.
        finish.add_child("teleport",config{"x",11,"y",6,"check_passability",false,"filter",config{"id","alliance_personal_leader"}});
        finish.add_child("fire_event",config{"name","moveto","primary_unit",config{"id","alliance_personal_leader"}});
        finish.add_child("lua")["code"]="assert(wml.variables.alliance_crossed_river,'river objective not reached')";
      }
      finish.add_child("kill",config{"side","2,3","canrecruit",true,"fire_event",true});
    }
    lua(scenario,"victory","std_print('PROGRESSION REAL VICTORY')");lua(scenario,"defeat","std_print('PROGRESSION REAL DEFEAT')");
    config wrapper;wrapper.add_child("multiplayer",scenario);
    std::ofstream file(out/("mission-"+std::to_string(mission)+"-"+std::to_string(variant)+".cfg"));io::write(file,wrapper);
  }
  std::cout<<"PROGRESSION EXPORT VERIFIED: checks="<<checks<<"; missions=3; leader_types=24\n";return 0;
 } catch(const std::exception& error){std::cerr<<"PROGRESSION FIXTURE FAILED: "<<error.what()<<'\n';return 1;}
}
