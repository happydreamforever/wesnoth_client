// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/team_views.hpp"
#include "alliance_game/hero_catalog.hpp"
#include <utility>

namespace alliance_game {
namespace {
std::string size_label(int size) { return std::to_string(size)+"v"+std::to_string(size); }
std::string state_label(const std::string& state) {
    if(state=="draft")return "Planning";
    if(state=="running")return "In battle";
    if(state=="completed")return "Completed";
    if(state=="cancelled")return "Cancelled";
    return state;
}
}
BattleRow team_member_row(const alliance::account::Member& member,const std::string& captain,const std::string& actor)
{
    const bool leads=member.id==captain;
    return {member.username+(member.id==actor?"  •  You":""),
        std::string(leads?"Captain":"Member")+"  •  "+std::to_string(member.hero_count)+" heroes",
        member.username+"\n"+(leads?"Team captain":"Team member")+
        "\nHeroes unlocked: "+std::to_string(member.hero_count)+
        "\n\nEach player chooses their own hero before Ready. Team experience and hero experience are separate.\n\nPlayer ID: "+member.id,
        leads?"icons/circlet_winged.png":"icons/helmet_bascinet.png",{}};
}
BattleRow team_summary_row(const alliance::account::TeamSummary& team)
{
    return {team.name,"Level "+std::to_string(team.level)+"  •  Rating "+std::to_string(team.rating)+
        "  •  "+std::to_string(team.member_count)+"/8 members",
        "Team level: "+std::to_string(team.level)+"\nTeam experience: "+std::to_string(team.experience)+
        "\nRating: "+std::to_string(team.rating)+"\nMembers: "+std::to_string(team.member_count)+
        "/8\n\nOpen the roster before applying. Your captain must approve an application before you join.\n\nTeam ID: "+team.id,
        "icons/circlet_winged.png",{}};
}
BattleRow team_map_row(const alliance::account::BattleMap& map)
{
    return {map.name,size_label(map.size)+"  •  "+std::to_string(map.size*2)+" fixed seats",
        "Map: "+map.name+"\nFormat: "+size_label(map.size)+
        "\n\nChoose current members for both lineups. Each player selects a hero; both captains approve the saved plan before Ready.",
        {},battle_map_preview(map.scenario_id)};
}
BattleView team_roster_view(const alliance::account::Team& team,const std::string& actor)
{
    BattleView view;view.title=team.name;
    view.subtitle=team.area_name+"  •  Team level "+std::to_string(team.level)+"  •  Rating "+std::to_string(team.rating)+
        "  •  "+std::to_string(team.members.size())+"/"+std::to_string(team.capacity)+" members";
    view.rows.push_back({"Team overview",team.area_name+"  •  "+std::to_string(team.experience)+" team XP",
        "Team level: "+std::to_string(team.level)+"\nTeam experience: "+std::to_string(team.experience)+
        "\nRating: "+std::to_string(team.rating)+"\n\nPlan a shared battle with another team in your area. "
        "Captains choose the map and lineups. Players choose their hero and set Ready.\n\nTeam ID: "+team.id+
        "\nArea ID: "+team.area_id,"icons/circlet_winged.png",{}});
    for(const auto& member:team.members)view.rows.push_back(team_member_row(member,team.captain_id,actor));
    return view;
}
BattleView team_plan_view(const alliance::account::Battle& battle,const std::string& actor,
                         const std::string& validity,const std::string& availability)
{
    BattleView view;view.title="Battle plan";
    view.subtitle=state_label(battle.state)+"  •  "+battle.map.name+"  •  "+size_label(battle.map.size);
    const bool history=battle.state=="cancelled"||battle.state=="completed";
    const bool inactive=history||(battle.state=="draft"&&battle.validity!="valid");
    int count=0,ready=0,approved=0;
    for(const auto& team:battle.teams){approved+=team.approved;for(const auto& player:team.players){++count;ready+=player.ready;}}
    auto overview=team_map_row(battle.map);overview.title="Map and battle status";
    overview.subtitle=state_label(battle.state)+"  •  "+std::to_string(ready)+"/"+std::to_string(count)+
        (inactive?" saved Ready":" Ready")+"  •  "+std::to_string(approved)+"/2 captains";
    overview.detail=state_label(battle.state)+"\n"+validity+"\n\n"+
        (history?"This plan is inactive. Readiness is saved history and captain approval is inactive.\n\n":
         inactive?"This draft is inactive until its changed teams, heroes or map are resolved.\n\n":"")+
        availability+"\n\nA shared battle needs both captain approvals, every selected player Ready and every fixed seat connected."
        "\n\nBattle plan ID: "+battle.id+"\nCreated (UTC): "+battle.created_at;
    view.rows.push_back(std::move(overview));
    for(const auto& team:battle.teams){
        std::string roster=team.name+"\nCaptain approval"+(inactive?std::string(" (inactive)"):std::string{})+
            ": "+(team.approved?"Approved":"Waiting")+"\n";
        for(const auto& player:team.players)roster+="\nSide "+std::to_string(player.side)+": "+player.username+
            (player.id==team.captain_id?" (captain)":"")+" — "+(inactive?std::string(history?"Saved readiness: ":"Inactive readiness: "):std::string{})+(player.ready?"Ready":"Not ready");
        roster+="\n\nTeam ID: "+team.id;
        view.rows.push_back({team.name,"Captain: "+std::string(team.approved?"Approved":"Waiting")+
            (inactive?" (inactive)":""),roster,"icons/circlet_winged.png",{}});
        for(const auto& player:team.players){
            const auto* form=player.selection_present?find_hero_form(player.hero_id,player.form_id):nullptr;
            const std::string hero=form?hero_name(player.hero_id)+" / "+form->name:"Hero not selected";
            const std::string status=(inactive?std::string(history?"Saved readiness: ":"Inactive readiness: "):std::string{})+
                (player.ready?"Ready":"Not ready");
            view.rows.push_back({"Side "+std::to_string(player.side)+"  •  "+player.username+(player.id==actor?"  •  You":""),
                team.name+"  •  "+status,
                team.name+"\n"+player.username+(player.id==team.captain_id?"  •  Captain":"")+"\n\n"+hero+
                (form?"\nHero level: "+std::to_string(player.hero_level):"\nChoose an owned hero before Ready.")+
                "\n"+status+"\n\n"+(player.id==actor?"Use the actions below for your selected side.":"Only this player can choose their hero and change their readiness.")+
                "\n\nPlayer ID: "+player.id,
                form?battle_unit_image(form->unit_type):"units/unknown-unit.png",{}});
        }
    }
    return view;
}
}
