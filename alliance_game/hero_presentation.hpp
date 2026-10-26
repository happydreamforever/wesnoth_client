// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <string>
#include <vector>
namespace alliance_game {
struct HeroCampaignSource { std::string id,title; };
struct HeroPresentation {
 std::string id,name,biography,portrait;
 std::vector<HeroCampaignSource> campaigns;
 // Abilities across earned forms; actual combat stats remain native-unit data.
 std::vector<std::string> abilities;
};
inline const std::vector<HeroPresentation>& hero_presentations() {
#include "alliance_game/hero_presentation_data.inc"
}
inline const HeroPresentation* hero_presentation(const std::string& id) {
 for(const auto& hero:hero_presentations())if(hero.id==id)return &hero;
 return nullptr;
}
}
