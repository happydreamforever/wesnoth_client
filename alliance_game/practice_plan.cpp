// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/practice_plan.hpp"
#include "alliance_game/battle_launch.hpp"
#include <algorithm>
#include <map>
#include <set>
#include <stdexcept>
#include <tuple>
#include <utility>

namespace alliance_game {
namespace {
std::optional<PracticePlan> pending;
bool uuid(const std::string& value)
{
    if(value.size() != 36) return false;
    for(std::size_t i = 0; i < value.size(); ++i) {
        if(i == 8 || i == 13 || i == 18 || i == 23) {
            if(value[i] != '-') return false;
        } else if(!((value[i] >= '0' && value[i] <= '9') || (value[i] >= 'a' && value[i] <= 'f'))) return false;
    }
    return true;
}
bool alnum(char c) { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9'); }
bool username(const std::string& value)
{
    return value.size() >= 3 && value.size() <= 24 && std::all_of(value.begin(), value.end(), [](char c) { return alnum(c) || c == '_'; });
}
bool team_name(const std::string& value)
{
    return value.size() >= 3 && value.size() <= 40 && alnum(value.front()) && value.back() != ' ' &&
        std::all_of(value.begin(), value.end(), [](char c) { return alnum(c) || c == ' ' || c == '_' || c == '-'; });
}
struct Map { const char* name; const char* scenario; const char* file; std::vector<std::vector<int>> sides; };
const std::map<std::string, Map> maps = {
    {"isars-cross", {"Isar's Cross", "multiplayer_Isars_Cross", "multiplayer/maps/4p_Isars_Cross.map", {{1,4},{2,3}}}},
    {"hexcake", {"Hexcake", "multiplayer_Hexcake", "multiplayer/maps/6p_Hexcake.map", {{1,4,5},{2,3,6}}}},
    {"mokena-prairie", {"Mokena Prairie", "multiplayer_8p_Mokena_Prairie", "multiplayer/maps/8p_Mokena_Prairie.map", {{1,3,5,8},{2,4,6,7}}}}
};
struct Unit { int level; const char* faction; };
const std::map<std::string, Unit> units = {
    {"Spearman",{1,"Loyalists"}}, {"Swordsman",{2,"Loyalists"}}, {"Pikeman",{2,"Loyalists"}},
    {"Javelineer",{2,"Loyalists"}}, {"Royal Guard",{3,"Loyalists"}}, {"Halberdier",{3,"Loyalists"}},
    {"Elvish Fighter",{1,"Rebels"}}, {"Elvish Captain",{2,"Rebels"}}, {"Elvish Hero",{2,"Rebels"}},
    {"Elvish Marshal",{3,"Rebels"}}, {"Elvish Champion",{3,"Rebels"}},
    {"Orcish Grunt",{1,"Northerners"}}, {"Orcish Warrior",{2,"Northerners"}}, {"Orcish Warlord",{3,"Northerners"}},
    {"Dwarvish Fighter",{1,"Knalgan Alliance"}}, {"Dwarvish Steelclad",{2,"Knalgan Alliance"}}, {"Dwarvish Lord",{3,"Knalgan Alliance"}},
    {"Drake Fighter",{1,"Drakes"}}, {"Drake Warrior",{2,"Drakes"}}, {"Drake Blademaster",{3,"Drakes"}},
    {"Skeleton",{1,"Undead"}}, {"Revenant",{2,"Undead"}}, {"Deathblade",{2,"Undead"}}, {"Draug",{3,"Undead"}}
};
PracticeEligibility evaluate_practice(const alliance::account::Battle& battle,
                                     const alliance::account::Profile& viewer, PracticePlan& result)
{
    if(!uuid(battle.id) || !uuid(viewer.account_id) ||
       (battle.state != "draft" && battle.state != "cancelled" && battle.state != "running" && battle.state != "completed") ||
       battle.teams.size() != 2) return PracticeEligibility::invalid_plan;
    const auto map = maps.find(battle.map.id);
    if(map == maps.end() || battle.map.scenario_id != map->second.scenario ||
       battle.map.size != static_cast<int>(map->second.sides[0].size()) || battle.map.sides != map->second.sides)
        return PracticeEligibility::invalid_plan;
    if(!battle.teams[0].captain_id.empty() && battle.teams[0].captain_id == battle.teams[1].captain_id)
        return PracticeEligibility::invalid_plan;
    result.battle_id = battle.id;
    result.map_id = battle.map.id;
    result.map_name = map->second.name;
    result.scenario_id = map->second.scenario;
    result.map_file = map->second.file;
    result.size = battle.map.size;
    std::set<std::string> teams, players;
    bool approved = true, viewer_ready = false;
    for(std::size_t t = 0; t < battle.teams.size(); ++t) {
        const auto& team = battle.teams[t];
        if(!uuid(team.id) || (!team.captain_id.empty() && !uuid(team.captain_id)) || (team.approved && team.captain_id.empty()) ||
           !team_name(team.name) || !teams.insert(team.id).second ||
           team.players.size() != static_cast<std::size_t>(result.size)) return PracticeEligibility::invalid_plan;
        approved = approved && !team.captain_id.empty() && team.approved;
        for(std::size_t p = 0; p < team.players.size(); ++p) {
            const auto& player = team.players[p];
            const auto unit = units.find(player.unit_type);
            if(!uuid(player.id) || !username(player.username) || !players.insert(player.id).second ||
               player.side != map->second.sides[t][p] || unit == units.end() || player.level != unit->second.level)
                return PracticeEligibility::invalid_plan;
            const bool human = player.id == viewer.account_id;
            if(human) {
                result.controlled_side = player.side;
                viewer_ready = player.ready;
            }
            result.seats.push_back({player.id, player.username, team.id, team.name, unit->second.faction,
                                    player.unit_type, player.level, player.side, human});
        }
    }
    if(!result.controlled_side) return PracticeEligibility::not_selected;
    if(battle.state != "draft") return PracticeEligibility::inactive_plan;
    if(battle.validity != "valid") return PracticeEligibility::unavailable_plan;
    if(!approved) return PracticeEligibility::captain_approval_required;
    if(!viewer_ready) return PracticeEligibility::viewer_readiness_required;
    std::sort(result.seats.begin(), result.seats.end(), [](const auto& a, const auto& b) { return a.side < b.side; });
    return PracticeEligibility::eligible;
}
}

PracticeEligibility practice_eligibility(const alliance::account::Battle& battle, const alliance::account::Profile& viewer)
{
    PracticePlan result;
    return evaluate_practice(battle, viewer, result);
}

const char* practice_eligibility_reason(PracticeEligibility status) noexcept
{
    switch(status) {
    case PracticeEligibility::eligible: return "";
    case PracticeEligibility::inactive_plan: return "Local practice is available only for a draft plan.";
    case PracticeEligibility::unavailable_plan: return "This plan is unavailable or its teams or leaders have changed. Refresh the plan.";
    case PracticeEligibility::captain_approval_required: return "Both current captains must approve this plan before local practice.";
    case PracticeEligibility::viewer_readiness_required: return "Choose Ready for your own assigned side before local practice.";
    case PracticeEligibility::not_selected: return "Only a player in the fixed lineups can practice this plan.";
    case PracticeEligibility::invalid_plan: break;
    }
    return "This plan has an invalid saved lineup and cannot be practiced.";
}

bool same_practice_snapshot(const alliance::account::Battle& first, const alliance::account::Battle& second,
                            const std::string& viewer_account_id) noexcept
{
    if(!uuid(viewer_account_id)) return false;
    if(std::tie(first.id, first.area_id, first.state, first.created_at, first.validity,
                first.map.id, first.map.name, first.map.scenario_id, first.map.size, first.map.sides) !=
       std::tie(second.id, second.area_id, second.state, second.created_at, second.validity,
                second.map.id, second.map.name, second.map.scenario_id, second.map.size, second.map.sides) ||
       first.teams.size() != second.teams.size()) return false;
    bool viewer_found = false;
    for(std::size_t t = 0; t < first.teams.size(); ++t) {
        const auto& a = first.teams[t];
        const auto& b = second.teams[t];
        if(std::tie(a.id, a.name, a.captain_id, a.approved) != std::tie(b.id, b.name, b.captain_id, b.approved) ||
           a.players.size() != b.players.size()) return false;
        for(std::size_t p = 0; p < a.players.size(); ++p) {
            const auto& left = a.players[p];
            const auto& right = b.players[p];
            if(std::tie(left.id, left.username, left.unit_type, left.level, left.side) !=
               std::tie(right.id, right.username, right.unit_type, right.level, right.side)) return false;
            if(left.id == viewer_account_id) {
                if(viewer_found || left.ready != right.ready) return false;
                viewer_found = true;
            }
        }
    }
    return viewer_found;
}

PracticePlan make_practice_plan(const alliance::account::Battle& battle, const alliance::account::Profile& viewer)
{
    PracticePlan result;
    const auto status = evaluate_practice(battle, viewer, result);
    if(status != PracticeEligibility::eligible) throw std::invalid_argument(practice_eligibility_reason(status));
    return result;
}

std::string practice_advancement_targets()
{
    std::string result;
    for(const auto& entry : units) {
        if(!result.empty()) result += ',';
        result += entry.first;
    }
    return result;
}

void queue_practice(const alliance::account::Battle& battle, const alliance::account::Profile& viewer)
{
    if(pending) throw std::logic_error("A local practice launch is already pending.");
    pending = make_practice_plan(battle, viewer);
}
bool practice_pending() noexcept { return pending.has_value(); }
std::optional<PracticePlan> take_pending_practice()
{
    auto result = std::move(pending);
    pending.reset();
    return result;
}
}
