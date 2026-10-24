// SPDX-License-Identifier: GPL-2.0-or-later
// Actual GUI render cases: production view models, no network/database mutation.
#pragma once
#include "alliance_game/team_views.hpp"
#include "alliance_game/hero_catalog.hpp"
#include <utility>
#include <algorithm>
#include <stdexcept>
std::vector<std::pair<std::string,alliance_game::BattleView>> team_render_cases()
{
    using namespace alliance_game;
    namespace account=alliance::account;
    std::vector<std::pair<std::string,BattleView>> cases;
    const std::string actor="00000000-0000-0000-0000-000000000001";
    account::Team own;own.id="4cd736d3-dc6c-4c3d-a816-e7682b56db30";
    own.name="Northern Guardians of the Ancient Valley";own.area_id="ff010e4d-af74-4906-8ba6-5f096aaf90ec";
    own.area_name="Yama Valley";own.captain_id=actor;own.capacity=8;own.level=12;own.experience=12345;own.rating=1240;
    for(int i=1;i<=8;++i) {
        const auto id=i==1?actor:"00000000-0000-0000-0000-00000000000"+std::to_string(i);
        own.members.push_back({id,"GuardianPlayerNameLong_"+std::to_string(i),i*3});
    }
    auto hub=team_roster_view(own,actor);hub.title="Teams — "+own.name;
    hub.actions={{"Battle plans"},{"New battle"},{"Captain orders"},{"Applications"},{"Find teams"},{"Area rankings"},{"Manage team"},{"Refresh"}};
    cases.emplace_back("11-team-hub-eight-members",hub);
    hub.selected=8;cases.emplace_back("12-team-hub-last-member",hub);
    std::vector<account::BattleMap> maps={
        {"isars-cross","Isar's Cross","multiplayer_Isars_Cross",2,{{1,4},{2,3}}},
        {"hexcake","Hexcake","multiplayer_Hexcake",3,{{1,4,5},{2,3,6}}},
        {"mokena-prairie","Mokena Prairie","multiplayer_8p_Mokena_Prairie",4,{{1,3,5,8},{2,4,6,7}}}};
    BattleView map_view;map_view.title="Choose battle map";map_view.subtitle="Yama Valley  •  Choose a fixed 2v2, 3v3 or 4v4 lineup";
    map_view.actions={{"Choose map"}};map_view.selected_action=true;
    for(const auto& map:maps){auto row=team_map_row(map);if(row.map_data.empty())throw std::runtime_error("Production team minimap absent: "+map.scenario_id);map_view.rows.push_back(row);}
    for(int i=0;i<3;++i){map_view.selected=i;cases.emplace_back(std::to_string(13+i)+"-team-map-"+std::to_string(i+2)+"v"+std::to_string(i+2),map_view);}
    const auto& forms=hero_forms();
    const auto preferred=std::find_if(forms.begin(),forms.end(),[](const auto& form){return form.name=="Spearman"||form.name=="Elvish Fighter";});
    const auto& form=preferred==forms.end()?forms.front():*preferred;
    account::Battle last;
    for(int size=2;size<=4;++size){
        account::Battle battle;battle.id="100599c9-610f-4d81-b8bb-1cb1b79e5f27";battle.area_id=own.area_id;battle.state="draft";battle.validity="valid";battle.created_at="2026-10-24T10:00:00Z";battle.map=maps[size-2];
        battle.teams={{own.id,own.name,actor,true,{}},{"38742807-4a4c-4337-93c1-ec0e43413208","Southern Rangers of the Forgotten Coast","00000000-0000-0000-0000-000000000005",true,{}}};
        for(int t=0;t<2;++t)for(int j=0;j<size;++j){
            const int number=t*4+j+1;account::BattlePlayer player;player.id="00000000-0000-0000-0000-00000000000"+std::to_string(number);player.username="GuardianPlayerNameLong_"+std::to_string(number);
            player.side=battle.map.sides[t][j];player.ready=number!=8;player.selection_present=true;player.hero_id=form.hero_id;player.form_id=form.id;player.hero_level=12;player.hero_revision=8;player.unit_type=form.unit_type;player.level=form.native_level;battle.teams[t].players.push_back(player);
        }
        auto view=team_plan_view(battle,actor,"The saved lineups match the current teams and leaders.","Your personal practice is available. Other players do not need to be Ready.");
        view.actions={{"Refresh plan"},{"Practice this plan"},{"Change my hero"},{"Set myself not ready"},{"Cancel this battle plan"}};
        cases.emplace_back(std::to_string(14+size)+"-team-plan-"+std::to_string(size)+"v"+std::to_string(size),view);last=battle;
    }
    auto hero=team_plan_view(last,actor,"The saved lineups match the current teams and leaders.","Your personal practice is available.");hero.selected=2;
    hero.actions={{"Refresh plan"},{"Practice this plan"},{"Change my hero"},{"Set myself not ready"},{"Cancel this battle plan"}};
    cases.emplace_back("19-team-plan-owned-hero",hero);
    last.state="cancelled";auto history=team_plan_view(last,actor,"The saved lineups match the current teams and leaders.","Cancelled plans are history only. Local practice is unavailable.");history.actions={{"Refresh plan"}};
    cases.emplace_back("20-team-cancelled-plan",history);
    last.state="draft";last.validity="roster_changed";auto changed=team_plan_view(last,actor,"Team membership changed after this plan was saved.","This draft is not eligible for a shared battle. Captain approval and saved readiness are inactive.");changed.actions={{"Refresh plan"},{"Change my hero"},{"Set myself not ready"},{"Cancel this battle plan"}};
    cases.emplace_back("21-team-membership-changed",changed);
    auto lineup=team_roster_view(own,actor);lineup.rows.erase(lineup.rows.begin());lineup.title="Choose lineup — "+own.name;
    lineup.subtitle="Selected 4/4 players. Selection order assigns the map sides. Use the lineup when it is full.";
    for(int i=0;i<4;++i){lineup.rows[i].title="Side "+std::to_string(maps.back().sides[0][i])+"  •  "+lineup.rows[i].title;lineup.rows[i].subtitle="Selected  •  "+lineup.rows[i].subtitle;}
    lineup.actions={{"Select / remove",true,true},{"Use this lineup"}};cases.emplace_back("22-team-lineup-selection",lineup);
    return cases;
}
