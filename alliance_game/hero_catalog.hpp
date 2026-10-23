// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <cstdint>
#include <string>
#include <vector>
class config;
class game_config_view;
namespace alliance_game {
struct HeroAttack { std::string name,type,range; int damage=0,number=0; };
struct HeroForm {
 std::string id,hero_id,unit_type,faction,name;
 int native_level=0,base_hitpoints=0;
 std::vector<HeroAttack> attacks;
};
struct StoryAlias { std::string unit_id,hero_id,source_type,source_variation,form_id; };
struct StoryCampaign { std::string id; std::vector<std::string> scenario_ids; std::vector<StoryAlias> aliases; };
const HeroForm* find_hero_form(const std::string&,const std::string&) noexcept;
const HeroForm* find_hero_unit(const std::string&) noexcept;
const std::vector<HeroForm>& hero_forms();
std::string hero_name(const std::string& hero_id);
const StoryCampaign* find_story_campaign(const std::string&) noexcept;
const StoryAlias* find_story_alias(const std::string& campaign,const std::string& unit,const std::string& type,const std::string& variation="") noexcept;
void apply_hero_level(config& leader,const std::string& hero,const std::string& form,std::int64_t level);
game_config_view append_hero_unit_config(game_config_view base);
const char* hero_catalog_version();
const char* hero_catalog_sha256();
int scaled_hero_stat(int base,std::int64_t level,int percent_per_level);
}
