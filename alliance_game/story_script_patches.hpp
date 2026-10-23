// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "config.hpp"
#include <string>
#include <utility>
#include <vector>
namespace alliance_game {
// These are the three reviewed direct Lua award statements in the supplied
// story campaigns (UtBS teaching/intercept). Absolute XP setters elsewhere are
// deliberately untouched. Apply to copied scenario and unit-type event code.
inline void patch_story_script_awards(config& cfg) {
 for(auto [tag,c]:cfg.all_children_view()) {
  if(tag=="lua") {
   auto code=c["code"].str();
   for(const auto& patch:std::vector<std::pair<std::string,std::string>>{
    {"student.experience = student.experience + xp_to_add","student.unified_story_credit = xp_to_add"},
    {"e.experience = e.experience +(u_level * wesnoth.game_config.kill_experience)","e.unified_story_credit = u_level * wesnoth.game_config.kill_experience"},
    {"e.experience = wesnoth.game_config.kill_experience * 0.5","e.unified_story_credit = math.ceil(wesnoth.game_config.kill_experience * 0.5)"}}) {
     std::size_t pos=0;while((pos=code.find(patch.first,pos))!=std::string::npos){
      const auto credit=patch.second+"\n";
      if(pos<credit.size() || code.substr(pos-credit.size(),credit.size())!=credit){code.insert(pos,credit);pos+=credit.size();}
      pos+=patch.first.size();
     }
   }c["code"]=code;
  }
  patch_story_script_awards(c);
 }
}
}
