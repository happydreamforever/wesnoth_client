// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "alliance_native/account_client.hpp"
#include <optional>
#include <string>
#include <vector>

namespace alliance_game {
enum class PracticeControl { own_side, own_team, hotseat };
struct PracticeSettings {
    PracticeControl control = PracticeControl::own_side;
    int starting_gold = 100;
    int turn_limit = -1;
    bool fog = true;
};
// Settings affect only this local game. They never alter the saved battle plan
// or consent, and never reserve another player's account.
bool valid_practice_settings(const PracticeSettings&) noexcept;
const char* practice_control_name(PracticeControl) noexcept;
struct PracticeSeat {
    std::string id, username, team_id, team_name, faction, unit_type;
    int level = 0, side = 0;
    bool human = false;
    std::string hero_id, form_id;
    std::int64_t hero_level = 1, hero_revision = 0;
};
struct PracticePlan {
    std::string battle_id, map_id, map_name, scenario_id, map_file;
    int size = 0, controlled_side = 0;
    PracticeSettings settings;
    std::vector<PracticeSeat> seats;
};
enum class PracticeEligibility {
    eligible, inactive_plan, invalid_plan, unavailable_plan,
    captain_approval_required, viewer_readiness_required, not_selected
};
// The UI and queue share full saved-lineup validation and the same policy:
// a valid draft, both current captains' approval, and the selected viewer ready.
PracticeEligibility practice_eligibility(const alliance::account::Battle&, const alliance::account::Profile&);
const char* practice_eligibility_reason(PracticeEligibility) noexcept;
// Confirmation binds the viewer and every reviewed field except other players'
// Ready flags, which do not control this player's local AI practice.
bool same_practice_snapshot(const alliance::account::Battle&, const alliance::account::Battle&,
                            const std::string& viewer_account_id) noexcept;
// Local copies only. Eligibility is checked at queue time, not reserved on the
// server; no account credential enters this descriptor or local game.
PracticePlan make_practice_plan(const alliance::account::Battle&, const alliance::account::Profile&,
                                const PracticeSettings& = {});
// Every permitted target type, for a persistent remove_advancement effect.
// Using the same allowlist avoids a second, drifting progression tree.
std::string practice_advancement_targets();
// Consumes before any loading or gameplay; errors/return-to-title cannot retry.
std::optional<PracticePlan> take_pending_practice();
}
