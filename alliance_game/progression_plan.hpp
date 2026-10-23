// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <optional>
#include <cstdint>
#include <string>
#include <vector>

namespace alliance_game {
inline constexpr const char* progression_mission_version = "progression-campaign-v2";
inline constexpr int progression_title_result = 18102;
struct ProgressionMission {
    std::string id, name, description;
    int turns, starting_gold;
};
struct ProgressionRun {
    std::string id, account_id, username, mission_id, mission_version, ruleset_version;
    std::string unit_type, nonce;
    int leader_level = 0;
    std::string hero_id, form_id, database_generation;
    std::int64_t hero_level = 1;
};
const std::vector<ProgressionMission>& progression_missions();
bool valid_progression_run(const ProgressionRun&) noexcept;
bool same_progression_run(const ProgressionRun&, const ProgressionRun&) noexcept;
std::string progression_faction(const ProgressionRun&);
// Run capability is held in private local metadata only, never WML or replay.
// Queue refuses a second pending launch; take consumes before game loading.
void queue_progression(const ProgressionRun&);
bool progression_pending() noexcept;
std::optional<ProgressionRun> take_pending_progression();
void run_pending_progression();
}
