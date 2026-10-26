// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/individual_rooms.hpp"
#include "alliance_game/account_ui.hpp"
#include "alliance_game/battle_ui.hpp"
#include "alliance_game/hero_catalog.hpp"
#include "alliance_game/session_watch.hpp"
#include "config.hpp"
#include "alliance_game/product_ui_layout.hpp"
#include "game_initialization/create_engine.hpp"
#include "gui/dialogs/multiplayer/mp_create_game.hpp"
#include "saved_game.hpp"
#include "map/map.hpp"
#include "game_config.hpp"
#include "gui/widgets/button.hpp"
#include "gui/widgets/label.hpp"
#include "gui/widgets/listbox.hpp"
#include "gui/widgets/minimap.hpp"
#include "gui/widgets/text_box.hpp"
#include "gui/widgets/toggle_button.hpp"
#include <array>
#include "gui/dialogs/message.hpp"
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
int filter_gold = 0, filter_turns = 0, filter_capacity = 0;
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
    BattleView view;view.title=title;view.subtitle=message;view.selected=selected;
    for(const auto& row:rows)view.rows.push_back({row,{},message,{},{}});
    view.actions={{"Choose",true}};
    const auto result=show_battle_view(view);
    return result.action==0?result.row:-1;
}
std::string field(const config& value,const char* name,const std::string& fallback="Not reported") {
    const auto result=value[name].str(); return result.empty()?fallback:bounded(result);
}
std::string turns(const config& room) {
    const auto value=room["room_turns"].str();
    return value=="-1"?"Unlimited":(value.empty()?"Not reported":bounded(value));
}
BattleView room_view(const config& room,const std::string& title) {
    BattleView view;view.title=title;
    view.subtitle=field(room,"name")+" · "+individual_listing_status(room);
    const auto map=room["map_data"].str().empty()?battle_map_preview(room["mp_scenario"].str()):room["map_data"].str();
    view.rows.push_back({field(room,"mp_scenario_name",field(room,"mp_scenario")),individual_listing_settings(room),
        "Observers: "+std::string(room["observer"].to_bool()?"Allowed":"Not allowed")
        +"\nAll seats filled + everyone Ready starts the battle."
        +"\nRule, hero or player changes clear Ready.\nUnranked · No permanent XP",{},map});
    std::string visibility="Fog: "+std::string(room["mp_fog"].to_bool()?"On":"Off")+" · Shroud: "+(room["mp_shroud"].to_bool()?"On":"Off");
    std::string timer="Turn timer: "+std::string(room["mp_countdown"].to_bool()?"On":"Off");
    if(room["mp_countdown"].to_bool())timer+="\nInitial time: "+field(room,"mp_countdown_init_time")+" seconds\nTurn bonus: "+field(room,"mp_countdown_turn_bonus")
        +" seconds\nReserve: "+field(room,"mp_countdown_reservoir_time")+" seconds\nAction bonus: "+field(room,"mp_countdown_action_bonus")+" seconds";
    view.rows.push_back({"Rules & time",visibility,"Turn limit: "+turns(room)+"\n"+visibility+"\n"+timer,{},{}});
    for(const auto& side:room.child_range("room_side")) {
        const bool occupied=!side["player_id"].str().empty();
        std::string state=occupied?(side["ready"].to_bool()?"Ready":"Not ready"):"Open place";
        if(occupied&&room["room_status"].str()=="playing"&&!side["connected"].to_bool())state="Disconnected";
        const auto unit=side["unit_type"].str();
        const auto* form=find_hero_unit(unit);
        std::string hero=unit.empty()?"Hero not selected":(form?hero_name(form->hero_id):"Selected hero")+" · Level "+field(side,"hero_level");
        view.rows.push_back({"Side "+field(side,"side")+" · "+field(side,"player_id","Open place"),
            "Team "+field(side,"team_name")+" · "+state,
            hero+"\nStarting gold: "+field(side,"gold")+"\nBase income: "+std::to_string(side["base_income"].to_int(side["income"].to_int()+game_config::base_income))
            +"\nVillage gold: "+field(side,"village_gold")+" · Village support: "+field(side,"village_support")
            +"\nFog: "+(side["fog"].to_bool()?"On":"Off")+" · Shroud: "+(side["shroud"].to_bool()?"On":"Off"),
            unit.empty()?std::string():battle_unit_image(unit),{}});
    }
    return view;
}
bool review_room(const config& room,const std::string& title,const std::string& action) {
    auto view=room_view(room,title);view.actions={{action,true}};
    return show_battle_view(view).action==0;
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
    return "Gold: "+field(room,"room_gold")+" · Base income: "+field(room,"room_base_income","2")+" · Turns: "+turns(room);
}
std::string individual_listing_review(const config& room) {
    std::ostringstream text;
    text<<field(room,"name")<<"\nMap: "<<field(room,"mp_scenario_name",field(room,"mp_scenario"))
        <<"\n"<<individual_listing_status(room)<<"\nStarting gold: "<<field(room,"room_gold")
        <<"\nObservers: "<<(room["observer"].to_bool()?"Allowed":"Not allowed")
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
            <<" | Gold "<<field(side,"gold")<<" | Base income "<<(side["base_income"].to_int(side["income"].to_int()+game_config::base_income))
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
std::string individual_room_status() {
    if(!active)return "Preparing room…";
    return field(current_room,"name","Room")+" · "+individual_listing_status(current_room);
}
bool review_individual_join(const config& room) {
    return review_room(room,"Review and join","Agree and join");
}
bool show_individual_details(const config& room,bool can_observe) {
    auto view=room_view(room,"Battle room");
    if(can_observe)view.actions={{"Observe",true}};
    return show_battle_view(view).action==0&&can_observe;
}
void show_individual_settings() {
    if(active)show_individual_details(current_room,false);
}
bool confirm_individual_ready() {
    if(!active) return true;
    const auto id=current_game;const auto before=revision;
    auto view=room_view(current_room,"Ready for battle");
    if(room_changed)view.subtitle="Room updated · Review these settings before Ready";
    view.actions={{"Set Ready",true}};
    if(show_battle_view(view).action!=0)return false;
    if(!active||current_game!=id||revision!=before) {
        gui2::show_message("Individual battle","The room changed while you reviewed it. Review the current settings and choose Ready again.",gui2::dialogs::message::ok_button,false,false);
        return false;
    }
    return true;
}
bool configure_individual_creation(config& scenario,bool eligible) {
    if(!eligible||!supported)return false;
    const auto count=scenario.child_count("side");if(count<2||count>8)return false;
    for(auto& side:scenario.child_range("side")) {
        if(!side["allow_player"].to_bool(true))return false;
        side["controller"]="human";side["allow_player"]=true;side["controller_lock"]=true;
        side["gold_lock"]=true;side["income_lock"]=true;side["team_lock"]=true;
    }
    scenario["individual_room"]=true;return true;
}
#include "alliance_game/individual_creation.inc"

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
    if(query.rfind("capacity:",0)==0)return room&&(*room)["room_capacity"].str()==query.substr(9);
    if(query.rfind("gold:",0)==0)return room&&(*room)["room_gold"].str()==query.substr(5);
    if(query.rfind("turns:",0)==0)return room&&((*room)["room_turns"].str()==query.substr(6)||(query.substr(6)=="unlimited"&&(*room)["room_turns"].str()=="-1"));
    if(query=="slots:open")return room&&(*room)["room_status"].str()=="waiting"&&(*room)["room_members"].to_int()<(*room)["room_capacity"].to_int();
    return -1;
}
void reset_individual_filters() noexcept {filter_gold=filter_turns=filter_capacity=0;}
std::string individual_filter_label() {
    std::string label="Gold: "+(filter_gold?std::to_string(filter_gold):std::string("Any"));
    label+=" · Turns: "+(filter_turns==-1?std::string("Unlimited"):filter_turns?std::to_string(filter_turns):std::string("Any"));
    label+=" · Players: "+(filter_capacity?std::to_string(filter_capacity):std::string("Any"));
    return label;
}
bool individual_filter_settings(const config* room) {
    if(!filter_gold&&!filter_turns&&!filter_capacity)return true;
    return room&&(!filter_gold||(*room)["room_gold"].to_int()==filter_gold)
        &&(!filter_turns||(*room)["room_turns"].to_int()==filter_turns)
        &&(!filter_capacity||(*room)["room_capacity"].to_int()==filter_capacity);
}
bool edit_individual_filters() {
    int gold=filter_gold,limit=filter_turns,capacity=filter_capacity,selected=0;
    for(;;) {
        BattleView view;view.title="Room filters";view.subtitle="Players, gold and turn limit";
        view.selected=selected;
        view.rows={{"Starting gold",gold?std::to_string(gold):"Any","Choose the starting gold you want to play with.",{},{}},
            {"Turn limit",limit==-1?"Unlimited":limit?std::to_string(limit):"Any","Find short battles or rooms with no turn limit.",{},{}},
            {"Players",capacity?std::to_string(capacity):"Any","Total human seats in the room.",{},{}}};
        view.actions={{"Change",true},{"Apply filters",true},{"Clear limits",true}};
        const auto result=show_battle_view(view);
        if(result.action<0)return false;
        if(result.action==1){filter_gold=gold;filter_turns=limit;filter_capacity=capacity;return true;}
        if(result.action==2){gold=limit=capacity=0;continue;}
        selected=result.row;
        if(result.action!=0||selected<0||selected>2)continue;
        if(selected==0) {
            const int answer=choose("Starting gold","Show rooms with this starting gold.",{"Any","100","150","200","300","500"});
            static constexpr int amounts[]{0,100,150,200,300,500};
            if(answer>=0&&answer<6)gold=amounts[answer];
        } else if(selected==1) {
            const int answer=choose("Turn limit","Show rooms with this turn limit.",{"Any","Unlimited","20","30","40","50","100"});
            static constexpr int limits[]{0,-1,20,30,40,50,100};
            if(answer>=0&&answer<7)limit=limits[answer];
        } else {
            const int answer=choose("Players","Total human seats.",{"Any","2","3","4","5","6","7","8"});
            if(answer>=0&&answer<8)capacity=answer?answer+1:0;
        }
    }
}

}
