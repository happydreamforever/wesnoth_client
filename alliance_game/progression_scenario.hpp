// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "alliance_game/progression_plan.hpp"
#include "config.hpp"
namespace alliance_game {
config make_progression_scenario(const ProgressionRun&, const config& default_era);
bool progression_marker_matches(const config& scenario, const ProgressionRun&) noexcept;
}
