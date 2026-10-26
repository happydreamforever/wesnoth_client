// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "alliance_game/account_ui.hpp"
#include "alliance_game/hero_presentation.hpp"
#include "alliance_game/product_ui_layout.hpp"
#include "alliance_game/native_touch_layout.hpp"
#include "gui/widgets/button.hpp"
#include "gui/widgets/image.hpp"
#include "gui/widgets/menu_button.hpp"
#include "gui/widgets/multimenu_button.hpp"
#include "gui/widgets/panel.hpp"
#include "gui/widgets/text_box.hpp"
#include "gui/widgets/toggle_button.hpp"
#include <cstdlib>

namespace alliance_game {
inline std::string prepare_campaign_window(){return prepare_native_touch_window("campaign_selection");}
// Each campaign page retains the native description and art. The reward strip
// reports story eligibility; only the authoritative scenario settlement unlocks.
inline void attach_campaign_rewards(gui2::grid& page,const std::string& campaign) {
    std::vector<const HeroPresentation*> heroes;
    for(const auto& hero:hero_presentations())for(const auto& source:hero.campaigns)
        if(source.id==campaign){heroes.push_back(&hero);break;}
    if(heroes.empty())return;
    auto* image=page.find_widget<gui2::image>("image",false,false);
    if(!image)return;
    auto* parent=dynamic_cast<gui2::grid*>(image->parent());if(!parent)return;
    product_ui::prepare_theme();config definition;definition["id"]="campaign_rewards";
    product_ui::text(definition,"reward_heading","Heroes to discover","alliance_text");
    auto& strip=product_ui::cell(product_ui::row(definition),"grid");auto& portraits=product_ui::row(strip);
    for(std::size_t n=0;n<std::min<std::size_t>(heroes.size(),4);++n){const auto& hero=*heroes[n];
        auto& card=product_ui::cell(portraits,"grid","",1,3);auto path=product_ui::artwork(hero.portrait);
        product_ui::cell(product_ui::row(card),"image")["label"]=path+"~SCALE_INTO(64,68)";
        auto& label=product_ui::text(card,"",hero.name,"alliance_caption");label["characters_per_line"]=12;
    }
    auto& button=product_ui::cell(product_ui::row(definition),"button","campaign_heroes");button["label"]="View "+std::to_string(heroes.size())+" heroes";
    product_ui::text(definition,"reward_timing","Earned at scenario victory while you control the hero. Story branches affect availability.","alliance_caption");
    product_ui::cell(product_ui::row(definition),"image","campaign_art")["label"]=image->get_label();
    auto replacement=gui2::builder_grid(definition).build();auto* reward_grid=dynamic_cast<gui2::grid*>(replacement.get());
    gui2::event::connect_signal_mouse_left_click(reward_grid->find_widget<gui2::button>("campaign_heroes"),[campaign](auto&&...){show_campaign_heroes(campaign);});
    parent->swap_child("image",std::move(replacement),false);
}
}
