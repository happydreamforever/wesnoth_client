// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "alliance_native/transport.hpp"
#include <string>
namespace alliance_game {
inline constexpr int world_title_result=18105;
struct WorldAdmission {alliance::TrustedProfile profile;std::string run_id,account_id,token;bool replay=false,worker=false,computer_controlled=false;};
void queue_world(WorldAdmission);
bool world_pending() noexcept;
void run_pending_world();
bool world_last_launch_complete() noexcept;
}
