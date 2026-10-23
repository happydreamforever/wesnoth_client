// SPDX-License-Identifier: GPL-2.0-or-later
// Actual pinned config/unit/event engine around the production casual side transform.
#include "alliance_game/casual_sides.hpp"
#include "alliance_game/casual_session.hpp"
#include "alliance_game/account_ui.hpp"
#include "alliance_game/hero_catalog.hpp"
#include "serialization/parser.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
extern "C" int __real_main(int,char**);
// GUI/account boundaries are unused by this config/engine fixture.
namespace alliance_game {
PlayerSession::~PlayerSession()=default;
std::optional<PlayerSession> player_session(){return std::nullopt;}
bool has_player_session() noexcept{return false;}
bool account_session_interaction_active() noexcept{return false;}
bool player_request(alliance::account::Request&,alliance::account::Reply&){return false;}
bool choose_owned_hero(std::string&,std::string&){return false;}
}
bool shared_fixture_companion(){return false;}
bool shared_fixture_replay(){return false;}
void shared_fixture_decorate(config&){}
static void check(bool value,const char* message){if(!value)throw std::runtime_error(message);}
static std::string written(const config& c){std::ostringstream s;io::write(s,c);return s.str();}
extern "C" int __wrap_main(int argc,char**argv){
 if(argc!=3||std::string(argv[1])!="--make-casual-identity")return __real_main(argc,argv);
 try{
 const std::filesystem::path out(argv[2]);check(!std::filesystem::exists(out),"Fixture output already exists");std::filesystem::create_directories(out);
 const auto* form=alliance_game::find_hero_form("konrad","konrad_fighter");
 if(!form){for(const auto& f:alliance_game::hero_forms())if(f.hero_id=="konrad"&&f.native_level==1){form=&f;break;}}
 check(form,"Konrad form missing");
 config packet;packet.add_child("alliance_game",config{"id","11111111-1111-4111-8111-111111111111","resume",false});
 packet.add_child("alliance_selection",config{"game_id","11111111-1111-4111-8111-111111111111","side",1,"username","Player","account_id","22222222-2222-4222-8222-222222222222","hero_id",form->hero_id,"form_id",form->id,"hero_level",51,"hero_revision",1,"unit_type",form->unit_type,"unit_level",form->native_level,"ready",true});
 alliance_game::observe_casual_message(packet);
 config side{"side",1,"controller","human","player_id","Player","id","AuthoredSide","team_name","one","gold",100,"recruit","Spearman","persistent",true,"save_id","AuthoredSide"};
 side.add_child("leader",config{"id","AuthoredLeader","name","Authored captain","gender","male","type","Spearman","canrecruit",true,"random_traits",false});
 alliance_game::apply_casual_side(side);
 const auto& leader=side.child_or_empty("leader");
 check(leader["id"].str()=="AuthoredLeader"&&leader["name"].str()=="Authored captain"&&leader["gender"].str()=="male","Authored identity changed");
 check(leader["type"].str()==form->unit_type,"Canonical type missing");
 auto resumed=side;resumed.mandatory_child("leader")["hitpoints"]=13;resumed.mandatory_child("leader")["experience"]=9;
 const auto saved=written(resumed);config resume;resume.add_child("alliance_game",config{"id","11111111-1111-4111-8111-111111111111","resume",true});alliance_game::observe_casual_message(resume);alliance_game::apply_casual_side(resumed);check(written(resumed)==saved,"Resume modified saved combat configuration");
 config data;auto& test=data.add_child("test");test["id"]="unified_casual_identity";test["name"]="Casual identity fixture";test["turns"]=3;test["victory_when_enemies_defeated"]=false;
 std::ifstream map("/gamedata/data/multiplayer/maps/4p_Isars_Cross.map");std::ostringstream terrain;terrain<<map.rdbuf();test["map_data"]=terrain.str();
 test.add_child("side",side);auto& opponent=test.add_child("side",config{"side",2,"controller","ai","team_name","two","type","Spearman","id","Enemy","gold",0,"random_traits",false});(void)opponent;
 auto& filtered=test.add_child("event",config{"name","authored_leader_event","first_time_only",false});filtered.add_child("filter",config{"id","AuthoredLeader"});filtered.add_child("lua")["code"]="wml.variables.authored_identity_hits=(wml.variables.authored_identity_hits or 0)+1";
 auto& start=test.add_child("event",config{"name","start"});
 start.add_child("lua")["code"]=std::string(R"(
  local u=wesnoth.units.get('AuthoredLeader')
  assert(u and tostring(u.name)=='Authored captain' and u.gender=='male')
  assert(u.type==')")+form->unit_type+R"(' and u.variables.alliance_hero_level==51)
  local base=wesnoth.unit_types[u.type].max_hitpoints
  assert(u.max_hitpoints==base*2)
  local attack=wml.get_child(wesnoth.unit_types[u.type].__cfg,'attack')
  assert(u.attacks[1].damage==math.floor(attack.damage*1.5+0.5))
  wesnoth.game_events.fire('authored_leader_event',u.x,u.y)
  assert(wml.variables.authored_identity_hits==1)
  u.hitpoints=u.max_hitpoints-7;u.experience=9;u.moves=1;u.status.poisoned=true
  local saved=u.__cfg
  local loaded=wesnoth.units.create(saved)
  assert(loaded.id=='AuthoredLeader' and tostring(loaded.name)=='Authored captain')
  assert(loaded.hitpoints==u.hitpoints and loaded.max_hitpoints==u.max_hitpoints)
  assert(loaded.experience==9 and loaded.moves==1 and loaded.status.poisoned)
  assert(loaded.attacks[1].damage==u.attacks[1].damage)
  loaded:to_map(u.x,u.y)
  wesnoth.game_events.fire('authored_leader_event',loaded.x,loaded.y)
  assert(wml.variables.authored_identity_hits==2)
  std_print('ACTUAL CASUAL AUTHORED ID EVENT AND SAVED COMBAT STATE VERIFIED')
 )";
 start.add_child("endlevel",config{"result","victory","carryover_report",false,"linger_mode",false,"bonus",false});
 std::ofstream file(out/"_main.cfg");io::write(file,data);std::cout<<"CASUAL IDENTITY SCENARIO GENERATED; production resume transform preserved saved combat fields\n";return 0;
 }catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}
}
