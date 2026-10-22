// SPDX-License-Identifier: GPL-2.0-or-later
// Real pinned-engine config parser/writer + production practice generator.
// Linked with the already-built host engine using --wrap=main; never used in APKs.
#include "alliance_game/practice_plan.hpp"
#include "alliance_game/practice_scenario.hpp"
#include "serialization/parser.hpp"
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <set>
#include <stdexcept>
#include <vector>

namespace {
void require(bool condition, const char* message) { if(!condition) throw std::runtime_error(message); }
std::string uuid(int value) {
    std::ostringstream out;
    out << "10000000-0000-4000-8000-" << std::setfill('0') << std::setw(12) << value;
    return out.str();
}
std::string written(const config& cfg) { std::ostringstream output; io::write(output,cfg); return output.str(); }
std::string quoted(const std::string& text) { std::ostringstream output; output << std::quoted(text); return output.str(); }
struct Unit { const char* type; int level; };
const std::vector<Unit> units = {
    {"Spearman",1},{"Swordsman",2},{"Pikeman",2},{"Javelineer",2},
    {"Royal Guard",3},{"Halberdier",3},{"Elvish Fighter",1},{"Elvish Captain",2},
    {"Elvish Hero",2},{"Elvish Marshal",3},{"Elvish Champion",3},{"Orcish Grunt",1},
    {"Orcish Warrior",2},{"Orcish Warlord",3},{"Dwarvish Fighter",1},{"Dwarvish Steelclad",2},
    {"Dwarvish Lord",3},{"Drake Fighter",1},{"Drake Warrior",2},{"Drake Blademaster",3},
    {"Skeleton",1},{"Revenant",2},{"Deathblade",2},{"Draug",3}
};
struct Map { const char* id; const char* scenario; const char* file; std::vector<std::vector<int>> sides; };
const std::vector<Map> maps = {
    {"isars-cross","multiplayer_Isars_Cross","multiplayer/maps/4p_Isars_Cross.map",{{1,4},{2,3}}},
    {"hexcake","multiplayer_Hexcake","multiplayer/maps/6p_Hexcake.map",{{1,4,5},{2,3,6}}},
    {"mokena-prairie","multiplayer_8p_Mokena_Prairie","multiplayer/maps/8p_Mokena_Prairie.map",{{1,3,5,8},{2,4,6,7}}}
};
void lua_event(config& scenario, const std::string& name, const std::string& code) {
    config& event=scenario.add_child("event"); event["name"]=name;
    event.add_child("lua")["code"]=code;
}
}

