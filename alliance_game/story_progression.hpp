// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "level_result.hpp"
class saved_game;class unit;
namespace alliance_game {
bool begin_story_campaign(saved_game&);
bool prepare_story_scenario(saved_game&,bool replay);
// Allocate the next checkpoint before native scenario-start saves are written.
bool advance_story_scenario(saved_game&) noexcept;
void observe_story_completion(saved_game&,level_result::type) noexcept;
void record_story_experience(unit&,int earned) noexcept;
void record_story_form(unit&) noexcept;
void capture_story_participants() noexcept;
void retry_story_results() noexcept;
}
