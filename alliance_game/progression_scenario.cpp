#include "alliance_game/hero_catalog.hpp"
// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/progression_scenario.hpp"
#include "alliance_game/practice_plan.hpp"
#include <algorithm>
#include <sstream>
#include <stdexcept>
namespace alliance_game {
namespace {
std::string mission_map(int mission) {
    std::vector<std::vector<std::string>> cells(12,std::vector<std::string>(18,"Gg"));
    for(int y=0;y<12;++y) for(int x=0;x<18;++x) {
        if((x+y*3)%13==0) cells[y][x]="Gg^Fet";
        else if((x*3+y)%17==0) cells[y][x]="Hh";
    }
    if(mission==1) {
        for(int y=0;y<12;++y) cells[y][8]="Ww";
        cells[5][8]="Gg";cells[6][8]="Gg";
    }
    if(mission==2) for(int x=6;x<13;++x) { cells[2][x]="Hh";cells[9][x]="Gg^Fet"; }
    auto castle=[&](int x,int y,int side){
        for(int dy=-1;dy<=1;++dy) for(int dx=-1;dx<=1;++dx) cells[y+dy][x+dx]="Ce";
        cells[y][x]=std::to_string(side)+" Ke";
    };
    castle(2,5,1);castle(15,mission==2?3:5,2);if(mission==2) castle(15,8,3);
    for(auto xy : std::vector<std::pair<int,int>>{{5,3},{5,8},{11,3},{11,8},{8,2},{8,9}}) cells[xy.second][xy.first]="Gg^Vh";
    std::ostringstream out;
    for(const auto& row:cells) { bool first=true;for(const auto& c:row) { if(!first)out<<", ";out<<c;first=false;}out<<'\n'; }
    return out.str();
}
void endlevel(config& target,const char* result) {
    auto& end=target.add_child("endlevel");end["result"]=result;end["carryover_report"]=false;end["carryover_percentage"]=0;end["bonus"]=false;end["linger_mode"]=false;
}
void crossing_win(config& event) {
    auto& condition=event.add_child("if");
    condition.add_child("variable",config{"name","alliance_crossed_river","boolean_equals",true});
    condition.add_child("not").add_child("have_unit",config{"side","2","canrecruit",true});
    endlevel(condition.add_child("then"),"victory");
}
}
config make_progression_scenario(const ProgressionRun& run,const config& era) {
    if(!valid_progression_run(run)||era["id"].str()!="era_default") throw std::invalid_argument("Invalid mission registration or era.");
    const auto& missions=progression_missions();
    const auto at=std::find_if(missions.begin(),missions.end(),[&](const auto& m){return m.id==run.mission_id;});
    const int mission=static_cast<int>(at-missions.begin());
    const auto faction=era.find_child("multiplayer_side","id",progression_faction(run));
    if(!faction||(*faction)["recruit"].empty()) throw std::invalid_argument("Personal leader faction is unavailable.");
    config scenario;
    scenario["id"]="alliance_progression_"+run.id;
    scenario["name"]=at->name;scenario["description"]=at->description;
    scenario["map_data"]=mission_map(mission);scenario["turns"]=at->turns;
    scenario["next_scenario"]="";scenario["victory_when_enemies_defeated"]=mission!=1;
    scenario["experience_modifier"]=100;scenario["random_start_time"]=false;
    scenario["objectives"]="<big>"+at->name+"</big>\n"+at->description+
      "\nDefeat: your personal leader dies, or time runs out.\nYour selected hero keeps its registered form and Hero Level; recruits can advance in this mission.\nA completed victory earns 20 hero experience after automatic server confirmation. Defeat awards none.\nOnly this newly started mission can report a result. Quitting or interruption requires cancellation and a new run; loading a save does not resume progression.";
    scenario.add_child("time",config{"id","day","name","Day","image","misc/time-schedules/default/schedule-dawn.png","lawful_bonus",0});
    auto& vars=scenario.add_child("variables");
    vars["alliance_progression"]=true;vars["alliance_run_id"]=run.id;vars["alliance_account_id"]=run.account_id;
    vars["alliance_mission_id"]=run.mission_id;vars["alliance_mission_version"]=run.mission_version;
    vars["alliance_ruleset_version"]=run.ruleset_version;vars["alliance_unit_type"]=run.unit_type;vars["alliance_leader_level"]=run.leader_level;
    vars["alliance_crossed_river"]=false;
    for(int side=1;side<=(mission==2?3:2);++side) {
        auto& player=scenario.add_child("side");player["side"]=side;player["controller"]=side==1?"human":"ai";
        player["is_local"]=true;player["team_name"]=side==1?"alliance":"raiders";
        player["user_team_name"]=side==1?"Your company":"Enemy forces";player["persistent"]=false;
        player["gold"]=side==1?at->starting_gold:(mission==0?70:mission==1?100:90);
        player["income"]=0;player["village_gold"]=2;player["fog"]=true;player["shroud"]=false;
        player["recruit"]=side==1?(*faction)["recruit"].str():(side==3?"Skeleton,Skeleton Archer,Walking Corpse":mission==0?"Thug,Poacher,Footpad":"Orcish Grunt,Orcish Archer,Wolf Rider");
        auto& leader=player.add_child("leader");leader["id"]=side==1?"alliance_personal_leader":"alliance_enemy_"+std::to_string(side);
        leader["type"]=side==1?run.unit_type:(side==3?"Revenant":mission==0?"Outlaw":"Orcish Warrior");
        leader["name"]=side==1?run.username:(side==3?"The Silent Warden":mission==0?"Rovan the Raider":"Gorak");
        leader["canrecruit"]=true;leader["random_traits"]=false;leader["generate_name"]=false;
        if(side==1) {
            apply_hero_level(leader,run.hero_id,run.form_id,run.hero_level);
            leader["experience"]=0;leader["max_experience"]=1000000000;leader["advances_to"]="null";
            auto& lock=leader.mandatory_child("modifications").add_child("object");lock["id"]="alliance_progression_leader_lock";lock["duration"]="forever";lock["silent"]=true;
            lock.add_child("effect",config{"apply_to","remove_advancement","types",practice_advancement_targets()});
            leader.add_child("advancement",config{"id","alliance_progression_locked","max_times",0});
        }
    }
    auto& start=scenario.add_child("event");start["name"]="start";
    start.add_child("message",config{"speaker","alliance_personal_leader","message",at->description});
    auto& loss=scenario.add_child("event");loss["name"]="last breath";loss.add_child("filter",config{"id","alliance_personal_leader"});endlevel(loss,"defeat");
    auto& timeout=scenario.add_child("event");timeout["name"]="time over";endlevel(timeout,"defeat");
    if(mission==1) {
        auto& cross=scenario.add_child("event");cross["name"]="moveto";cross["first_time_only"]=false;
        cross.add_child("filter",config{"side",1,"x","10-18"});
        cross.add_child("set_variable",config{"name","alliance_crossed_river","value",true});crossing_win(cross);
        auto& die=scenario.add_child("event");die["name"]="die";die["first_time_only"]=false;
        die.add_child("filter",config{"side",2,"canrecruit",true});crossing_win(die);
    }
    return scenario;
}
bool progression_marker_matches(const config& scenario,const ProgressionRun& run) noexcept {
    const auto& v=scenario.child_or_empty("variables");
    return valid_progression_run(run)&&scenario["id"].str()=="alliance_progression_"+run.id&&v["alliance_progression"].to_bool()&&
      v["alliance_run_id"].str()==run.id&&v["alliance_account_id"].str()==run.account_id&&v["alliance_mission_id"].str()==run.mission_id&&
      v["alliance_mission_version"].str()==run.mission_version&&v["alliance_ruleset_version"].str()==run.ruleset_version&&
      v["alliance_unit_type"].str()==run.unit_type&&v["alliance_leader_level"].to_int()==run.leader_level;
}
}