extern "C" int __wrap_main(int argc, char** argv) {
    try {
        require(argc==4,"expected preprocessed input, data directory and output directory");
        std::ifstream input(argv[1]); require(bool(input),"fixture input missing");
        const config data=io::read(input);
        const auto era=data.find_child("era","id","era_default"); require(bool(era),"real era missing");
        const std::string original_data=written(data);
        const std::filesystem::path datadir(argv[2]), output(argv[3]);
        std::filesystem::create_directories(output);
        std::ofstream manifest(output/"cases.tsv");
        int scenarios=0, rejections=0;
        std::set<std::string> exercised_units;
        for(int index=0; index<18; ++index) {
            const Map& map=maps[index/6];
            const int mode=index%6/2, viewer_team=index%2;
            const char* control=mode==0 ? "own_side" : mode==1 ? "own_team" : "hotseat";
            alliance_game::PracticeSettings settings;
            settings.control=static_cast<alliance_game::PracticeControl>(mode);
            settings.starting_gold=std::vector<int>{75,100,150,200}[index%4];
            settings.turn_limit=std::vector<int>{-1,20,30,50}[index%4];
            settings.fog=index%2==0;
            const auto source=data.find_child("multiplayer","id",map.scenario); require(bool(source),"real map missing");
            alliance::account::Battle battle;
            battle.id=uuid(100+index); battle.state="draft"; battle.validity="valid";
            battle.map.id=map.id; battle.map.scenario_id=map.scenario;
            battle.map.size=static_cast<int>(map.sides[0].size()); battle.map.sides=map.sides;
            for(int team=0;team<2;++team) {
                alliance::account::BattleTeam t;
                t.id=uuid(10+team); t.name=team?"Fixture Beta":"Fixture Alpha"; t.approved=true;
                for(std::size_t slot=0;slot<map.sides[team].size();++slot) {
                    const int side=map.sides[team][slot];
                    const Unit& unit=units[(index*4+side-1)%units.size()];
                    exercised_units.insert(unit.type);
                    t.players.push_back({uuid(side),"Fixture_"+std::to_string(side),unit.type,unit.level,side,true});
                }
                t.captain_id=t.players.front().id; battle.teams.push_back(t);
            }
            alliance::account::Profile viewer;
            viewer.account_id=uuid(map.sides[viewer_team].back());
            // Individual practice must work with only the viewer Ready. Other
            // cases retain a mixture of teammate/opponent readiness flags.
            for(auto& team : battle.teams) for(auto& player : team.players)
                player.ready = player.id == viewer.account_id || (index%2 && player.side%2 == 0);
            const auto plan=alliance_game::make_practice_plan(battle,viewer,settings);
            auto generated=alliance_game::make_practice_scenario(plan,*source,*era);
            require(generated.child_count("side")==plan.seats.size(),"wrong side count");
            require(generated["experience_modifier"].to_int()==100,"incorrect XP modifier");
            require(generated["objectives"].str().find("No persistent experience")!=std::string::npos,"practice notice missing");
            require(generated.child_count("event")==(*source).child_count("event"),"map events changed");
            int human=0;
            for(const auto& seat:plan.seats) {
                const auto side=generated.find_child("side","side",std::to_string(seat.side)); require(bool(side),"side missing");
                human+=(*side)["controller"].str()=="human";
                require((*side)["controller"].str()==(seat.human?"human":"ai"),"controller mismatch");
                require((*side)["team_name"].str()==seat.team_id,"alliance mismatch");
                require((*side)["current_player"].empty() && (*side)["player_id"].empty(),"guest identity leaked");
                const auto faction=(*era).find_child("multiplayer_side","id",seat.faction); require(bool(faction),"real faction missing");
                require((*side)["recruit"].str()==(*faction)["recruit"].str(),"real faction recruits differ");
                require((*side)["persistent"].to_bool(true)==false,"practice side persistent");
                const auto leader=(*side).optional_child("leader"); require(bool(leader),"leader missing");
                require((*leader)["type"].str()==seat.unit_type && (*leader)["experience"].to_int(-1)==0,"snapshot leader differs");
            }
            require(human==(mode==0?1:mode==1?plan.size:plan.size*2),"wrong number of locally controlled sides");
            require(plan.controlled_side==map.sides[viewer_team].back(),"viewer identity changed with mode");
            require(generated["turns"].to_int()==settings.turn_limit,"turn setting lost");
            require(generated.child_or_empty("variables")["alliance_practice_control"].str()==control,"mode marker lost");
            // Real parser roundtrip protects generated display names and nested events.
            require(written(io::read(written(generated)))==written(generated),"serialized config roundtrip differs");
            auto rejected=[&](config bad_source,config bad_era) {
                bool failed=false;
                try { (void)alliance_game::make_practice_scenario(plan,bad_source,bad_era); }
                catch(const std::invalid_argument&) {failed=true;}
                require(failed,"malformed installed input accepted"); ++rejections;
            };
            auto bad=*source; bad["id"]="another-map"; rejected(bad,*era);
            bad=*source; bad["map_file"]="multiplayer/maps/another.map"; rejected(bad,*era);
            bad=*source; bad.remove_child("side",0); rejected(bad,*era);
            bad=*era; bad["id"]="era_heroes"; rejected(*source,bad);
            bad=*era; bad.clear_children("multiplayer_side"); rejected(*source,bad);
            auto rejected_plan=[&](alliance_game::PracticePlan bad_plan) {
                bool failed=false;
                try { (void)alliance_game::make_practice_scenario(bad_plan,*source,*era); }
                catch(const std::invalid_argument&) { failed=true; }
                require(failed,"malformed training settings accepted"); ++rejections;
            };
            auto bad_plan=plan; bad_plan.settings.starting_gold=0; rejected_plan(bad_plan);
            bad_plan=plan; bad_plan.settings.turn_limit=0; rejected_plan(bad_plan);
            bad_plan=plan; bad_plan.settings.control=static_cast<alliance_game::PracticeControl>(99); rejected_plan(bad_plan);
            bad_plan=plan; bad_plan.controlled_side=99; rejected_plan(bad_plan);
            bad_plan=plan; bad_plan.seats.front().human=!bad_plan.seats.front().human; rejected_plan(bad_plan);
            std::ifstream map_input(datadir/"data"/map.file); require(bool(map_input),"map data missing");
            std::ostringstream map_bytes; map_bytes<<map_input.rdbuf();
            generated["map_data"]=map_bytes.str(); generated.remove_attribute("map_file");
            const std::string scenario_id="alliance_practice_engine_"+std::to_string(index);
            generated["id"]=scenario_id;
            // Preserve every generated production side and map event. Only add
            // assertions and an automatic human end-turn for headless testing.
            std::ostringstream assertions;
            assertions << "assert(#wesnoth.sides==" << plan.seats.size() << ", 'side count')\n";
            for(const auto& seat:plan.seats) {
                assertions << "do local u=wesnoth.units.get(" << quoted("alliance_practice_leader_"+seat.id) << ")\n"
                 << "assert(u and u.canrecruit and u.side=="<<seat.side<<", 'leader identity/side')\n"
                 << "assert(u.type=="<<quoted(seat.unit_type)<<" and u.level=="<<seat.level<<", 'saved unit type/level')\n"
                 << "assert(u.experience==0 and u.max_experience==1000000000, 'leader XP lock')\n"
                 << "assert(#u.advances_to==0, 'normal advancement must be disabled')\n"
                 << "local adv=wml.child_array(u.__cfg,'advancement'); assert(#adv==1 and adv[1].max_times==0, 'AMLA lock')\n"
                 << "local restored=u; for round=1,2 do restored=wesnoth.units.create(restored.__cfg)\n"
                 << "assert(restored.type==u.type and restored.level==u.level, 'saved leader type/level')\n"
                 << "assert(restored.experience==0 and restored.max_experience==1000000000, 'saved leader XP lock')\n"
                 << "assert(#restored.advances_to==0, 'saved leader regained normal advancement')\n"
                 << "local saved_adv=wml.child_array(restored.__cfg,'advancement'); assert(#saved_adv==1 and saved_adv[1].max_times==0, 'saved leader AMLA lock')\nend\n"
                 << "local loc=wesnoth.current.map.special_locations["<<quoted(std::to_string(seat.side))<<"]\n"
                 << "assert(loc and u.x==loc[1] and u.y==loc[2], 'map starting position')\n"
                 << "local side=wesnoth.sides["<<seat.side<<"]\n"
                 << "assert(side.controller=="<<quoted(seat.human?"human":"ai")<<", 'controller')\n"
                 << "assert(side.gold=="<<settings.starting_gold<<" and side.fog=="<<(settings.fog?"true":"false")<<", 'gold/fog')\n"
                 << "assert(#side.recruit>0, 'recruit availability')\nend\n";
                for(const auto& other:plan.seats) assertions << "assert(wesnoth.sides.is_enemy("<<seat.side<<","<<other.side<<")=="<<(seat.team_id!=other.team_id?"true":"false")<<", 'team alliance')\n";
            }
            assertions << "assert(wesnoth.scenario.turns=="<<settings.turn_limit<<", 'turn limit')\n"
                       << "assert(wml.variables.alliance_practice_viewer_side=="<<plan.controlled_side<<", 'viewer identity marker')\n"
                       << "assert(wml.variables.alliance_practice_control=="<<quoted(control)<<", 'control marker')\n"
                       << "assert(wml.variables.alliance_practice_starting_gold=="<<settings.starting_gold<<", 'starting gold marker')\n"
                       << "assert(wml.variables.alliance_practice_turn_limit=="<<settings.turn_limit<<", 'turn limit marker')\n"
                       << "assert(wml.variables.alliance_practice_fog=="<<(settings.fog?"true":"false")<<", 'fog marker')\n";
            assertions << "std_print('PRACTICE START VERIFIED " << index << "')\n";
            lua_event(generated,"start",assertions.str());
            std::ostringstream round;
            for(const auto& seat:plan.seats) {
                round<<"assert((wml.variables["<<quoted("fixture_side_"+std::to_string(seat.side))<<"] or 0)>=1, 'each side must play')\n";
                if(!seat.human) round<<"assert(#wesnoth.units.find_on_map(wml.parse('side="<<seat.side<<"'))>1, 'each AI must recruit')\n";
                config& turn=generated.add_child("event");
                turn["name"]="side "+std::to_string(seat.side)+" turn";turn["first_time_only"]=false;
                turn.add_child("lua")["code"]="local key="+quoted("fixture_side_"+std::to_string(seat.side))+"; wml.variables[key]=(wml.variables[key] or 0)+1";
            }
            round<<"std_print('PRACTICE ROUND VERIFIED "<<index<<"')";
            auto playable=generated;
            config& mp_round=playable.add_child("event");mp_round["name"]="turn 2";mp_round.add_child("lua")["code"]=round.str();
            for(const auto& seat:plan.seats) if(seat.human) {
                config& end=playable.add_child("event");end["name"]="side "+std::to_string(seat.side)+" turn";end["first_time_only"]=false;
                end.add_child("lua")["code"]="if wesnoth.current.turn==1 then wesnoth.wml_actions.end_turn(wml.parse('')) end";
            }
            if(index==0 || index==9 || index==16) {
                playable["id"]=scenario_id+"_save";
                config mp_root;mp_root.add_child("multiplayer",playable);
                std::ofstream mp_file(output/(scenario_id+"_save.cfg"));io::write(mp_file,mp_root);
                require(bool(mp_file),"multiplayer save scenario write failed");
            }
            if(index==0 || index==9 || index==16) {
                std::ostringstream resume;
                resume<<"assert(wml.variables.alliance_practice_control=="<<quoted(control)<<", 'saved control marker')\n"
                      <<"assert(wml.variables.alliance_practice_viewer_side=="<<plan.controlled_side<<", 'saved viewer')\n"
                      <<"assert(wml.variables.alliance_practice_starting_gold=="<<settings.starting_gold<<", 'saved gold setting')\n"
                      <<"assert(wml.variables.alliance_practice_turn_limit=="<<settings.turn_limit<<", 'saved turn setting')\n"
                      <<"assert(wml.variables.alliance_practice_fog=="<<(settings.fog?"true":"false")<<", 'saved fog setting')\n"
                      <<"assert(wesnoth.scenario.turns=="<<settings.turn_limit<<", 'saved scenario turns')\n";
                for(const auto& seat:plan.seats) {
                    resume<<"do local s=wesnoth.sides["<<seat.side<<"]; local u=wesnoth.units.get("<<quoted("alliance_practice_leader_"+seat.id)<<")\n"
                          <<"assert(s.controller=="<<quoted(seat.human?"human":"ai")<<" and s.is_local, 'saved local controller')\n"
                          <<"assert(s.fog=="<<(settings.fog?"true":"false")<<", 'saved fog')\n"
                          <<"assert(u and u.type=="<<quoted(seat.unit_type)<<" and u.level=="<<seat.level<<", 'saved leader')\n"
                          <<"assert(u.max_experience==1000000000 and #u.advances_to==0, 'saved leader advancement lock')\n"
                          <<"local adv=wml.child_array(u.__cfg,'advancement'); assert(#adv==1 and adv[1].max_times==0, 'saved AMLA lock')\nend\n";
                    for(const auto& other:plan.seats) resume<<"assert(wesnoth.sides.is_enemy("<<seat.side<<","<<other.side<<")=="<<(seat.team_id!=other.team_id?"true":"false")<<", 'saved alliance')\n";
                }
                std::ofstream saved_check(output/(scenario_id+"_resume.lua"));saved_check<<resume.str();require(bool(saved_check),"resume check write failed");
            }
            if(index==5) {
                // A real finite-turn all-human game. Do not override the normal
                // time-over result: observe its native defeat event instead.
                auto timeout=generated; timeout["id"]="alliance_practice_timeout";
                lua_event(timeout,"time over","std_print('PRACTICE TIME LIMIT REACHED')");
                lua_event(timeout,"defeat","std_print('PRACTICE STOCK TIMEOUT DEFEAT VERIFIED')");
                for(const auto& seat:plan.seats) {
                    config& end=timeout.add_child("event");end["name"]="side "+std::to_string(seat.side)+" turn";end["first_time_only"]=false;end.add_child("end_turn");
                }
                config timeout_root;timeout_root.add_child("multiplayer",timeout);
                std::ofstream timeout_file(output/"alliance_practice_timeout.cfg");io::write(timeout_file,timeout_root);
                require(bool(timeout_file),"timeout scenario write failed");
            }
            config& pass=generated.add_child("event"); pass["name"]="turn 2";
            pass.add_child("lua")["code"]=round.str()+"; unit_test.succeed()";
            for(const auto& seat:plan.seats) if(seat.human) {
                config& end=generated.add_child("event");end["name"]="side "+std::to_string(seat.side)+" turn";end["first_time_only"]=false;end.add_child("end_turn");
            }
            config root;root.add_child("test",generated);
            const auto path=output/(scenario_id+".cfg");std::ofstream result(path);io::write(result,root);
            require(bool(result),"scenario write failed");
            manifest<<scenario_id<<'\t'<<map.id<<'\t'<<plan.controlled_side<<'\t'<<plan.seats.size()<<'\t'<<control<<'\t'<<human<<'\t'<<settings.starting_gold<<'\t'<<settings.turn_limit<<'\t'<<settings.fog<<'\n'; ++scenarios;
        }
        require(exercised_units.size()==24,"not all saved leader types covered");
        require(written(data)==original_data,"installed source configurations changed");
        std::cout<<"PRACTICE SCENARIO EXPORT PASSED: scenarios="<<scenarios<<"; rejected_inputs="<<rejections<<"; unit_types=24\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr<<"PRACTICE SCENARIO EXPORT FAILED: "<<error.what()<<'\n';return 1;
    }
}
