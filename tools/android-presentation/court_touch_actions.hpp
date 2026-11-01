// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "config.hpp"
#include <algorithm>
#include <string>
#include <vector>

namespace alliance_game::mobile_actions {
// Use the native WML visibility and synchronized execution path. This only
// places already-eligible Court consumables in the visible Actions menu.
template<class Expand, class Allowed>
void append(const std::vector<config>& original, std::vector<config>& items,
            bool context_menu, Expand expand, Allowed allowed)
{
    if(context_menu) return;
    const auto has=[&](const std::string& id){return std::any_of(original.begin(),original.end(),[&](const config& row){return row["id"].str()==id;});};
    if(!has("recruit") || !has("recall")) return;
    std::vector<config> available;
    expand(available);
    for(auto& item:available) {
        const std::string id=item["id"].str(),prefix="wml_menu:court_item_";
        if(id.size()!=prefix.size()+1 || id.compare(0,prefix.size(),prefix)!=0
           || id.back()<'1' || id.back()>'8' || !allowed(id)) continue;
        if(std::none_of(items.begin(),items.end(),[&](const config& row){return row["id"].str()==id;}))
            items.insert(items.begin(),std::move(item));
    }
}
}
