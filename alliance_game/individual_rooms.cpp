// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/individual_rooms.hpp"
#include "alliance_game/account_ui.hpp"
#include "alliance_game/session_watch.hpp"
#include "config.hpp"
#include "gui/dialogs/message.hpp"
#include "gui/dialogs/simple_item_selector.hpp"
#include <algorithm>
#include <cctype>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <vector>

namespace alliance_game {
namespace {
std::string current_game;
config current_room;
std::int64_t revision = 0, requested_revision = 0, authorized_revision = 0;
bool active = false;
bool supported = false;
bool room_changed = false;
unsigned interactions = 0;
bool identifier(const std::string& value) {
    if(value.size()!=36) return false;
    for(std::size_t i=0;i<value.size();++i) {
        if(i==8||i==13||i==18||i==23) { if(value[i]!='-')return false; }
        else if(!((value[i]>='0'&&value[i]<='9')||(value[i]>='a'&&value[i]<='f'))) return false;
    }
    return true;
}
std::string bounded(const std::string& text) { return text.substr(0,512); }
int choose(const std::string& title,const std::string& message,const std::vector<std::string>& rows,int selected=0) {
    gui2::dialogs::simple_item_selector dialog(title,message,rows,false,false);
    dialog.set_allow_plugin_skip(false); dialog.set_selected_index(selected);
    dialog.set_ok_label("Choose"); dialog.set_cancel_label("Back");
    return dialog.show()?dialog.selected_index():-1;
}
bool approve(const std::string& title,const std::string& message,const std::string& action) {
    return choose(title,message,{"Back",action})==1;
}
std::string field(const config& value,const char* name,const std::string& fallback="Not reported") {
    const auto result=value[name].str(); return result.empty()?fallback:bounded(result);
}
std::string turns(const config& room) {
    const auto value=room["room_turns"].str();
    return value=="-1"?"Unlimited":(value.empty()?"Not reported":bounded(value));
}
}
IndividualRoomInteraction::IndividualRoomInteraction() noexcept {++interactions;}
IndividualRoomInteraction::~IndividualRoomInteraction() {--interactions;}
bool individual_room_interaction_active() noexcept {return interactions!=0;}
void reset_individual_capability() noexcept {supported=false; reset_individual_room();}
bool individual_rooms_supported() noexcept {return supported;}
void reset_individual_room() noexcept {
    active=false; room_changed=false; revision=requested_revision=authorized_revision=0;
    current_game.clear(); current_room.clear();
}
bool individual_room_active() noexcept {return active;}
std::int64_t individual_room_revision() noexcept {return active?revision:0;}
void invalidate_individual_start() noexcept {requested_revision=authorized_revision=0;}
void observe_individual_room(const config& packet) {
    if(auto version=packet.optional_child("version"))supported=version["individual_rooms"].str()=="1";
    if(auto context=packet.optional_child("alliance_game")) {
        const auto id=context["id"].str();
        if(!identifier(id)) throw std::runtime_error("Invalid multiplayer room identity.");
        if(id!=current_game) { reset_individual_room(); current_game=id; }
        const bool is_individual=context["individual_room"].to_bool();
        if(is_individual) {
            const auto next=context["room_revision"].to_long_long();
            if(next<1) throw std::runtime_error("Invalid multiplayer room revision.");
            if(active && next<revision) return; // Ignore an obsolete publication.
            if(next!=revision) { if(active)room_changed=true; invalidate_individual_start(); }
            revision=next; current_room=*context; active=true;
        } else { active=false; invalidate_individual_start(); }
    }
    if(auto start=packet.optional_child("individual_start")) {
        if(active && start["game_id"].str()==current_game && start["room_revision"].to_long_long()==revision)
            requested_revision=revision;
    }
    if(packet.has_child("leave_game") || packet.has_child("error")) invalidate_individual_start();
}
bool individual_start_requested() noexcept {return active&&requested_revision==revision&&revision>0;}
bool consume_individual_start() noexcept {
    if(!individual_start_requested()||individual_room_interaction_active()||account_session_interaction_active()||!has_player_session()) return false;
    authorized_revision=requested_revision;requested_revision=0;return true;
}
bool individual_start_authorized() noexcept {return active&&authorized_revision==revision&&revision>0;}
std::string individual_listing_status(const config& room) {
    const auto status=room["room_status"].str();
    std::string label=status=="playing"?"Playing":status=="ready"?"Starting":status=="full"?"Full — waiting for Ready":"Waiting";
    return label+" — "+field(room,"room_members","0")+"/"+field(room,"room_capacity","?")+" players — "+field(room,"room_ready","0")+" ready";
}
std::string individual_listing_settings(const config& room) {
    return "Gold: "+field(room,"room_gold")+" | Turns: "+turns(room);
}
std::string individual_listing_review(const config& room) {
    std::ostringstream text;
    text<<"Room: "<<field(room,"name")<<"\nRoom ID: "<<field(room,"room_id",field(room,"id"))<<" | Revision: "<<field(room,"room_revision")<<"\nMap: "<<field(room,"mp_scenario_name",field(room,"mp_scenario"))
        <<"\n"<<individual_listing_status(room)<<"\nStarting gold: "<<field(room,"room_gold")
        <<"\nEra: "<<field(room,"mp_era")<<" | Observers: "<<(room["observer"].to_bool()?"Allowed":"Not allowed")
        <<"\nTurn limit: "<<turns(room)<<"\nFog: "<<(room["mp_fog"].to_bool()?"On":"Off")
        <<"\nShroud: "<<(room["mp_shroud"].to_bool()?"On":"Off")
        <<"\nTurn timer: "<<(room["mp_countdown"].to_bool()?"On":"Off");
    if(room["mp_countdown"].to_bool()) {
        text<<" (initial "<<field(room,"mp_countdown_init_time")<<"s; turn bonus "<<field(room,"mp_countdown_turn_bonus")
            <<"s; reserve "<<field(room,"mp_countdown_reservoir_time")<<"s; action bonus "<<field(room,"mp_countdown_action_bonus")<<"s)";
    }
    for(const auto& side:room.child_range("room_side")) {
        text<<"\nSide "<<field(side,"side")<<": "<<field(side,"player_id","Open")<<" | Team "<<field(side,"team_name")
            <<(room["room_status"].str()=="playing"&&!side["player_id"].str().empty()&&!side["connected"].to_bool()?" (disconnected)":"")
            <<" | Gold "<<field(side,"gold")<<" | Income "<<field(side,"income")
            <<" | Village gold "<<field(side,"village_gold")<<" / support "<<field(side,"village_support")
            <<" | Fog "<<(side["fog"].to_bool()?"On":"Off")<<" / shroud "<<(side["shroud"].to_bool()?"On":"Off");
    }
    text<<"\n\nEveryone can find this room. No area or team membership is required."
        <<"\nChoose your own hero and review the current settings before Ready. The full room starts automatically when every player is Ready."
        <<"\nSettings, hero or membership changes require fresh readiness. No permanent experience or rating is awarded.";
    return text.str();
}
std::string individual_room_summary() {
    if(!active)return {};
    return (room_changed?"The room rules, heroes or players changed. Review the current settings before setting Ready again.\n\n":"")+individual_listing_review(current_room);
}
bool review_individual_join(const config& room) {
    return approve("Review battle room",individual_listing_review(room),"Agree and join");
}
bool show_individual_details(const config& room,bool can_observe) {
    std::vector<std::string> rows{"Back"};
    if(can_observe)rows.push_back("Observe battle");
    return choose("Battle room details",individual_listing_review(room),rows)==1&&can_observe;
}
void show_individual_settings() {
    if(active)show_individual_details(current_room,false);
}
bool confirm_individual_ready() {
    if(!active) return true;
    const auto id=current_game;const auto before=revision;
    if(!approve("Ready for individual battle",individual_room_summary(),"Agree to these settings and set Ready")) return false;
    if(!active||current_game!=id||revision!=before) {
        gui2::show_message("Individual battle","The room changed while you reviewed it. Review the current settings and choose Ready again.",gui2::dialogs::message::ok_button,false,false);
        return false;
    }
    return true;
}
bool configure_individual_creation(config& scenario,bool eligible) {
    scenario["individual_room"]=false;
    if(!eligible) return true;
    const int mode=choose("Create battle room",
        "Individual battle: a public room without a password for 2–8 players. Every seat must be filled and Ready before automatic start.\nStandard multiplayer keeps manual start, computer sides and saved-game options.",
        {"Individual battle room", "Standard multiplayer (manual start)", "Back"});
    if(mode==1)return true;
    if(mode!=0)return false;
    if(!supported) {
        gui2::show_message("Individual battle","This running Windows multiplayer server does not support individual rooms. Update and restart the native Multiplayer service, then reopen this dashboard.",gui2::dialogs::message::ok_button,false,false);
        return false;
    }
    const auto count=scenario.child_count("side");
    if(count<2||count>8||std::any_of(scenario.child_range("side").begin(),scenario.child_range("side").end(),
            [](const config& side){return !side["allow_player"].to_bool(true);})) {
        gui2::show_message("Individual battle","Choose a standalone map with 2–8 playable sides. This scenario can still be played using Standard multiplayer.",gui2::dialogs::message::ok_button,false,false);
        return false;
    }
    const int option=choose("Starting gold","Every player starts with the same gold. You can review each side in the room before Ready.",
        {"100 gold","150 gold","200 gold","300 gold","500 gold","Back"});
    if(option<0||option>4)return false;
    static constexpr int amounts[]{100,150,200,300,500};
    config prepared=scenario;
    prepared["individual_room"]=true;
    for(auto& side:prepared.child_range("side")) {
        side["controller"]="human";side["allow_player"]=true;side["controller_lock"]=true;
        side["gold"]=amounts[option];side["gold_lock"]=true;side["income_lock"]=true;side["team_lock"]=true;
        side.remove_attribute("current_player");side.remove_attribute("player_id");
    }
    const std::string limit=prepared["turns"].to_int(-1)<0?"Unlimited":prepared["turns"].str();
    if(!approve("Publish individual battle room","Map: "+prepared["name"].str()+"\nPlayers: "+std::to_string(count)
            +"\nStarting gold: "+std::to_string(amounts[option])+"\nTurn limit: "+limit
            +"\n\nThe full room starts automatically after every player reviews the settings, chooses a hero and sets Ready. No permanent experience or rating is awarded.","Publish room")) return false;
    scenario=std::move(prepared);return true;
}
const config* individual_lobby_room(const config& lobby,int id) {
    const config* list=&lobby;
    if(auto child=lobby.optional_child("gamelist")) list=&*child;
    for(const auto& room:list->child_range("game")) if(room["id"].to_int()==id&&room["individual_room"].to_bool())return &room;
    return nullptr;
}
int individual_filter_token(const config* room,const std::string& token) {
    std::string query=token;
    std::transform(query.begin(),query.end(),query.begin(),[](unsigned char c){return static_cast<char>(std::tolower(c));});
    if(query=="type:all")return 1;
    if(query=="type:individual")return room?1:0;
    if(query=="type:standard")return room?0:1;
    if(query.rfind("status:",0)==0) {
        if(!room)return 0;
        const auto status=room->operator[]("room_status").str();
        const auto wanted=query.substr(7);
        if(wanted=="waiting")return status=="waiting"||status=="full";
        if(wanted=="playing")return status=="playing";
        if(wanted=="starting")return status=="ready";
        if(wanted=="full")return (*room)["room_members"].to_int()>=(*room)["room_capacity"].to_int();
        return 0;
    }
    if(query.rfind("gold:",0)==0)return room&&(*room)["room_gold"].str()==query.substr(5);
    if(query.rfind("turns:",0)==0)return room&&((*room)["room_turns"].str()==query.substr(6)||(query.substr(6)=="unlimited"&&(*room)["room_turns"].str()=="-1"));
    if(query=="slots:open")return room&&(*room)["room_status"].str()=="waiting"&&(*room)["room_members"].to_int()<(*room)["room_capacity"].to_int();
    return -1;
}
}
