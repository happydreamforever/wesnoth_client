// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/shared_controls.hpp"
#include "alliance_native/progression_protocol.hpp"
#include "config.hpp"
#include <sstream>
#include <stdexcept>

namespace alliance_game {
void add_shared_controls(config& scenario,const alliance::account::Battle& battle) {
    if(battle.teams.size()!=2||!alliance::progression_protocol::uuid(battle.id))throw std::invalid_argument("Invalid captain controls.");
    std::ostringstream code;
    code<<"local captain_side={}\nlocal side_team={}\nlocal teams={}\n";
    for(std::size_t index=0;index<battle.teams.size();++index) {
        const auto& team=battle.teams[index];
        if(!alliance::progression_protocol::uuid(team.id)||!alliance::progression_protocol::uuid(team.captain_id))throw std::invalid_argument("Invalid captain identity.");
        code<<"teams["<<index+1<<"]='"<<team.id<<"'\n";
        for(const auto& slot:team.slots)if(slot.kind!="closed")code<<"side_team["<<slot.side<<"]="<<index+1<<"\n";
        for(const auto& player:team.players) {
            if(player.side<1||player.side>8)throw std::invalid_argument("Invalid captain side.");
            code<<"side_team["<<player.side<<"]="<<index+1<<"\n";
            if(player.id==team.captain_id)code<<"captain_side["<<player.side<<"]=true\n";
        }
    }
    code<<R"LUA(
local function finish(kind,winner)
    wml.variables.alliance_terminal_kind=kind
    wml.variables.alliance_terminal_run_id=wml.variables.alliance_shared_run_id
    wml.variables.alliance_terminal_winner_team_id=winner and teams[winner] or ''
    wml.variables.alliance_terminal_draw=(kind=='draw')
    local cfg={result='victory',bonus=false,carryover_percentage=0,carryover_report=false,
               save=false,replay_save=false,linger_mode=false,reveal_map=false}
    for side,team in pairs(side_team) do
        cfg[#cfg+1]=wml.tag.result{side=side,result=(not winner or team==winner) and 'victory' or 'defeat'}
    end
    wesnoth.wml_actions.endlevel(cfg)
end
function alliance_time_limit_draw() finish('draw',nil) end
function wesnoth.custom_synced_commands.alliance_captain_v1(cfg)
    local side=wesnoth.current.side
    if not captain_side[side] or wml.variables.alliance_terminal_kind then
        error('Unavailable Alliance captain command')
    end
    local team=side_team[side]
    local action=cfg.action
    if action~='surrender' and action~='offer_draw' and action~='withdraw_draw' then
        error('Unknown Alliance captain command')
    end
    -- The owned native command overlay commits confirmed Alliance commands
    -- before calling Lua, so variable-only draw offers reach every player.
    if action=='surrender' then finish('surrender',3-team)
    elseif action=='offer_draw' or action=='withdraw_draw' then
        wml.variables['alliance_draw_offer_'..team]=(action=='offer_draw')
        if wml.variables.alliance_draw_offer_1 and wml.variables.alliance_draw_offer_2 then finish('draw',nil) end
    else error('Unknown Alliance captain command') end
end
function alliance_captain_prompt(action)
    local side=wesnoth.current.side
    if not captain_side[side] or not wesnoth.sides[side].is_local or
       not wesnoth.current.user_can_invoke_commands then return end
    local labels={surrender='Confirm: surrender this team (opponents win)',
                  offer_draw='Confirm: offer a draw (both captains must agree)',
                  withdraw_draw='Confirm: withdraw my team draw offer'}
    if not labels[action] then return end
    -- This menu command is UNSYNCED. Back/Escape/default selection emits no
    -- command and cannot surrender or offer a draw on the server.
    local choice=gui.show_menu({{label='Back - keep playing'},{label=labels[action]}},1,false)
    if choice~=2 then return end
    if wesnoth.current.side~=side or not captain_side[wesnoth.current.side] or not wesnoth.sides[wesnoth.current.side].is_local or
       not wesnoth.current.user_can_invoke_commands then return end
    wesnoth.sync.invoke_command('alliance_captain_v1',{action=action})
end
)LUA";
    scenario.add_child("lua")["code"]=code.str();
    if(battle.format==2&&battle.settings.turns>0) {
        auto& timeout=scenario.add_child("event");timeout["name"]="time over";
        timeout.add_child("lua")["code"]="alliance_time_limit_draw()";
    }
    auto& event=scenario.add_child("event");event["name"]="prestart";
    for(const auto& team:battle.teams)for(const auto& player:team.players)if(player.id==team.captain_id) {
        for(const auto& action:{"surrender","offer_draw","withdraw_draw"}) {
            const std::string value=action;
            auto& item=event.add_child("set_menu_item");
            item["id"]="alliance_captain_"+value+"_"+std::to_string(player.side);
            item["description"]=value=="surrender"?"Alliance: surrender team…":value=="offer_draw"?"Alliance: offer draw…":"Alliance: withdraw draw offer…";
            item["synced"]=false;item["use_hotkey"]=false;
            auto& condition=item.add_child("show_if").add_child("variable");condition["name"]="side_number";condition["equals"]=player.side;
            item.add_child("command").add_child("lua")["code"]="alliance_captain_prompt('"+value+"')";
        }
    }
}
}
