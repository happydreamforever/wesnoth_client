// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <string>
#include <vector>
#include <map>
#include "config.hpp"
namespace alliance_game {
struct WorldUnit {std::string instance_id,owner,kind,unit_type,hero_id,form_id;int xp=0,hero_level=1;bool delegated=false;std::map<std::string,std::string> branches;};
struct WorldSide {int side=0;std::string team_id,account_id,username,controller,faction,leader_type;WorldUnit hero;std::vector<WorldUnit> soldiers;};
struct WorldPlan {std::string run_id,generation,mode,mission_id,map_id;int gold=100,income=2,turns=30;bool fog=true,shroud=false;std::vector<WorldSide>sides;};
WorldPlan parse_world_plan(const std::string&);
config make_world_scenario(const WorldPlan&,const config& era);
void localize_world_scenario(config&,const WorldPlan&,const std::vector<int>& controlled);
std::string world_scenario_digest(const config&);
}
