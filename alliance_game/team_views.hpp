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
struct TeamPlanDraft {
    std::string map_id;
    alliance::account::BattleSettings settings;
    std::vector<alliance::account::BattleSlot> slots;
};
bool edit_team_plan(const std::vector<alliance::account::BattleMap>& maps,
                    const std::vector<alliance::account::Team>& teams,
                    const std::string& actor, TeamPlanDraft& draft);
enum class TeamPlanAction { back, refresh, join, leave, hero, ready, approve, edit, cancel, replay, reconnect, watch, result, upload };
struct TeamPlanActionResult { TeamPlanAction action=TeamPlanAction::back; int side=0; };
TeamPlanActionResult show_team_plan_workspace(const alliance::account::Battle& battle,
                                             const std::string& actor,const std::string& actor_team,
                                             const std::vector<alliance::account::Team>& rosters = {},
                                             bool terminal_result=false,bool pending_result=false);
}
