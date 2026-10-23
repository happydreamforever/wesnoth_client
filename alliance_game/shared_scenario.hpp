// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "alliance_game/practice_plan.hpp"
#include "config.hpp"
namespace alliance_game {
PracticePlan make_shared_plan(const alliance::account::Battle&,const std::string& account_id);
// Identical on every device. No account token or device-local controller flag.
config make_shared_scenario(const PracticePlan&,const config& source,const config& era);
std::string shared_scenario_digest(const config&);
void localize_shared_scenario(config&,int admitted_side);
}
