// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/progression_plan.hpp"
#include <algorithm>
#include <map>
#include <stdexcept>
#include <tuple>
namespace alliance_game {
namespace {
std::optional<ProgressionRun> pending;
bool hex(const std::string& v, std::size_t size) {
    return v.size()==size && std::all_of(v.begin(),v.end(),[](char c){return (c>='0'&&c<='9')||(c>='a'&&c<='f');});
}
bool uuid(std::string value) {
    if(value.size()!=36) return false;
    for(std::size_t p : {23U,18U,13U,8U}) { if(value[p]!='-') return false; value.erase(p,1); }
    return hex(value,32);
}
struct Unit { int level; const char* faction; };
const std::map<std::string,Unit> units = {
 {"Spearman",{1,"Loyalists"}},{"Swordsman",{2,"Loyalists"}},{"Pikeman",{2,"Loyalists"}},{"Javelineer",{2,"Loyalists"}},{"Royal Guard",{3,"Loyalists"}},{"Halberdier",{3,"Loyalists"}},
 {"Elvish Fighter",{1,"Rebels"}},{"Elvish Captain",{2,"Rebels"}},{"Elvish Hero",{2,"Rebels"}},{"Elvish Marshal",{3,"Rebels"}},{"Elvish Champion",{3,"Rebels"}},
 {"Orcish Grunt",{1,"Northerners"}},{"Orcish Warrior",{2,"Northerners"}},{"Orcish Warlord",{3,"Northerners"}},
 {"Dwarvish Fighter",{1,"Knalgan Alliance"}},{"Dwarvish Steelclad",{2,"Knalgan Alliance"}},{"Dwarvish Lord",{3,"Knalgan Alliance"}},
 {"Drake Fighter",{1,"Drakes"}},{"Drake Warrior",{2,"Drakes"}},{"Drake Blademaster",{3,"Drakes"}},
 {"Skeleton",{1,"Undead"}},{"Revenant",{2,"Undead"}},{"Deathblade",{2,"Undead"}},{"Draug",{3,"Undead"}}
};
}
const std::vector<ProgressionMission>& progression_missions() {
    static const std::vector<ProgressionMission> missions={
      {"alliance_training_1","Border Patrol","Drive the raiders away from the border villages. Recruit troops and defeat the bandit captain.",20,140},
      {"alliance_training_2","The River Crossing","Cross the guarded river with one of your units, then defeat the orc commander.",24,160},
      {"alliance_training_3","The Old Fortress","Break the siege by defeating both enemy commanders. Keep your personal leader alive.",30,200}
    };
    return missions;
}
bool valid_progression_run(const ProgressionRun& run) noexcept {
    const auto unit=units.find(run.unit_type);
    return uuid(run.id)&&uuid(run.account_id)&&hex(run.nonce,64)&&run.username.size()>=3&&run.username.size()<=24&&
      std::all_of(run.username.begin(),run.username.end(),[](char c){return (c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9')||c=='_';})&&
      run.mission_version==progression_mission_version&&run.ruleset_version=="alliance-v1"&&
      std::any_of(progression_missions().begin(),progression_missions().end(),[&](const auto& m){return m.id==run.mission_id;})&&
      unit!=units.end()&&unit->second.level==run.leader_level;
}
bool same_progression_run(const ProgressionRun& a,const ProgressionRun& b) noexcept {
    return std::tie(a.id,a.account_id,a.username,a.mission_id,a.mission_version,a.ruleset_version,a.unit_type,a.nonce,a.leader_level)==
           std::tie(b.id,b.account_id,b.username,b.mission_id,b.mission_version,b.ruleset_version,b.unit_type,b.nonce,b.leader_level);
}
std::string progression_faction(const ProgressionRun& run) {
    if(!valid_progression_run(run)) throw std::invalid_argument("Invalid registered mission.");
    return units.at(run.unit_type).faction;
}
void queue_progression(const ProgressionRun& run) {
    if(!valid_progression_run(run)||pending) throw std::invalid_argument("Invalid or already queued mission.");
    pending=run;
}
bool progression_pending() noexcept { return pending.has_value(); }
std::optional<ProgressionRun> take_pending_progression() { auto result=std::move(pending);pending.reset();return result; }
}
