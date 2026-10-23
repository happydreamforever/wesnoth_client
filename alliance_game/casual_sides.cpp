// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/casual_sides.hpp"
#include "alliance_game/casual_session.hpp"
#include "alliance_game/account_ui.hpp"
#include "alliance_game/hero_catalog.hpp"
#include "config.hpp"
#include "game_initialization/multiplayer.hpp"
#include "gui/dialogs/message.hpp"
#include "gui/dialogs/simple_item_selector.hpp"
#include "wesnothd_connection.hpp"
#include <chrono>
#include <cstdint>
#include <map>
#include <stdexcept>
#include <thread>
#include <vector>

namespace alliance_game {
namespace {
struct Selection {
    std::string username, account_id, hero_id, form_id;
    std::int64_t level = 0, revision = 0;
    bool ready = false;
};
std::string game_id;
bool resumed = false;
std::map<int, Selection> selections;
bool uuid(const std::string& value) {
    if(value.size()!=36) return false;
    for(std::size_t i=0;i<value.size();++i) {
        if(i==8||i==13||i==18||i==23) { if(value[i]!='-') return false; }
        else if(!((value[i]>='0'&&value[i]<='9')||(value[i]>='a'&&value[i]<='f'))) return false;
    }
    return true;
}
void message(const std::string& text) {
    gui2::show_message("Multiplayer",text,gui2::dialogs::message::ok_button,false,false);
}
struct ClearSecret { std::string& value; ~ClearSecret(){ clear_casual_secret(value); } };
struct ClearTicketField { config& value; ~ClearTicketField(){ value["ticket"]=""; } };
}

void reset_casual_game() noexcept { game_id.clear(); selections.clear(); resumed=false; }
bool casual_game_active() noexcept { return !game_id.empty(); }
bool casual_game_resumed() noexcept { return resumed; }

void observe_casual_message(const config& data) {
    if(auto context=data.optional_child("alliance_game")) {
        const auto id=context["id"].str();
        if(!uuid(id)) throw std::runtime_error("Invalid multiplayer game identity.");
        if(id!=game_id) { reset_casual_game(); game_id=id; }
        resumed=context["resume"].to_bool();
    }
    for(const auto& row:data.child_range("alliance_selection")) {
        if(game_id.empty() || row["game_id"].str()!=game_id) continue;
        const int side=row["side"].to_int();
        if(side<1||side>100) throw std::runtime_error("Invalid multiplayer side.");
        if(row["cleared"].to_bool()) { selections.erase(side); continue; }
        Selection choice{row["username"].str(),row["account_id"].str(),row["hero_id"].str(),row["form_id"].str(),
            row["hero_level"].to_long_long(),row["hero_revision"].to_long_long(),row["ready"].to_bool()};
        const auto* form=find_hero_form(choice.hero_id,choice.form_id);
        if(!uuid(choice.account_id)||choice.username.empty()||choice.username.size()>24||choice.level<1||choice.revision<0
                ||!form||form->unit_type!=row["unit_type"].str()||form->native_level!=row["unit_level"].to_int())
            throw std::runtime_error("Invalid multiplayer hero selection.");
        selections[side]=std::move(choice);
    }
}

bool choose_casual_side(int side) {
    if(!casual_game_active()) { message("The server is preparing this game. Wait a moment, then choose your hero."); return false; }
    auto session=player_session();
    if(!session) return false;
    const auto original_game=game_id;
    const auto original_account=session->account.account_id;
    const auto original_generation=session->account.database_generation;
    const auto found=selections.find(side);
    const bool selected=found!=selections.end()&&found->second.account_id==original_account;
    std::vector<std::string> rows{"Back", resumed ? (selected && found->second.ready ? "Keep saved hero and set myself not ready" : "Keep saved hero and set myself ready") : "Choose hero and form"};
    if(selected&&!resumed) rows.push_back(found->second.ready ? "Set myself not ready" : "Set myself ready");
    gui2::dialogs::simple_item_selector dialog("My multiplayer side",
        "Choose one of your heroes, then set yourself Ready. Casual multiplayer gives no permanent experience or rating.",rows,false,false);
    dialog.set_allow_plugin_skip(false); dialog.set_selected_index(0);
    dialog.set_ok_label("Choose"); dialog.set_cancel_label("Back");
    if(!dialog.show()||dialog.selected_index()==0) return false;
    const int action=resumed && dialog.selected_index()==1 ? 2 : dialog.selected_index();
    if(action==1) {
        std::string hero,form;
        if(!choose_owned_hero(hero,form)) return false;
        if(game_id!=original_game) return false;
        auto ticket=casual_selection_ticket(game_id,side,hero,form);
        if(!ticket) return false;
        ClearSecret ticket_guard{*ticket};
        auto current=player_session();
        if(!current||current->account.account_id!=original_account||current->account.database_generation!=original_generation||game_id!=original_game) return false;
        selections.erase(side);
        config command;
        auto& selection=command.add_child("alliance_hero"); selection["ticket"]=*ticket;
        ClearTicketField command_guard{selection};
        mp::send_to_server(command);
        clear_casual_secret(*ticket); selection["ticket"]="";
        message("Hero choice sent. After the server confirms it, open this side again and choose Set myself ready.");
        return true;
    }
    auto current=player_session();
    auto choice=selections.find(side);
    if(action!=2||!current||current->account.account_id!=original_account||current->account.database_generation!=original_generation||game_id!=original_game
            ||choice==selections.end()||choice->second.account_id!=original_account) return false;
    config command;
    auto& ready=command.add_child("alliance_ready"); ready["side"]=side;
    ready["ready"]=!choice->second.ready;
    choice->second.ready=false; // A pending acknowledgement cannot authorize Start.
    mp::send_to_server(command);
    return true;
}

bool casual_side_ready(int side,const std::string& username) {
    auto found=selections.find(side);
    return !game_id.empty()&&found!=selections.end()&&found->second.username==username&&found->second.ready;
}

std::string casual_side_label(int side,const std::string& username) {
    const auto found=selections.find(side);
    if(found==selections.end()||found->second.username!=username) return "Choose hero, then Ready";
    const auto& choice=found->second;
    const auto* form=find_hero_form(choice.hero_id,choice.form_id);
    return (form?form->name:"Hero")+" — level "+std::to_string(choice.level)+(choice.ready?" — Ready":" — Not ready");
}

void apply_casual_side(config& side) {
    if(game_id.empty()||resumed||side["controller"].str()!="human") return;
    const auto found=selections.find(side["side"].to_int());
    if(found==selections.end()||found->second.username!=side["player_id"].str()) return;
    const auto& choice=found->second;
    side["alliance_account_id"]=choice.account_id;
    side["alliance_hero_id"]=choice.hero_id;
    side["alliance_form_id"]=choice.form_id;
    side["alliance_hero_level"]=choice.level;
    side["alliance_hero_revision"]=choice.revision;
    side["alliance_casual_game_id"]=game_id;
    config identity;
    auto previous=side.optional_child("leader");
    for(const char* field : {"id","name","gender"}) {
        const auto& value=previous && !previous[field].str().empty() ? previous[field] : side[field];
        const auto text=value.str();
        if(text.empty()) continue;
        if(text.size()>256) throw std::runtime_error("The authored leader identity is too long.");
        if(std::string(field)=="gender" && text!="male" && text!="female") continue;
        identity[field]=value;
    }
    side.remove_children("leader");
    auto& leader=side.add_child("leader");
    for(const char* field : {"id","name","gender"})
        if(!identity[field].str().empty()) leader[field]=identity[field];
    leader["type"]=find_hero_form(choice.hero_id,choice.form_id)->unit_type;
    leader["random_traits"]=false;
    leader["canrecruit"]=true;
    apply_hero_level(leader,choice.hero_id,choice.form_id,choice.level);
}

bool await_casual_start(wesnothd_connection& connection) {
    if(game_id.empty()) return false;
    const auto expected=game_id;
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(10);
    while(std::chrono::steady_clock::now()<deadline) {
        config packet;
        if(!connection.receive_data(packet)) { std::this_thread::sleep_for(std::chrono::milliseconds(5)); continue; }
        if(auto result=packet.optional_child("alliance_start")) {
            if(result["game_id"].str()==expected && result["accepted"].to_bool()) return true;
            break;
        }
        if(packet.has_child("error")||packet.has_child("leave_game")) break;
    }
    message("The server did not approve this start. Return to the lobby and review each player's hero and Ready status.");
    return false;
}
}
