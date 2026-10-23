// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "alliance_game/progression_plan.hpp"
#include "level_result.hpp"
#include <cstddef>
#include <vector>
class saved_game;
namespace alliance_game {
struct ProgressionPending {
    ProgressionRun run;
    // started means no terminal result has been captured; restart/cancel only.
    std::string status, outcome, replay_sha256, replay_filename;
    std::size_t replay_bytes = 0;
};
// Private getFilesDir() sibling of WESNOTH_ALLIANCE_PROFILE. Never uses the
// externally readable game userdata directory for the run capability.
std::vector<ProgressionPending> inspect_pending_progression(const std::string& account_id);
void acknowledge_progression_result(const std::string& account_id, const std::string& run_id,
                                    const std::string& outcome);
void acknowledge_progression_cancel(const std::string& account_id, const std::string& run_id);
// Returns the stock save basename only after exact retained hash/size checking.
std::string verified_progression_replay(const std::string& account_id, const std::string& run_id);
// Throws before launch if registration cannot be durably retained.
void register_progression_run(const ProgressionRun&);
class ProgressionActiveLaunch {
public:
    explicit ProgressionActiveLaunch(const ProgressionRun&);
    ~ProgressionActiveLaunch();
    ProgressionActiveLaunch(const ProgressionActiveLaunch&) = delete;
    ProgressionActiveLaunch& operator=(const ProgressionActiveLaunch&) = delete;
};
// Place in campaign_controller after normal result/snapshot capture and the
// replay/unit/quit returns, before convert_to_start_save clears the replay.
// Captures only the currently active new launch, never an arbitrary loaded save.
void observe_progression_completion(saved_game&, level_result::type) noexcept;
bool progression_save(const saved_game&) noexcept;
// Fixed message only, consumed by UI after launch; no exception/path/nonce data.
std::string take_progression_notice();
}
