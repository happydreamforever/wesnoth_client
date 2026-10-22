// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/practice_scenario.hpp"
#include <stdexcept>

namespace alliance_game {
config make_practice_scenario(const PracticePlan& plan, const config& original, const config& era)
{
    if(original["id"].str() != plan.scenario_id || original["map_file"].str() != plan.map_file ||
       original.child_count("side") != plan.seats.size() || era["id"].str() != "era_default")
        throw std::invalid_argument("The installed map or era does not match this practice plan.");
    config scenario = original;
    scenario["id"] = "alliance_practice_" + plan.battle_id + "_" + std::to_string(plan.controlled_side);
    scenario["name"] = "Alliance practice - " + plan.map_name + " - side " + std::to_string(plan.controlled_side);
    scenario["description"] = "Unranked local practice using a saved lineup. All other sides are computer players. No account experience, team experience, rating or battle-plan changes are saved.";
    scenario["next_scenario"] = "";
    scenario["turns"] = -1;
    scenario["victory_when_enemies_defeated"] = true;
    scenario["experience_modifier"] = 100;
    scenario["random_start_time"] = false;
    scenario["force_lock_settings"] = true;
    scenario["objectives"] = "<big>Local team practice</big>\nDefeat the opposing team's leaders.\nYou control only your saved side; every other side is controlled by the computer.\nThis is a saved lineup, not a live membership check.\nNo persistent experience or ratings are awarded. Leaders keep their saved type; recruited units may advance locally.";
    scenario.clear_children("side", "options");
    auto& marker = scenario.child_or_add("variables");
    marker["alliance_practice"] = true;
    marker["alliance_practice_plan_id"] = plan.battle_id;
    marker["alliance_practice_viewer_side"] = plan.controlled_side;
    marker["simple_background"] = false;
    for(const auto& seat : plan.seats) {
        const auto faction = era.find_child("multiplayer_side", "id", seat.faction);
        if(!faction || (*faction)["recruit"].empty())
            throw std::invalid_argument("The installed recruitment faction is unavailable.");
        config& side = scenario.add_child("side");
        side["side"] = seat.side;
        side["id"] = "alliance_practice_side_" + seat.id;
        side["save_id"] = side["id"];
        side["controller"] = seat.human ? "human" : "ai";
        side["is_local"] = true;
        side["allow_player"] = seat.human;
        side["allow_changes"] = false;
        side["team_name"] = seat.team_id;
        side["user_team_name"] = seat.team_name;
        side["name"] = seat.username;
        side["user_description"] = seat.username;
        side["faction"] = seat.faction;
        side["recruit"] = (*faction)["recruit"];
        side["gold"] = 100;
        // WML income is added to the core base income (2).
        side["income"] = 0;
        side["village_gold"] = 2;
        side["village_support"] = 1;
        side["fog"] = true;
        side["shroud"] = false;
        side["share_view"] = true;
        side["share_maps"] = true;
        side["persistent"] = false;
        side["disallow_shuffle"] = true;
        for(const char* lock : {"leader_lock", "faction_lock", "controller_lock", "team_lock", "gold_lock", "income_lock", "color_lock"}) side[lock] = true;
        config& leader = side.add_child("leader");
        leader["id"] = "alliance_practice_leader_" + seat.id;
        leader["name"] = seat.username;
        leader["type"] = seat.unit_type;
        leader["canrecruit"] = true;
        leader["random_traits"] = false;
        leader["generate_name"] = false;
        leader["gender"] = "male";
        leader["experience"] = 0;
        leader["max_experience"] = 1000000000;
        leader["advances_to"] = "null";
        // unit::write serializes an empty advances_to vector as an empty
        // string; the stock reader then retains the type's default advances.
        // Saved modifications are reapplied on every construction, so remove
        // the permitted branches persistently as well as on this first load.
        auto& lock = leader.add_child("modifications").add_child("object");
        lock["id"] = "alliance_practice_leader_lock";
        lock["duration"] = "forever";
        lock["silent"] = true;
        lock.add_child("effect", config{"apply_to", "remove_advancement", "types", practice_advancement_targets()});
        // Explicit advancement replaces the unit type's AMLA list; max_times=0
        // disables it. The copied persistent leader cannot change type or AMLA.
        leader.add_child("advancement", config{"id", "alliance_practice_locked", "max_times", 0});
        for(const config& ai : (*faction).child_range("ai")) side.add_child("ai", ai);
    }
    return scenario;
}
}
