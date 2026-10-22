// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "alliance_native/account_client.hpp"
#include <optional>
#include <string>
#include <vector>

namespace alliance_game {
struct PracticeSeat {
    std::string id, username, team_id, team_name, faction, unit_type;
    int level = 0, side = 0;
    bool human = false;
};
struct PracticePlan {
    std::string battle_id, map_id, map_name, scenario_id, map_file;
    int size = 0, controlled_side = 0;
    std::vector<PracticeSeat> seats;
};
// Local copies only. The saved plan is historical; no current membership or
// readiness claim is made and no account credential enters this descriptor.
PracticePlan make_practice_plan(const alliance::account::Battle&, const alliance::account::Profile&);
// Every permitted target type, for a persistent remove_advancement effect.
// Using the same allowlist avoids a second, drifting progression tree.
std::string practice_advancement_targets();
// Consumes before any loading or gameplay; errors/return-to-title cannot retry.
std::optional<PracticePlan> take_pending_practice();
}
