// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "alliance_game/practice_plan.hpp"
#include "config.hpp"

namespace alliance_game {
// Uses the already loaded, supplied 1.19.26 map and Default-era configuration.
// Produces an in-memory scenario; no addon, OBB, account or plan is written.
config make_practice_scenario(const PracticePlan&, const config& source_scenario, const config& era);
}
