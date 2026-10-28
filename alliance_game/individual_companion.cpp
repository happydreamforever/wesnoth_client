// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/individual_companion.hpp"
#include "alliance_game/casual_sides.hpp"
#include "alliance_native/account_client.hpp"
#include "alliance_native/account_protocol.hpp"
#include "alliance_native/progression_protocol.hpp"
#include "config.hpp"
#include "game_config_manager.hpp"
#include "game_initialization/mp_game_utils.hpp"
#include "game_initialization/playcampaign.hpp"
#include "preferences/preferences.hpp"
#include "saved_game.hpp"
#include "video.hpp"
#include "wesnothd_connection.hpp"
#include <openssl/crypto.h>
#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>
#include <thread>

namespace alliance_game {
namespace {
using Clock = std::chrono::steady_clock;
void require(bool value) { if(!value) throw std::runtime_error("Individual companion check failed."); }
void wipe(std::string& value) { if(!value.empty()) OPENSSL_cleanse(value.data(),value.size()); value.clear(); }
struct Secret { std::string& value; ~Secret(){wipe(value);} };
std::string line() {
    std::string value;
    for(char byte; std::cin.get(byte);) {
        if(byte=='\n') return value;
        require(value.size()<4096);
        value+=byte;
    }
    throw std::runtime_error("Companion input closed.");
}
config packet(wesnothd_connection& connection, int seconds=20) {
    const auto end=Clock::now()+std::chrono::seconds(seconds);
    while(Clock::now()<end) {
        config value;
        if(connection.receive_data(value)) {
            require(!value.has_child("error")&&!value.has_child("leave_game")&&!value["failed"].to_bool());
            return value;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    throw std::runtime_error("Companion connection timed out.");
}
config until(wesnothd_connection& connection,const std::string& tag) {
    const auto deadline=Clock::now()+std::chrono::seconds(20);
    for(int i=0;i<200&&Clock::now()<deadline;++i) {
        const auto remaining=std::chrono::duration_cast<std::chrono::seconds>(deadline-Clock::now()).count();
        auto value=packet(connection,static_cast<int>(remaining)+1);if(value.has_child(tag))return value;
    }
    throw std::runtime_error("Companion response missing.");
}
alliance::account::Reply account(const alliance::TrustedProfile& profile,
    const std::string& token,const std::string& operation,const std::string& game={},int side=0,
    const std::string& hero={},const std::string& form={}) {
    alliance::account::Request request;request.operation=operation;request.token=token;
    Secret cleanup{request.token};request.game_id=game;request.side=side;
    request.hero_id=hero;request.form_id=form;
    auto reply=alliance::account::perform_request(profile,request,alliance::CancellationToken{});
    require(reply.ok);return reply;
}
config& scenario(config& level) {
    if(auto value=level.optional_child("scenario")) return *value;
    throw std::runtime_error("Individual room has no scenario.");
}
int own_side(config& level,const std::string& username) {
    if(!level.has_child("scenario"))return 0;
    int found=0;
    for(const auto& side:scenario(level).child_range("side"))
        if(side["player_id"].str()==username) { require(!found);found=side["side"].to_int(); }
    return found;
}
std::int64_t revision(const config& room) {
    const auto value=room["room_revision"].to_long_long();require(value>0);return value;
}
void review(const config& room,const std::string& action,int room_id,int side=0) {
    namespace json=boost::json;
    json::object value{{"action",action},{"room_id",room_id},{"room_revision",revision(room)},
        {"name",room["name"].str()},{"map",room["mp_scenario_name"].str()},
        {"members",room["room_members"].to_int()},{"capacity",room["room_capacity"].to_int()},
        {"gold",room["room_gold"].str()},{"turns",room["room_turns"].str()},{"side",side},
        {"era",room["mp_era"].str()},{"fog",room["mp_fog"].to_bool()},
        {"shroud",room["mp_shroud"].to_bool()},{"observers",room["observer"].to_bool()},
        {"timer",room["mp_countdown"].to_bool()}};
    for(const auto* key:{"mp_countdown_init_time","mp_countdown_reservoir_time","mp_countdown_turn_bonus","mp_countdown_action_bonus"})
        value[key]=room[key].to_int();
    json::array sides;
    for(const auto& seat:room.child_range("room_side")) {
        require(sides.size()<8&&seat["team_name"].str().size()<=256&&seat["player_id"].str().size()<=24);
        sides.push_back(json::object{{"side",seat["side"].to_int()},{"team",seat["team_name"].str()},
            {"player",seat["player_id"].str()},{"gold",seat["gold"].to_int()},
            {"income",seat["income"].to_int()},{"village_gold",seat["village_gold"].to_int()},
            {"village_support",seat["village_support"].to_int()},
            {"fog",seat["fog"].to_bool()},{"shroud",seat["shroud"].to_bool()}});
    }
    value["sides"]=std::move(sides);
    require(value.at("name").as_string().size()<=256&&value.at("map").as_string().size()<=256);
    std::cout<<"INDIVIDUAL COMPANION REVIEW "<<json::serialize(value)<<std::endl;
}
bool consent(const std::string& action,std::int64_t expected) {
    auto text=line();Secret cleanup{text};auto value=alliance::account::parse_document(text);
    namespace protocol=alliance::progression_protocol;
    protocol::keys(value,{"action","revision"});
    return protocol::text(value,"action",8)==action&&protocol::number(value,"revision",1,2147483647)==expected;
}
int select_room(const config& listing,const std::optional<int>& requested_id,const std::string& requested_name) {
    require(requested_id.has_value()!=!requested_name.empty());
    int selected=0;
    for(const auto& room:listing.child_range("game")) {
        const bool matches=requested_id ? room["id"].str()==std::to_string(*requested_id) :
            room["name"].str()==requested_name;
        if(!matches)continue;
        // Count every exact-name publication, including full/running rooms.
        // Never silently choose another room when the requested name is shared.
        require(!selected);
        const auto id=room["id"].to_long_long();
        require(id>0&&id<=2147483647&&room["id"].str()==std::to_string(id));
        selected=static_cast<int>(id);
    }
    require(selected>0);
    return selected;
}
}
bool individual_companion_requested() noexcept {
    return std::getenv("WESNOTH_INDIVIDUAL_COMPANION_PROFILE")!=nullptr;
}
int run_individual_companion() noexcept {
    try {
        const auto* path=std::getenv("WESNOTH_INDIVIDUAL_COMPANION_PROFILE");
        require(video::headless()&&path&&*path);
        auto input=line();Secret input_cleanup{input};
        auto message=alliance::account::parse_document(input);
        namespace protocol=alliance::progression_protocol;
        const bool by_id=message.contains("room_id");
        if(by_id)protocol::keys(message,{"intent","account_id","token","room_id","hero_id","form_id"});
        else protocol::keys(message,{"intent","account_id","token","room_name","hero_id","form_id"});
        require(protocol::text(message,"intent",32)=="individual_play");
        const auto account_id=protocol::text(message,"account_id",36);
        auto token=protocol::text(message,"token",43);Secret token_cleanup{token};
        const std::optional<int> requested_id=by_id?std::optional<int>(protocol::number(message,"room_id",1,2147483647)):std::nullopt;
        const auto requested_name=by_id?std::string{}:protocol::text(message,"room_name",256);
        const auto hero=protocol::text(message,"hero_id",80),form=protocol::text(message,"form_id",80);
        require(alliance::account::token_valid(token));
        auto& json_token=message.at("token").as_string();
        if(!json_token.empty())OPENSSL_cleanse(json_token.data(),json_token.size());
        wipe(input);
        const auto profile=alliance::account::read_public_profile(path);
        const auto me=account(profile,token,"me").profile;
        require(me.account_id==account_id);
        const auto owned=std::find_if(me.heroes.begin(),me.heroes.end(),[&](const auto& row){return row.hero_id==hero;});
        require(owned!=me.heroes.end()&&std::find(owned->forms.begin(),owned->forms.end(),form)!=owned->forms.end());
        auto ticket=account(profile,token,"casual_ticket");Secret ticket_cleanup{ticket.ticket};
        require(ticket.account_id==account_id&&ticket.username==me.username&&ticket.database_generation==me.database_generation);
        auto native_profile=profile;native_profile.port=15000;
        wesnothd_connection connection(native_profile);
        auto hello=until(connection,"version").mandatory_child("version");
        require(hello["alliance_protocol"].str()=="4"&&hello["implementation_release"].str()=="1192624"&&hello["individual_rooms"].str()=="1");
        connection.send_data(config{"version",config{"version","1.19.26"}});until(connection,"mustlogin");
        config login{"login",config{"username",me.username,"alliance_ticket",ticket.ticket}};
        connection.send_data(login);login.clear();wipe(ticket.ticket);until(connection,"join_lobby");
        auto listing=until(connection,"gamelist").mandatory_child("gamelist");
        const int room_id=select_room(listing,requested_id,requested_name);
        auto selected=listing.find_child("game","id",std::to_string(room_id));require(bool(selected));
        config room=*selected;
        require(room["individual_room"].to_bool()&&room["room_status"].str()=="waiting"&&
                room["room_members"].to_int()<room["room_capacity"].to_int());
        review(room,"join",room_id);
        require(consent("join",revision(room)));
        connection.send_data(config{"join",config{"id",room_id,"observe",false,"room_revision",revision(room)}});
        prefs::get().set_login(me.username);reset_casual_game();
        config level;std::string game;int side=0;bool selected_hero=false,own_ready=false,started=false;
        std::int64_t asked_revision=0;auto waiting_deadline=Clock::now()+std::chrono::minutes(30);
        std::optional<Clock::time_point> ready_deadline;
        auto observe=[&](const config& value) {
            require(!value.has_child("error")&&!value.has_child("leave_game")&&!value["failed"].to_bool());
            if(auto context=value.optional_child("alliance_game")) {
                require(context["individual_room"].to_bool());
                if(game.empty())game=context["id"].str();else require(game==context["id"].str());
                room.merge_attributes(*context);
                room.remove_children("room_side");
                for(const auto& seat:context->child_range("room_side"))room.add_child("room_side",seat);
                if(asked_revision&&revision(room)!=asked_revision)ready_deadline.reset();
            }
            if(value.has_child("scenario"))level=value;
            else if(auto diff=value.optional_child("scenario_diff")) { require(level.has_child("scenario"));level.apply_diff(*diff); }
            if(auto change=value.optional_child("change_controller")) {
                if(level.has_child("scenario"))if(auto target=scenario(level).find_child("side","side",change["side"]))target->merge_with(*change);
            }
            const auto assigned=own_side(level,me.username);if(assigned){if(side)require(side==assigned);side=assigned;}
            for(const auto& selection:value.child_range("alliance_selection")) {
                if(selection["game_id"].str()!=game||selection["side"].to_int()!=side)continue;
                if(selection["cleared"].to_bool()) { require(!selected_hero);own_ready=false;continue; }
                require(selection["account_id"].str()==account_id&&selection["hero_id"].str()==hero&&selection["form_id"].str()==form);
                selected_hero=true;own_ready=selection["ready"].to_bool();
                if(own_ready)ready_deadline.reset();
            }
            if(value.has_child("start_game")) { require(selected_hero&&own_ready&&side>0);started=true; }
        };
        bool selection_sent=false;
        std::cout<<"INDIVIDUAL COMPANION JOINING: authenticated player; no permanent rewards"<<std::endl;
        while(!started&&Clock::now()<waiting_deadline) {
            config value;bool received=false;
            while(connection.receive_data(value)){observe(value);value.clear();received=true;}
            if(started)break;
            if(side&&!game.empty()&&!selection_sent) {
                auto chosen=account(profile,token,"casual_select",game,side,hero,form);
                Secret chosen_cleanup{chosen.selection_ticket};
                config command{"alliance_hero",config{"ticket",chosen.selection_ticket,"side",side}};
                connection.send_data(command);command.clear();wipe(chosen.selection_ticket);selection_sent=true;
            }
            if(selected_hero&&!own_ready&&asked_revision!=revision(room)) {
                const auto reviewed=revision(room);asked_revision=reviewed;review(room,"ready",room_id,side);
                require(consent("ready",reviewed));
                while(connection.receive_data(value)){observe(value);value.clear();}
                if(!started&&selected_hero&&!own_ready&&revision(room)==reviewed) {
                    connection.send_data(config{"alliance_ready",config{"side",side,"ready",true,"room_revision",reviewed}});
                    ready_deadline=Clock::now()+std::chrono::seconds(20);
                }
            }
            require(!ready_deadline||Clock::now()<*ready_deadline);
            if(!received)std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        require(started&&level.has_child("scenario")&&side>0);
        auto* manager=game_config_manager::get();require(manager!=nullptr);
        saved_game state;state.classification().type=campaign_type::type::multiplayer;
        mp::level_to_gamestate(level,state);
        manager->load_game_config_for_game(state.classification(),state.get_scenario_id());
        state.classification().end_credits=false;
        for(auto& row:state.get_starting_point().child_range("side")) {
            const bool own=row["side"].to_int()==side;
            row["is_local"]=own;if(own)row["controller"]="ai";
        }
        mp_game_metadata metadata(connection);metadata.is_host=false;metadata.queue_id=0;
        for(const auto& row:state.get_starting_point().child_range("side"))
            if(!row["player_id"].empty())metadata.connected_players.insert(row["player_id"].str());
        std::cout<<"INDIVIDUAL COMPANION STARTED: computer controls only its selected side"<<std::endl;
        campaign_controller controller(state);controller.set_mp_info(&metadata);
        const auto result=controller.play_game();
        connection.send_data(config{"leave_game"});reset_casual_game();
        require(result==level_result::type::victory||result==level_result::type::defeat||result==level_result::type::observer_end);
        std::cout<<"INDIVIDUAL COMPANION FINISHED: native battle ended; no permanent rewards"<<std::endl;
        return 0;
    } catch(...) {
        reset_casual_game();
        std::cerr<<"INDIVIDUAL COMPANION STOPPED: no completed native battle was confirmed"<<std::endl;
        return 1;
    }
}
}
