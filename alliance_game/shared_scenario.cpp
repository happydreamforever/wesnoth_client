// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/shared_scenario.hpp"
#include "alliance_game/practice_scenario.hpp"
#include "serialization/parser.hpp"
#include <openssl/evp.h>
#include <algorithm>
#include <sstream>
#include <stdexcept>
namespace alliance_game {
PracticePlan make_shared_plan(const alliance::account::Battle& descriptor,const std::string& account_id) {
    if(descriptor.state!="draft"&&descriptor.state!="running")throw std::invalid_argument("Shared plan is not active.");
    for(const auto& team:descriptor.teams)for(const auto& player:team.players)
        if(!player.ready)throw std::invalid_argument("Shared plan is not ready.");
    auto canonical=descriptor;canonical.state="draft";
    alliance::account::Profile viewer;viewer.account_id=account_id;
    PracticeSettings settings;settings.control=PracticeControl::hotseat;
    return make_practice_plan(canonical,viewer,settings);
}
config make_shared_scenario(const PracticePlan& plan,const config& source,const config& era) {
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
void localize_shared_scenario(config& scenario,int admitted_side) {
    bool found=false;
    for(auto& side:scenario.child_range("side")) {
        const bool own=side["side"].to_int()==admitted_side;found=found||own;
        side["is_local"]=own;side["controller"]="human";
    }
    if(!found)throw std::invalid_argument("The admitted side is missing.");
}
}
