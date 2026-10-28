// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "alliance_game/world_scenario.hpp"
#include "game_end_exceptions.hpp"
#include <string>
class saved_game;class unit;
namespace alliance_game {
class WorldActiveLaunch {public:WorldActiveLaunch(const WorldPlan&,const std::string&scenario_hash,bool witness);~WorldActiveLaunch();WorldActiveLaunch(const WorldActiveLaunch&)=delete;};
void capture_world_units() noexcept;
void record_world_experience(unit&,int) noexcept;
void record_world_form(unit&) noexcept;
void world_sync_error() noexcept;
void observe_world_completion(saved_game&,level_result::type) noexcept;
bool world_save(const saved_game&) noexcept;
std::string pending_world_evidence();
std::string pending_world_replay();
}
