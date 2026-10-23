// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <optional>
#include <string>
namespace alliance_game {
inline constexpr int replay_title_result=18104;
enum class AllianceReplay { campaign, shared };
void queue_alliance_replay(AllianceReplay,const std::string& account_id,const std::string& run_id);
bool replay_pending() noexcept;
// Rechecks the owned result journal and exact replay bytes immediately before Load.
std::optional<std::string> take_verified_alliance_replay();
}
