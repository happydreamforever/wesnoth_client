// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "alliance_game/practice_plan.hpp"
#include "game_end_exceptions.hpp"
#include <string>
#include <vector>
class saved_game;
namespace alliance_game {
struct SharedPending {
    std::string run_id,account_id,winner_team_id,replay_sha256,replay_filename,status;
    bool draw=false;
    std::size_t replay_bytes=0;
};
class SharedActiveLaunch {
public:
    SharedActiveLaunch(const PracticePlan&,const std::string& account_id);
    ~SharedActiveLaunch();
    SharedActiveLaunch(const SharedActiveLaunch&)=delete;
};
void observe_shared_completion(saved_game&,level_result::type) noexcept;
bool shared_save(const saved_game&) noexcept;
std::vector<SharedPending> inspect_pending_shared(const std::string& account_id);
std::string read_shared_replay(const SharedPending&);
void acknowledge_shared_result(const SharedPending&);
std::string verified_shared_replay(const std::string& account_id,const std::string& run_id);
void store_downloaded_shared_replay(const std::string& owner,const std::string& run_id,const std::string& source_account,
                                    const std::string& sha256,const std::string& bytes);
std::string take_shared_notice();
}
