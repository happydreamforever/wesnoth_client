// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "alliance_game/battle_ui.hpp"
#include "alliance_native/account_client.hpp"

namespace alliance_game {
BattleView team_roster_view(const alliance::account::Team& team, const std::string& actor);
BattleView team_plan_view(const alliance::account::Battle& battle, const std::string& actor,
                          const std::string& validity, const std::string& availability);
BattleRow team_summary_row(const alliance::account::TeamSummary& team);
BattleRow team_map_row(const alliance::account::BattleMap& map);
BattleRow team_member_row(const alliance::account::Member& member, const std::string& captain,
                          const std::string& actor);
}
