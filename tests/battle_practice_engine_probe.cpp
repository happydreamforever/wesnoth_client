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
        for(int index=0; index<8; ++index) {
            const Map& map=maps[index<6?0:index-5];
            const auto source=data.find_child("multiplayer","id",map.scenario); require(bool(source),"real map missing");
            alliance::account::Battle battle;
            battle.id=uuid(100+index); battle.state=index%2?"cancelled":"draft";
            battle.map.id=map.id; battle.map.scenario_id=map.scenario;
            battle.map.size=static_cast<int>(map.sides[0].size()); battle.map.sides=map.sides;
            for(int team=0;team<2;++team) {
                alliance::account::BattleTeam t;
                t.id=uuid(10+team); t.name=team?"Fixture Beta":"Fixture Alpha";
                for(std::size_t slot=0;slot<map.sides[team].size();++slot) {
                    const int side=map.sides[team][slot];
                    const Unit& unit=units[(index<6?index*4+side-1:side*3+index)%units.size()];
                    t.players.push_back({uuid(side),"Fixture_"+std::to_string(side),unit.type,unit.level,side,false});
                }
                t.captain_id=t.players.front().id; battle.teams.push_back(t);
            }
            alliance::account::Profile viewer;
            viewer.account_id=uuid(index%2?battle.map.size*2:1);
            const auto plan=alliance_game::make_practice_plan(battle,viewer);
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
            require(human==1,"practice requires exactly one human side");
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
                 << "assert(side.gold==100 and side.fog, 'gold/fog')\n"
                 << "assert(#side.recruit>0, 'recruit availability')\nend\n";
                for(const auto& other:plan.seats) assertions << "assert(wesnoth.sides.is_enemy("<<seat.side<<","<<other.side<<")=="<<(seat.team_id!=other.team_id?"true":"false")<<", 'team alliance')\n";
            }
            assertions << "std_print('PRACTICE START VERIFIED " << index << "')\n";
            lua_event(generated,"start",assertions.str());
            config& pass=generated.add_child("event"); pass["name"]="turn 2";
            pass.add_child("lua")["code"]="assert(#wesnoth.units.find_on_map() > "+std::to_string(plan.seats.size())+", 'AI must recruit in the first round'); std_print('PRACTICE AI ROUND VERIFIED "+std::to_string(index)+"'); unit_test.succeed()";
            config& end=generated.add_child("event");end["name"]="side "+std::to_string(plan.controlled_side)+" turn";end["first_time_only"]=false;end.add_child("end_turn");
            config root;root.add_child("test",generated);
            const auto path=output/(scenario_id+".cfg");std::ofstream result(path);io::write(result,root);
            require(bool(result),"scenario write failed");
            manifest<<scenario_id<<'\t'<<map.id<<'\t'<<plan.controlled_side<<'\t'<<plan.seats.size()<<'\n'; ++scenarios;
        }
        require(written(data)==original_data,"installed source configurations changed");
        std::cout<<"PRACTICE SCENARIO EXPORT PASSED: scenarios="<<scenarios<<"; rejected_inputs="<<rejections<<"; unit_types=24\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr<<"PRACTICE SCENARIO EXPORT FAILED: "<<error.what()<<'\n';return 1;
    }
}
