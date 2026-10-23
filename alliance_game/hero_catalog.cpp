// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/hero_catalog.hpp"
#include <boost/json.hpp>
#include <algorithm>
#include <limits>
#include <set>
#include <stdexcept>
#include <tuple>
namespace alliance_game {
namespace {
#include "alliance_game/hero_catalog_data.inc"
const boost::json::object& catalog() { static const auto c=boost::json::parse(catalog_json).as_object();return c; }
std::string str(const boost::json::object& c,const char* k){return std::string(c.at(k).as_string());}
int number(const boost::json::object& c,const char* k){return static_cast<int>(c.at(k).as_int64());}
const std::vector<StoryCampaign>& campaigns(){
 static const auto value=[] { std::vector<StoryCampaign> result;
  for(const auto& row:catalog().at("campaigns").as_array()) {const auto& c=row.as_object();StoryCampaign campaign;campaign.id=str(c,"id");
   for(const auto& s:c.at("scenario_ids").as_array())campaign.scenario_ids.emplace_back(s.as_string());
   for(const auto& ar:c.at("aliases").as_array()){const auto& a=ar.as_object();for(const auto& fr:a.at("forms").as_array()){const auto& f=fr.as_object();campaign.aliases.push_back({str(a,"unit_id"),str(a,"hero_id"),str(f,"source_type"),str(f,"source_variation"),str(f,"form_id")});}}
   result.push_back(std::move(campaign));}return result;}();return value;
}
}
std::string hero_name(const std::string& id){for(const auto& h:catalog().at("heroes").as_array())if(str(h.as_object(),"id")==id)return str(h.as_object(),"name");return id;}
const char* hero_catalog_version(){return "unified-heroes-v1";}
const char* hero_catalog_sha256(){return catalog_sha;}
const std::vector<HeroForm>& hero_forms(){
 static const auto value=[] {std::vector<HeroForm> result;for(const auto& row:catalog().at("forms").as_array()) {const auto& c=row.as_object();HeroForm f;
 f.id=str(c,"id");f.hero_id=str(c,"hero_id");f.unit_type=str(c,"unit_type");f.faction=str(c,"faction");f.name=str(c,"name");f.native_level=number(c,"native_level");f.base_hitpoints=number(c,"base_hitpoints");
 for(const auto& ar:c.at("attacks").as_array()){const auto& a=ar.as_object();f.attacks.push_back({str(a,"name"),str(a,"type"),str(a,"range"),number(a,"damage"),number(a,"number")});}result.push_back(std::move(f));}return result;}();return value;
}
const HeroForm* find_hero_form(const std::string& hero,const std::string& form) noexcept {try {for(const auto& f:hero_forms())if(f.hero_id==hero&&f.id==form)return &f;}catch(...){}return nullptr;}
const HeroForm* find_hero_unit(const std::string& unit) noexcept {try {for(const auto& f:hero_forms())if(f.unit_type==unit)return &f;}catch(...){}return nullptr;}
const StoryCampaign* find_story_campaign(const std::string& id) noexcept {try {for(const auto& c:campaigns())if(c.id==id)return &c;}catch(...){}return nullptr;}
const StoryAlias* find_story_alias(const std::string& campaign,const std::string& unit,const std::string& type,const std::string& variation) noexcept {const auto* c=find_story_campaign(campaign);if(!c)return nullptr;for(const auto& a:c->aliases)if(a.unit_id==unit&&a.source_type==type&&a.source_variation==variation)return &a;return nullptr;}
int scaled_hero_stat(int base,std::int64_t level,int rate){
 if(base<0||level<1||rate<0||rate>100)throw std::invalid_argument("Invalid hero scaling input.");
 if(base==0)return 0;
 const auto limit=static_cast<std::uint64_t>(std::numeric_limits<int>::max())*100ULL+49ULL;
 const auto per=static_cast<std::uint64_t>(base)*static_cast<unsigned>(rate);
 const auto initial=static_cast<std::uint64_t>(base)*100ULL;
 if(initial>limit||(per&&static_cast<std::uint64_t>(level-1)>(limit-initial)/per))throw std::overflow_error("Hero level exceeds native combat representation.");
 return static_cast<int>((initial+per*static_cast<std::uint64_t>(level-1)+50ULL)/100ULL);
}
}
