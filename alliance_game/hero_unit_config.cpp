// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/hero_catalog.hpp"
#include "alliance_game/story_script_patches.hpp"
#include "config.hpp"
#include "game_config_view.hpp"
#include "serialization/parser.hpp"
#include <algorithm>
#include <set>
#include <tuple>
#include <stdexcept>
namespace alliance_game {
namespace {
#include "alliance_game/hero_unit_data.inc"
}
void apply_hero_level(config& leader,const std::string& hero,const std::string& form,std::int64_t level){
 const auto* f=find_hero_form(hero,form);if(!f||leader["type"].str()!=f->unit_type)throw std::invalid_argument("Hero form does not match leader.");
 auto& mods=leader.optional_child("modifications") ? leader.mandatory_child("modifications") : leader.add_child("modifications");
 for(const auto& obj:mods.child_range("object"))if(obj["id"].str()=="unified_hero_scaling")throw std::invalid_argument("Hero scaling was already applied.");
 config scale{"id","unified_hero_scaling","duration","forever","silent",true};
 scale.add_child("effect",config{"apply_to","hitpoints","set_total",scaled_hero_stat(f->base_hitpoints,level,2),"heal_full",true});
 auto attacks=f->attacks;std::sort(attacks.begin(),attacks.end(),[](const auto&a,const auto&b){return a.damage>b.damage;});
 std::set<std::tuple<std::string,std::string,std::string,int,int>> seen;
 for(const auto& a:attacks)if(seen.emplace(a.name,a.type,a.range,a.number,a.damage).second)
  scale.add_child("effect",config{"apply_to","attack","name",a.name,"type",a.type,"range",a.range,"number",a.number,"damage",a.damage,"set_damage",scaled_hero_stat(a.damage,level,1)});
 mods.add_child("object",scale);
 auto& vars=leader.optional_child("variables") ? leader.mandatory_child("variables") : leader.add_child("variables");vars["alliance_hero_id"]=hero;vars["alliance_form_id"]=form;vars["alliance_hero_level"]=static_cast<long long>(level);
}
game_config_view append_hero_unit_config(game_config_view base){
 static std::vector<config> copies;copies.clear();
 for(const auto& entry:base.data()){copies.emplace_back(entry);patch_story_script_awards(copies.back());}
 game_config_view patched;for(const auto& copy:copies)patched.data().push_back(copy);
 static const auto extra=io::read(std::string(hero_units_wml));patched.data().push_back(extra);return patched;
}
}
