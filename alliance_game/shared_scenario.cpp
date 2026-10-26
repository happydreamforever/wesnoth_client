// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/shared_scenario.hpp"
#include "alliance_game/practice_scenario.hpp"
#include "alliance_game/hero_catalog.hpp"
#include "alliance_native/progression_protocol.hpp"
#include <map>
#include <set>
#include "serialization/parser.hpp"
#include <openssl/evp.h>
#include <algorithm>
#include <sstream>
#include <stdexcept>
namespace alliance_game {
PracticePlan make_shared_plan(const alliance::account::Battle& descriptor,const std::string& account_id,bool observer) {
    if(descriptor.state!="draft"&&descriptor.state!="running")throw std::invalid_argument("Shared plan is not active.");
    if(observer&&(descriptor.format!=2||descriptor.state!="running"||!descriptor.settings.observers))throw std::invalid_argument("Observing is unavailable.");
    if(descriptor.format==2) {
        if(descriptor.validity!="valid"||descriptor.teams.size()!=2||!alliance::progression_protocol::uuid(descriptor.id))throw std::invalid_argument("Shared plan is unavailable.");
        PracticePlan result;result.shared_format=2;result.battle_id=descriptor.id;result.map_id=descriptor.map.id;
        result.map_name=descriptor.map.name;result.scenario_id=descriptor.map.scenario_id;result.size=descriptor.map.size;
        result.shared_settings=descriptor.settings;result.engine_host_account_id=descriptor.engine_host_account_id;
        static const std::map<std::string,std::string> files={{"isars-cross","multiplayer/maps/4p_Isars_Cross.map"},{"hexcake","multiplayer/maps/6p_Hexcake.map"},{"mokena-prairie","multiplayer/maps/8p_Mokena_Prairie.map"}};
        const auto file=files.find(result.map_id);if(file==files.end())throw std::invalid_argument("Unknown shared map.");result.map_file=file->second;
        const auto& rules=result.shared_settings;
        if(rules.gold<0||rules.gold>1000||rules.base_income<0||rules.base_income>20||(rules.turns!=-1&&(rules.turns<1||rules.turns>1000)))throw std::invalid_argument("Invalid shared rules.");
        std::set<int> positions;std::set<std::string> humans,teams;std::vector<int> enabled;
        int host_side=9;std::string host;
        for(std::size_t t=0;t<2;++t) {
            const auto& team=descriptor.teams[t];int count=0,human_count=0;
            if(!team.approved||team.slots.size()!=static_cast<std::size_t>(result.size)||!teams.insert(team.id).second)throw std::invalid_argument("Shared team approval is missing.");
            for(const auto& slot:team.slots) {
                if(!positions.insert(slot.side).second||slot.side<1||slot.side>result.size*2||std::find(descriptor.map.sides.at(t).begin(),descriptor.map.sides.at(t).end(),slot.side)==descriptor.map.sides.at(t).end())throw std::invalid_argument("Invalid shared side.");
                PracticeSeat seat;seat.side=slot.side;seat.team_id=team.id;seat.team_name=team.name;
                seat.closed=slot.kind=="closed";seat.computer=slot.kind=="ai";
                if(seat.closed){seat.id="closed_"+std::to_string(slot.side);seat.username="Closed";}
                else if(seat.computer){++count;seat.id="computer_"+std::to_string(slot.side);seat.username="Computer "+std::to_string(slot.side);seat.faction=slot.ai_faction;seat.unit_type=slot.ai_leader;seat.level=2;if(seat.faction.empty()||seat.unit_type.empty())throw std::invalid_argument("AI selection missing.");}
                else {
                    if(slot.kind!="open"&&slot.kind!="reserved")throw std::invalid_argument("Invalid shared slot kind.");
                    auto player=std::find_if(team.players.begin(),team.players.end(),[&](const auto& p){return p.side==slot.side&&(slot.kind=="open"||p.id==slot.account_id);});
                    if(player==team.players.end()||!player->ready||!player->selection_present||!humans.insert(player->id).second)throw std::invalid_argument("Human seat is not ready.");
                    auto form=find_hero_form(player->hero_id,player->form_id);
                    if(!form||form->unit_type!=player->unit_type||form->native_level!=player->level||player->hero_level<1)throw std::invalid_argument("Human hero differs.");
                    ++count;++human_count;seat.id=player->id;seat.username=player->username;seat.faction=form->faction;seat.unit_type=player->unit_type;seat.level=player->level;
                    seat.hero_id=player->hero_id;seat.form_id=player->form_id;seat.hero_level=player->hero_level;seat.hero_revision=player->hero_revision;
                    seat.human=true;if(!observer&&player->id==account_id)result.controlled_side=slot.side;
                    if(slot.side<host_side){host_side=slot.side;host=player->id;}
                }
                result.seats.push_back(std::move(seat));
            }
            if(human_count<1||static_cast<std::size_t>(human_count)!=team.players.size())throw std::invalid_argument("Each team requires human players.");enabled.push_back(count);
        }
        if((!observer&&!result.controlled_side)||enabled[0]!=enabled[1]||host!=result.engine_host_account_id)throw std::invalid_argument("Invalid shared admission or host.");
        std::sort(result.seats.begin(),result.seats.end(),[](const auto&a,const auto&b){return a.side<b.side;});
        for(const auto& seat:result.seats)if(!observer&&(seat.side==result.controlled_side||(seat.computer&&host==account_id)))result.controlled_sides.push_back(seat.side);
        return result;
    }
    for(const auto& team:descriptor.teams)for(const auto& player:team.players)
        if(!player.ready)throw std::invalid_argument("Shared plan is not ready.");
    auto canonical=descriptor;canonical.state="draft";
    alliance::account::Profile viewer;viewer.account_id=account_id;
    PracticeSettings settings;settings.control=PracticeControl::hotseat;
    return make_practice_plan(canonical,viewer,settings);
}
config make_shared_scenario(const PracticePlan& plan,const config& source,const config& era) {
    if(plan.shared_format==2) {
        if(source["id"].str()!=plan.scenario_id||source["map_file"].str()!=plan.map_file||source.child_count("side")!=plan.seats.size()||era["id"].str()!="era_default")throw std::invalid_argument("Shared map or era differs.");
        config scenario=source;scenario.clear_children("side","options");scenario.clear_children("variables");
        scenario["id"]="alliance_shared_"+plan.battle_id;scenario["name"]=plan.map_name;
        scenario["description"]="Team battle";scenario["next_scenario"]="";scenario["turns"]=plan.shared_settings.turns;
        scenario["victory_when_enemies_defeated"]=true;scenario["experience_modifier"]=100;scenario["random_start_time"]=false;scenario["force_lock_settings"]=true;
        scenario["objectives"]="Defeat the opposing team.";
        auto& variables=scenario.add_child("variables");variables["alliance_shared"]=true;variables["alliance_shared_run_id"]=plan.battle_id;
        variables["alliance_shared_version"]="alliance-shared-v2";variables["simple_background"]=false;
        variables["alliance_engine_host"]=plan.engine_host_account_id;
        for(const auto& seat:plan.seats) {
            auto& side=scenario.add_child("side");side["side"]=seat.side;side["id"]="alliance_shared_side_"+seat.id;side["save_id"]=side["id"];
            side["team_name"]=seat.team_id;side["user_team_name"]=seat.team_name;side["name"]=seat.username;side["user_description"]=seat.username;
            side["controller"]=seat.closed?"null":seat.computer?"ai":"human";side["is_local"]=false;
            side["allow_player"]=!seat.closed&&!seat.computer;side["allow_changes"]=false;
            side["current_player"]=seat.computer?plan.engine_host_account_id:seat.username;
            if(seat.closed){side["no_leader"]=true;side["hidden"]=true;side["gold"]=0;continue;}
            auto faction=era.find_child("multiplayer_side","id",seat.faction);
            if(!faction||(*faction)["recruit"].empty())throw std::invalid_argument("Shared faction unavailable.");
            if(seat.computer) {
                const std::string allowed=","+(*faction)["leader"].str()+",";
                if(allowed.find(","+seat.unit_type+",")==std::string::npos)throw std::invalid_argument("AI leader is not in the approved faction.");
            }
            side["faction"]=seat.faction;side["recruit"]=(*faction)["recruit"];side["gold"]=plan.shared_settings.gold;side["income"]=plan.shared_settings.base_income-2;
            side["village_gold"]=2;side["village_support"]=1;side["fog"]=plan.shared_settings.fog;side["shroud"]=plan.shared_settings.shroud;
            side["share_view"]=true;side["share_maps"]=true;side["persistent"]=false;side["disallow_shuffle"]=true;
            for(const char* lock:{"leader_lock","faction_lock","controller_lock","team_lock","gold_lock","income_lock","color_lock"})side[lock]=true;
            auto& leader=side.add_child("leader");leader["id"]="alliance_shared_leader_"+seat.id;leader["name"]=seat.username;leader["type"]=seat.unit_type;
            leader["canrecruit"]=true;leader["random_traits"]=false;leader["generate_name"]=false;leader["experience"]=0;
            if(!seat.computer) {
                apply_hero_level(leader,seat.hero_id,seat.form_id,seat.hero_level);leader["max_experience"]=1000000000;leader["advances_to"]="null";
                auto& lock=leader.mandatory_child("modifications").add_child("object");lock["id"]="alliance_shared_leader_lock";lock["duration"]="forever";lock["silent"]=true;
                lock.add_child("effect",config{"apply_to","remove_advancement","types",practice_advancement_targets()});
                leader.add_child("advancement",config{"id","alliance_shared_locked","max_times",0});
            }
            for(const auto& ai:(*faction).child_range("ai"))side.add_child("ai",ai);
        }
        return scenario;
    }
    auto common=plan;common.controlled_side=1;
    auto scenario=make_practice_scenario(common,source,era);
    scenario["id"]="alliance_shared_"+plan.battle_id;
    scenario["name"]="Alliance battle - "+plan.map_name;
    scenario["description"]="Shared Alliance battle using the approved fixed lineups.";
    scenario["objectives"]="<big>Alliance team battle</big>\nDefeat every opposing leader. All selected players control their own sides over the local network.\nPersonal leaders keep their saved types; recruits may advance during this match.\nThe recorded result requires both captains' confirmation before the Windows server awards progression.";
    scenario["turns"]=-1;
    scenario.clear_children("variables");
    auto& variables=scenario.add_child("variables");
    variables["alliance_shared"]=true;variables["alliance_shared_run_id"]=plan.battle_id;
    variables["alliance_shared_version"]="alliance-shared-v1";
    variables["simple_background"]=false;
    for(auto& side:scenario.child_range("side")) {
        const auto number=side["side"].to_int();
        const auto found=std::find_if(plan.seats.begin(),plan.seats.end(),[&](const auto& seat){return seat.side==number;});
        if(found==plan.seats.end())throw std::invalid_argument("Shared side is missing.");
        side["id"]="alliance_shared_side_"+found->id;side["save_id"]=side["id"];
        side["current_player"]=found->username;side["controller"]="human";side["is_local"]=false;
        side["allow_player"]=true;side["allow_changes"]=false;
        side.mandatory_child("leader")["id"]="alliance_shared_leader_"+found->id;
    }
    return scenario;
}
std::string shared_scenario_digest(const config& scenario) {
    std::ostringstream output;io::write(output,scenario);const auto bytes=output.str();
    if(bytes.empty()||bytes.size()>2*1024*1024)throw std::invalid_argument("Shared scenario exceeds its limit.");
    unsigned char hash[EVP_MAX_MD_SIZE];unsigned int size=0;
    if(EVP_Digest(bytes.data(),bytes.size(),hash,&size,EVP_sha256(),nullptr)!=1||size!=32)throw std::runtime_error("Scenario hashing unavailable.");
    static const char hex[]="0123456789abcdef";std::string value;
    for(unsigned int i=0;i<size;++i){value+=hex[hash[i]>>4];value+=hex[hash[i]&15];}return value;
}
void localize_shared_scenario(config& scenario,const PracticePlan& plan) {
    if(plan.shared_format==1){localize_shared_scenario(scenario,plan.controlled_side);return;}
    for(auto& side:scenario.child_range("side")) {
        const int number=side["side"].to_int();const auto seat=std::find_if(plan.seats.begin(),plan.seats.end(),[&](const auto& s){return s.side==number;});
        if(seat==plan.seats.end())throw std::invalid_argument("Shared side missing.");
        side["is_local"]=std::find(plan.controlled_sides.begin(),plan.controlled_sides.end(),number)!=plan.controlled_sides.end();
        side["controller"]=seat->closed?"null":seat->computer?"ai":"human";
    }
}
void localize_shared_scenario(config& scenario,int admitted_side) {
    bool found=false;
    for(auto& side:scenario.child_range("side")) {
        const bool own=side["side"].to_int()==admitted_side;found=found||own;
        side["is_local"]=own;side["controller"]="human";
    }
    if(!found)throw std::invalid_argument("The admitted side is missing.");
}
}
