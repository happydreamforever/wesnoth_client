// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/battle_launch.hpp"
#include "alliance_game/practice_plan.hpp"
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>

using namespace alliance_game;
using namespace alliance::account;
namespace {
int checks = 0;
void require(bool value) { ++checks; if(!value) throw std::runtime_error("practice fixture failed"); }
std::string id(int n) { std::ostringstream out; out << "00000000-0000-4000-8000-" << std::setfill('0') << std::setw(12) << n; return out.str(); }
Battle battle(int size = 2)
{
    Battle b;
    b.id = id(100); b.area_id = id(101); b.state = "draft";
    b.map.id = size == 2 ? "isars-cross" : size == 3 ? "hexcake" : "mokena-prairie";
    b.map.scenario_id = size == 2 ? "multiplayer_Isars_Cross" : size == 3 ? "multiplayer_Hexcake" : "multiplayer_8p_Mokena_Prairie";
    b.map.size = size;
    b.map.sides = size == 2 ? std::vector<std::vector<int>>{{1,4},{2,3}} :
        size == 3 ? std::vector<std::vector<int>>{{1,4,5},{2,3,6}} : std::vector<std::vector<int>>{{1,3,5,8},{2,4,6,7}};
    for(int t = 0; t < 2; ++t) {
        BattleTeam team;
        team.id = id(200+t); team.name = t ? "RivalPlan" : "Koichi"; team.captain_id = id(t*size+1);
        for(int p = 0; p < size; ++p) {
            BattlePlayer player;
            const int number = t*size+p+1;
            player.id = id(number); player.username = "Player" + std::to_string(number);
            player.unit_type = "Spearman"; player.level = 1; player.side = b.map.sides[t][p];
            team.players.push_back(player);
        }
        b.teams.push_back(team);
    }
    return b;
}
template<class F> void rejects(F fn)
{
    bool rejected = false;
    try { fn(); } catch(const std::invalid_argument&) { rejected = true; }
    require(rejected);
}
}
int main()
{
    try {
        Profile viewer; viewer.account_id = id(1);
        for(int size : {2,3,4}) {
            const auto b = battle(size);
            for(const auto& team : b.teams) for(const auto& player : team.players) {
                viewer.account_id = player.id;
                const auto plan = make_practice_plan(b, viewer);
                require(plan.seats.size() == static_cast<std::size_t>(size*2));
                require(plan.controlled_side == player.side);
                int humans = 0;
                for(std::size_t i = 0; i < plan.seats.size(); ++i) {
                    const auto& seat = plan.seats[i];
                    require(seat.side == static_cast<int>(i+1));
                    require(seat.unit_type == "Spearman" && seat.faction == "Loyalists" && seat.level == 1);
                    if(seat.human) { ++humans; require(seat.id == viewer.account_id); }
                }
                require(humans == 1);
            }
        }
        viewer.account_id = id(1);
        const std::pair<const char*, int> branches[] = {
            {"Spearman",1},{"Swordsman",2},{"Pikeman",2},{"Javelineer",2},{"Royal Guard",3},{"Halberdier",3},
            {"Elvish Fighter",1},{"Elvish Captain",2},{"Elvish Hero",2},{"Elvish Marshal",3},{"Elvish Champion",3},
            {"Orcish Grunt",1},{"Orcish Warrior",2},{"Orcish Warlord",3},
            {"Dwarvish Fighter",1},{"Dwarvish Steelclad",2},{"Dwarvish Lord",3},
            {"Drake Fighter",1},{"Drake Warrior",2},{"Drake Blademaster",3},
            {"Skeleton",1},{"Revenant",2},{"Deathblade",2},{"Draug",3}};
        for(const auto& branch : branches) {
            auto b = battle(); b.teams[0].players[0].unit_type = branch.first; b.teams[0].players[0].level = branch.second;
            const auto plan = make_practice_plan(b, viewer);
            require(plan.seats[0].unit_type == branch.first && plan.seats[0].level == branch.second);
            // This same allowlist is used by the saved object's persistent
            // remove_advancement effect, including every branching target.
            require(("," + practice_advancement_targets() + ",").find("," + std::string(branch.first) + ",") != std::string::npos);
        }
        auto b = battle(); b.state = "cancelled"; b.validity = "membership_changed";
        viewer.unit_type = "Royal Guard"; viewer.level = 3; viewer.experience = 123;
        require(make_practice_plan(b, viewer).seats[0].unit_type == "Spearman");
        b.teams[1].captain_id.clear(); b.validity = "team_unavailable";
        require(make_practice_plan(b, viewer).seats[0].unit_type == "Spearman");
        for(const char* state : {"running", "completed", "disputed", "", "Draft"}) {
            auto bad = battle(); bad.state = state; rejects([&] { make_practice_plan(bad,viewer); });
        }
        auto reject_changed = [&](auto mutate) { auto bad = battle(); mutate(bad); rejects([&] { make_practice_plan(bad,viewer); }); };
        reject_changed([](auto& v) { v.id = "bad-id"; });
        reject_changed([](auto& v) { v.map.id = "unknown"; });
        reject_changed([](auto& v) { v.map.scenario_id = "multiplayer_The_Freelands"; });
        reject_changed([](auto& v) { v.map.size = 3; });
        reject_changed([](auto& v) { v.map.sides[0][0] = 2; });
        reject_changed([](auto& v) { v.teams.pop_back(); });
        reject_changed([](auto& v) { v.teams.push_back(v.teams[0]); });
        reject_changed([](auto& v) { v.teams[1].id = v.teams[0].id; });
        reject_changed([](auto& v) { v.teams[0].id = "bad"; });
        reject_changed([](auto& v) { v.teams[0].captain_id = "bad-captain"; });
        reject_changed([](auto& v) { v.teams[0].name = "bad\nname"; });
        reject_changed([](auto& v) { v.teams[0].players.pop_back(); });
        reject_changed([](auto& v) { v.teams[1].players[0].id = v.teams[0].players[0].id; });
        reject_changed([](auto& v) { v.teams[0].players[0].side = 2; });
        reject_changed([](auto& v) { std::swap(v.teams[0].players[0],v.teams[0].players[1]); });
        reject_changed([](auto& v) { v.teams[0].players[0].username = "bad name"; });
        reject_changed([](auto& v) { v.teams[0].players[0].username = std::string(25,'a'); });
        reject_changed([](auto& v) { v.teams[0].players[0].unit_type = "Ancient Lich"; });
        reject_changed([](auto& v) { v.teams[0].players[0].level = 2; });
        auto outsider = viewer; outsider.account_id = id(900);
        rejects([&] { make_practice_plan(battle(),outsider); });
        outsider.account_id.clear(); rejects([&] { make_practice_plan(battle(),outsider); });
        require(!practice_pending() && !take_pending_practice());
        auto input = battle(); queue_practice(input,viewer);
        require(practice_pending()); input.teams[0].players[0].unit_type = "Ancient Lich";
        bool busy = false; try { queue_practice(battle(),viewer); } catch(const std::logic_error&) { busy = true; } require(busy);
        const auto queued = take_pending_practice();
        require(queued && queued->seats[0].unit_type == "Spearman");
        require(!practice_pending() && !take_pending_practice());
        rejects([&] { queue_practice(input,viewer); });
        require(!practice_pending());
        queue_practice(battle(),viewer); require(take_pending_practice().has_value());
        std::cout << "PRACTICE PLAN CHECKS PASSED: " << checks << '\n';
        return 0;
    } catch(const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
