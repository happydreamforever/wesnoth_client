// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/shared_launch.hpp"
#include "alliance_game/shared_scenario.hpp"
#include "alliance_game/shared_result.hpp"
#include "alliance_game/shared_controls.hpp"
#include "alliance_native/account_client.hpp"
#include "cursor.hpp"
#include "events.hpp"
#include "game_config_manager.hpp"
#include "game_initialization/playcampaign.hpp"
#include "gui/dialogs/message.hpp"
#include "gui/dialogs/network_transmission.hpp"
#include "map/map.hpp"
#include "saved_game.hpp"
#include "serialization/parser.hpp"
#include "units/types.hpp"
#include "wesnothd_connection.hpp"
#include "video.hpp"
#include "scripting/plugins/manager.hpp"
#include <cstdlib>
#include <thread>
#include <openssl/crypto.h>
#include <openssl/evp.h>
#include <algorithm>
#include <chrono>
#include <exception>
#include <iostream>
#include <functional>
#include <memory>
#include <sstream>
#include <stdexcept>
namespace alliance_game {
namespace {
std::optional<SharedAdmission> pending;
bool uuid(const std::string& s){if(s.size()!=36)return false;for(std::size_t i=0;i<s.size();++i)if(i==8||i==13||i==18||i==23){if(s[i]!='-')return false;}else if(!((s[i]>='0'&&s[i]<='9')||(s[i]>='a'&&s[i]<='f')))return false;return true;}
void wipe(std::string& s){if(!s.empty())OPENSSL_cleanse(s.data(),s.size());s.clear();}
struct SecretCleanup {SharedAdmission& value;~SecretCleanup(){wipe(value.token);}};
class ReceiveJob final:public gui2::dialogs::network_transmission::connection_data {
    wesnothd_connection& connection_;std::function<bool(const config&)> accept_;
    std::chrono::steady_clock::time_point deadline_;bool done_=false,cancelled_=false;std::exception_ptr failure_;
public:
    ReceiveJob(wesnothd_connection& c,std::function<bool(const config&)> accept,int seconds):connection_(c),accept_(std::move(accept)),deadline_(std::chrono::steady_clock::now()+std::chrono::seconds(seconds)){}
    bool finished()override{return done_;}
    void cancel()override{cancelled_=true;done_=true;connection_.cancel();}
    void poll()override{if(done_)return;try{if(std::chrono::steady_clock::now()>=deadline_)throw std::runtime_error("Shared response timed out.");config data;for(int i=0;i<16&&connection_.receive_data(data);++i){if(accept_(data)){done_=true;return;}data.clear();}}catch(...){failure_=std::current_exception();done_=true;}}
    void result(){if(failure_)std::rethrow_exception(failure_);if(cancelled_||!done_)throw std::runtime_error("Shared wait was cancelled.");}
};
void receive(wesnothd_connection& connection,const std::string& text,std::function<bool(const config&)> accept,int seconds=15){
    ReceiveJob job(connection,std::move(accept),seconds);
    if(video::headless()) { while(!job.finished()) { job.poll(); std::this_thread::sleep_for(std::chrono::milliseconds(10)); } job.result(); return; }
    gui2::dialogs::network_transmission dialog(job,"Alliance shared battle",text+"\nCancel returns to the main menu.");
    dialog.set_allow_plugin_skip(false);
    bool accepted=false;try{accepted=dialog.show();}catch(...){job.cancel();throw;}
    if(!accepted)job.cancel();job.result();
}
config response(wesnothd_connection& connection,const std::string& tag,const std::string& message){config result;receive(connection,message,[&](const config& packet){if(packet.all_children_count()!=1||!packet.has_child(tag))throw std::runtime_error("Unexpected shared response.");result=packet.mandatory_child(tag);return true;});return result;}
std::string base64(const char* p,std::size_t count){std::string value(4*((count+2)/3),'\0');const int n=EVP_EncodeBlock(reinterpret_cast<unsigned char*>(value.data()),reinterpret_cast<const unsigned char*>(p),count);if(n<0||static_cast<std::size_t>(n)!=value.size())throw std::runtime_error("Replay encoding failed.");return value;}
std::string decode_base64(const std::string& text){
    if(text.empty()||text.size()>32000||text.size()%4)throw std::runtime_error("Invalid replay data encoding.");
    std::string bytes(text.size()/4*3,'\0');const int n=EVP_DecodeBlock(reinterpret_cast<unsigned char*>(bytes.data()),reinterpret_cast<const unsigned char*>(text.data()),text.size());
    if(n<0)throw std::runtime_error("Invalid replay data encoding.");std::size_t length=n;if(text.back()=='=')--length;if(text.size()>1&&text[text.size()-2]=='=')--length;bytes.resize(length);
    if(base64(bytes.data(),bytes.size())!=text)throw std::runtime_error("Noncanonical replay encoding.");return bytes;
}
void download(wesnothd_connection& connection,const SharedAdmission& admission){
    const auto info=response(connection,"alliance_replay_info","Checking the retained server replay.");
    const auto source=info["account_id"].str(),hash=info["sha256"].str();const auto size=info["bytes"].to_unsigned();
    if(info["format"].str()!="wesnoth-replay-v1"||info["run_id"].str()!=admission.run_id||!uuid(source)||size==0||size>32*1024*1024||hash.size()!=64||!std::all_of(hash.begin(),hash.end(),[](char x){return(x>='0'&&x<='9')||(x>='a'&&x<='f');}))throw std::runtime_error("Invalid server replay metadata.");
    std::string bytes;bytes.reserve(size);
    while(bytes.size()<size){connection.send_data(config{"alliance_replay_get",config{"offset",static_cast<unsigned long long>(bytes.size())}});const auto chunk=response(connection,"alliance_replay_data","Downloading the completed battle replay.");if(chunk["offset"].to_unsigned(size+1)!=bytes.size())throw std::runtime_error("Downloaded replay offset differs.");const auto block=decode_base64(chunk["data"].str());if(block.empty()||block.size()>size-bytes.size())throw std::runtime_error("Invalid replay chunk size.");bytes+=block;}
    store_downloaded_shared_replay(admission.account_id,admission.run_id,source,hash,bytes);
    if(video::headless())std::cout<<"SHARED SERVER REPLAY VERIFIED"<<std::endl;
    else gui2::show_message("Alliance","The completed server replay was downloaded and verified. Choose Watch replay in this battle's history. Watching cannot award experience.");
}
void captain_policy(const SharedAdmission& admission,const alliance::account::Battle& battle,int side){
    if(admission.captain_action.empty())return;
    if(!video::headless()||!std::getenv("WESNOTH_ALLIANCE_COMPANION_PROFILE")||!plugins_manager::get())throw std::runtime_error("Captain policy requires the explicit headless companion.");
    const auto team=std::find_if(battle.teams.begin(),battle.teams.end(),[&](const auto& team){return team.captain_id==admission.account_id;});
    if(team==battle.teams.end()||std::none_of(team->players.begin(),team->players.end(),[&](const auto& player){return player.id==admission.account_id&&player.side==side;}))throw std::runtime_error("Captain policy is not assigned to this captain's side.");
    // All inserted fields are fixed enumerations or bounded integers validated
    // before connecting. No session, username or arbitrary Lua enters this script.
    std::ostringstream script;
    script<<"return function(events,context,info) local sent=false local passed=0 while true do "
          <<"if info.name=='Game' and info.can_move().can_move and info.current_side().current_side=="<<side<<" then "
          <<"local turn=info.current_turn().current_turn if not sent and turn>="<<admission.after_turn<<" then sent=true "
          <<"context.synced_command{{'custom_command',{name='alliance_captain_v1',{'data',{action='"<<admission.captain_action<<"'}}}}} "
          <<"elseif passed~=turn then passed=turn context.end_turn{} end end "
          <<"events,context,info=wesnoth.plugin.next_slice() end end";
    auto* manager=plugins_manager::get();const auto index=manager->add_plugin("Alliance explicit captain test policy",script.str());manager->start_plugin(index);
}
void upload(wesnothd_connection& connection,const SharedPending& result){
    const auto bytes=read_shared_replay(result);
    connection.send_data(config{"alliance_replay_begin",config{"bytes",static_cast<unsigned long long>(bytes.size()),"sha256",result.replay_sha256}});
    auto ready=response(connection,"alliance_replay_ready","Preparing the saved battle replay.");if(ready["offset"].to_unsigned(1)!=0)throw std::runtime_error("Replay offset mismatch.");
    for(std::size_t offset=0;offset<bytes.size();){const auto size=std::min<std::size_t>(24000,bytes.size()-offset);connection.send_data(config{"alliance_replay_chunk",config{"offset",static_cast<unsigned long long>(offset),"data",base64(bytes.data()+offset,size)}});offset+=size;ready=response(connection,"alliance_replay_ready","Uploading the saved battle replay.");if(ready["offset"].to_unsigned()!=offset)throw std::runtime_error("Replay offset mismatch.");}
    connection.send_data(config{"alliance_result",config{"winner_team_id",result.winner_team_id,"draw",result.draw,"replay_sha256",result.replay_sha256}});
    const auto saved=response(connection,"alliance_result_saved","Saving the battle result for captain review.");
    const auto state=saved["state"].str();
    if(saved["run_id"].str()!=result.run_id || (state!="waiting_for_players"&&state!="disputed"&&state!="awaiting_captains"&&state!="agreed"&&state!="settled"))throw std::runtime_error("Shared result acknowledgement mismatch.");
    acknowledge_shared_result(result);
    if(video::headless())std::cout<<"SHARED RESULT UPLOAD ACKNOWLEDGED"<<std::endl;
    else gui2::show_message("Alliance","The battle result and replay reached the Windows server. Both captains must confirm the result before rewards are settled.");
}
}
void queue_shared(SharedAdmission admission){
    SecretCleanup cleanup{admission};
    const bool token=admission.token.size()==43&&std::all_of(admission.token.begin(),admission.token.end(),[](char x){return(x>='a'&&x<='z')||(x>='A'&&x<='Z')||(x>='0'&&x<='9')||x=='-'||x=='_';});
    const bool captain=admission.captain_action.empty()?admission.after_turn==0:
        ((admission.captain_action=="surrender"||admission.captain_action=="offer_draw"||admission.captain_action=="withdraw_draw")&&admission.after_turn>=1&&admission.after_turn<=100&&!admission.computer_controlled&&!admission.upload_only&&!admission.download_only);
    if(!captain||pending||(admission.upload_only&&admission.download_only)||!uuid(admission.run_id)||!uuid(admission.account_id)||!token||admission.profile.port!=15002||!alliance::validate_profile(admission.profile))throw std::invalid_argument("Invalid shared admission.");
    pending=std::move(admission);
}
bool shared_pending()noexcept{return pending.has_value();}
std::optional<SharedAdmission> take_pending_shared(){auto result=std::move(pending);pending.reset();return result;}
void run_pending_shared(){
    auto admission=take_pending_shared();if(!admission)return;SecretCleanup cleanup{*admission};
    try{
        wesnothd_connection connection(admission->profile);connection.wait_for_handshake();
        config request;auto& join=request.add_child("alliance_join");join["run_id"]=admission->run_id;join["intent"]=admission->download_only?"replay_download":admission->upload_only?"result_retry":"play";join["token"]=admission->token;
        connection.send_data(request);join["token"]="";request.clear();wipe(admission->token);
        if(admission->download_only){download(connection,*admission);return;}
        const auto room=response(connection,"alliance_room","Checking your fixed battle seat.");
        auto descriptor=alliance::account::parse_shared_battle_descriptor(room["descriptor"].str());
        if(descriptor.id!=admission->run_id)throw std::runtime_error("Shared run mismatch.");
        if(admission->upload_only){const auto records=inspect_pending_shared(admission->account_id);const auto found=std::find_if(records.begin(),records.end(),[&](const auto& r){return r.run_id==admission->run_id;});if(found==records.end())throw std::runtime_error("No saved shared result.");upload(connection,*found);return;}
        const auto plan=make_shared_plan(descriptor,admission->account_id);
        const int side=room["side"].to_int();if(side!=plan.controlled_side)throw std::runtime_error("Shared seat mismatch.");
        captain_policy(*admission,descriptor,side);
        const auto seed=room["seed"].str();if(seed.size()!=8||!std::all_of(seed.begin(),seed.end(),[](char x){return(x>='0'&&x<='9')||(x>='a'&&x<='f');}))throw std::runtime_error("Invalid shared random seed.");
        const bool reconnect=room["reconnect"].to_bool();

        auto* manager=game_config_manager::get();if(!manager)throw std::runtime_error("Game data unavailable.");manager->load_game_config_for_create(true);
        const auto& game=manager->game_config();const auto source=game.find_child("multiplayer","id",plan.scenario_id),era=game.find_child("era","id","era_default");if(!source||!era)throw std::runtime_error("Shared map unavailable.");
        auto scenario=make_shared_scenario(plan,*source,*era);add_shared_controls(scenario,descriptor);saved_game::expand_map_file(scenario);
        const gamemap map(scenario["map_data"].str());if(map.num_valid_starting_positions()!=static_cast<int>(plan.seats.size()))throw std::runtime_error("Shared map positions do not match.");
        for(const auto& seat:plan.seats){const auto* type=unit_types.find(seat.unit_type);if(!type||type->level()!=seat.level||!map.on_board(map.starting_position(seat.side)))throw std::runtime_error("Shared leader unavailable.");}
        const auto hash=shared_scenario_digest(scenario);localize_shared_scenario(scenario,side);
        if(admission->computer_controlled)scenario.find_child("side","side",std::to_string(side))->operator[]("controller")="ai";
        saved_game state;state.classification().type=campaign_type::type::multiplayer;state.classification().era_id="era_default";state.classification().end_credits=false;
        state.mp_settings().name=scenario["name"].str();state.mp_settings().mp_scenario=scenario["id"].str();state.mp_settings().use_map_settings=true;state.mp_settings().shuffle_sides=false;
        state.mp_settings().num_turns=-1;state.mp_settings().fog_game=true;state.mp_settings().shroud_game=false;state.mp_settings().xp_modifier=100;state.mp_settings().allow_observers=false;state.mp_settings().private_replay=true;
        state.set_scenario(std::move(scenario));state.set_carryover_sides_start(config{"random_seed",seed,"random_calls",0});
        connection.send_data(config{"alliance_loaded",config{"scenario_sha256",hash}});
        std::size_t replay_bytes=0,commands=0;
        receive(connection,"Waiting for all fixed players to load the same battle. No side may be replaced by a guest.",[&](const config& frame){
            if(frame.all_children_count()!=1)throw std::runtime_error("Invalid shared barrier packet.");
            if(frame.has_child("alliance_waiting")) { const auto& waiting=frame.mandatory_child("alliance_waiting"); if(reconnect||waiting["required"].to_int()!=static_cast<int>(plan.seats.size())||waiting["connected"].to_int()<1||waiting["connected"].to_int()>static_cast<int>(plan.seats.size()))throw std::runtime_error("Invalid shared wait count.");return false; }
            if(frame.has_child("alliance_start")){if(frame.mandatory_child("alliance_start")["reconnect"].to_bool()!=reconnect)throw std::runtime_error("Shared restart mismatch.");return true;}
            if(!reconnect||!frame.has_child("turn"))throw std::runtime_error("Unexpected shared barrier packet.");
            std::ostringstream text;io::write(text,frame);replay_bytes+=text.str().size();const auto& turn=frame.mandatory_child("turn");commands+=turn.child_count("command");
            if(replay_bytes>32*1024*1024||commands>100000||turn.all_children_count()!=turn.child_count("command"))throw std::runtime_error("Shared replay exceeds its limit.");state.get_replay().append_config(turn);return false;
        },600);
        mp_game_metadata metadata(connection);metadata.is_host=side==1;metadata.queue_id=0;metadata.current_turn=room["current_turn"].to_unsigned(1);metadata.skip_replay=reconnect;
        if(metadata.current_turn<1||metadata.current_turn>1000000)throw std::runtime_error("Invalid shared replay turn.");
        for(const auto& seat:plan.seats)metadata.connected_players.insert(seat.username);
        events::discard_input();cursor::set(cursor::NORMAL);SharedActiveLaunch active(plan,admission->account_id);
        campaign_controller controller(state);controller.set_mp_info(&metadata);controller.play_game();
        const auto records=inspect_pending_shared(admission->account_id);const auto result=std::find_if(records.begin(),records.end(),[&](const auto& row){return row.run_id==admission->run_id&&row.status=="pending";});
        if(result!=records.end())upload(connection,*result);
    }catch(const std::exception&){if(video::headless()){std::cerr<<"Shared battle did not complete. No automatic retry was made. [shared-connection]"<<std::endl;return;}gui2::show_error_message("The shared battle connection or replay transfer did not complete. No automatic retry was made. Return to Alliance to reconnect or submit a retained result. Keep the Windows database, account service and shared battle service running. [shared-connection]");}
}
}
