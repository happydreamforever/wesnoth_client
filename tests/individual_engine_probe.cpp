// Actual production guest companion plus a deterministic test-only room host.
// Both clients execute the pinned native networked combat engine. Host UI
// clicks and the fixture's turn-3 ending are substituted, not Android-tested.
#include "../alliance_game/individual_companion.cpp"
#include "alliance_game/account_ui.hpp"
#include "alliance_game/companion.hpp"
#include "alliance_game/casual_session.hpp"
#include "serialization/parser.hpp"
#include <fstream>
#include <sstream>

extern "C" int __real_main(int,char**);
namespace alliance_game {
namespace protocol=alliance::progression_protocol;
std::string fixture_stage="enter";
PlayerSession::~PlayerSession()=default;
std::optional<PlayerSession> player_session(){return std::nullopt;}
bool player_request(alliance::account::Request&,alliance::account::Reply&){return false;}
bool choose_owned_hero(std::string&,std::string&){return false;}
bool account_session_interaction_active() noexcept{return false;}
bool has_player_session() noexcept{return false;}

static int fixture_host() {
    const auto profile=alliance::account::read_public_profile(std::getenv("WESNOTH_INDIVIDUAL_COMPANION_PROFILE"));
    auto input=line();Secret clean{input};auto payload=alliance::account::parse_document(input);
    auto token=protocol::text(payload,"token",43);Secret token_clean{token};
    const auto hero=protocol::text(payload,"hero_id",80),form=protocol::text(payload,"form_id",80);
    const auto me=account(profile,token,"me").profile;
    auto native=profile;native.port=15000;wesnothd_connection connection(native);
    until(connection,"version");connection.send_data(config{"version",config{"version","1.19.26"}});until(connection,"mustlogin");
    auto ticket=account(profile,token,"casual_ticket");
    connection.send_data(config{"login",config{"username",me.username,"alliance_ticket",ticket.ticket}});wipe(ticket.ticket);
    until(connection,"join_lobby");until(connection,"gamelist");prefs::get().set_login(me.username);reset_casual_game();
    connection.send_data(config{"create_game",config{"name","Individual actual engine fixture","individual_room",true}});
    auto context=until(connection,"alliance_game");observe_casual_message(context);
    const auto game=context.mandatory_child("alliance_game")["id"].str();
    auto* manager=game_config_manager::get();require(manager);manager->load_game_config_for_create(true);
    const auto source=manager->game_config().find_child("multiplayer","id","multiplayer_The_Freelands");require(bool(source));
    config scene=*source;scene["individual_room"]=true;scene["turns"]=10;scene["victory_when_enemies_defeated"]=false;
    saved_game::expand_map_file(scene);scene.remove_children("side");
    for(int index=1;index<=2;++index) {
        auto& row=scene.add_child("side",config{"side",index,"controller","human","allow_player",true,
            "player_id",index==1?me.username:"","current_player",index==1?me.username:"",
            "team_name",std::to_string(index),"gold",100,"income",2,"village_gold",2,"village_support",1,
            "fog",false,"shroud",false,"recruit","Spearman,Bowman"});
        row.add_child("leader",config{"type","Spearman","canrecruit",true,"random_traits",false,"id","FixtureLeader"+std::to_string(index)});
    }
    scene.add_child("event",config{"name","prestart"}).add_child("lua")["code"]=R"(
        assert(#wesnoth.sides==2)
        for i=1,2 do
          local us=wesnoth.units.find_on_map{side=i,canrecruit=true}; assert(#us==1)
          assert(us[1].variables.alliance_hero_id and us[1].variables.alliance_hero_level>=1)
        end
        std_print('INDIVIDUAL ACTUAL HERO CONFIG VERIFIED')
    )";
    scene.add_child("event",config{"name","turn 2"}).add_child("lua")["code"]=R"(
        assert(#wesnoth.units.find_on_map{}>2,'Real AI sides must recruit')
        std_print('INDIVIDUAL ACTUAL NETWORK ROUND VERIFIED')
    )";
    auto& end=scene.add_child("event",config{"name","turn 3"});
    end.add_child("lua")["code"]="std_print('INDIVIDUAL ACTUAL SECOND ROUND VERIFIED')";
    end.add_child("endlevel",config{"result","victory","carryover_report",false,"linger_mode",false,"bonus",false});
    saved_game state;state.classification().type=campaign_type::type::multiplayer;state.classification().era_id="era_default";
    state.mp_settings().name="Individual actual engine fixture";state.mp_settings().num_turns=10;
    state.mp_settings().shuffle_sides=false;state.mp_settings().allow_observers=false;
    state.set_scenario(scene);state.set_carryover_sides_start(config{"random_seed","104729","random_calls",0});
    auto level=mp::initial_level_config(state);connection.send_data(level);
    config room;bool sent=false;std::int64_t ready_revision=0;bool started=false;
    const bool change_review=std::getenv("INDIVIDUAL_FIXTURE_REVIEW_CHANGE")!=nullptr;
    bool revised=false;std::int64_t changed_revision=0;
    const auto deadline=Clock::now()+std::chrono::seconds(120);
    while(!started&&Clock::now()<deadline) {
        // A fresh second engine preprocesses the full pinned data before it
        // connects. This fixture deadline includes that isolated cold cache.
        fixture_stage="receive";const auto packet_value=packet(connection,90);
        fixture_stage="observe";observe_casual_message(packet_value);
        if(auto current=packet_value.optional_child("alliance_game")) {
            room=*current;
            if(!sent&&room.has_attribute("room_id")) {
                std::cout<<"INDIVIDUAL HOST ROOM "<<room["room_id"].str()<<std::endl;
                fixture_stage="select";auto chosen=account(profile,token,"casual_select",game,1,hero,form);
                connection.send_data(config{"alliance_hero",config{"ticket",chosen.selection_ticket,"side",1}});wipe(chosen.selection_ticket);sent=true;
            }
        }
        // Rebuild only the two canonical side fields from published assignment.
        fixture_stage="assignment";for(const auto& seat:room.child_range("room_side")) {
            auto side=scenario(level).find_child("side","side",seat["side"]);
            require(bool(side));(*side)["player_id"]=seat["player_id"];(*side)["current_player"]=seat["player_id"];
        }
        fixture_stage="finalize";auto final=level;for(auto& side:scenario(final).child_range("side"))apply_casual_side(side);
        if(change_review&&!revised) {
            for(const auto& selection:packet_value.child_range("alliance_selection"))
                if(selection["side"].to_int()==2&&selection["ready"].to_bool()) {
                    for(auto& side:scenario(final).child_range("side"))side["gold"]=125;
                    revised=true;changed_revision=revision(room);
                    std::cout<<"INDIVIDUAL HOST SETTINGS CHANGED AFTER GUEST READY"<<std::endl;
                }
        }
        const auto diff=final.get_diff(level);if(!diff.empty()){connection.send_data(config{"scenario_diff",diff});level=final;}
        fixture_stage="ready";if(sent&&room["room_members"].to_int()==2&&ready_revision!=revision(room)&&
           (!change_review||(revised&&revision(room)>changed_revision))&&
           scenario(level).child_range("side").begin()->has_attribute("alliance_hero_id")) {
            ready_revision=revision(room);connection.send_data(config{"alliance_ready",config{"side",1,"ready",true,"room_revision",ready_revision}});
        }
        fixture_stage="hint";if(auto hint=packet_value.optional_child("individual_start")) {
            require(hint["game_id"].str()==game&&hint["room_revision"].to_long_long()==revision(room));
            connection.send_data(config{"start_game",config{"room_revision",revision(room)}});
        }
        if(auto ack=packet_value.optional_child("alliance_start")) {require(ack["accepted"].to_bool());started=true;}
    }
    require(started);mp::level_to_gamestate(level,state);state.classification().type=campaign_type::type::multiplayer;
    state.classification().end_credits=false;manager->load_game_config_for_game(state.classification(),state.get_scenario_id());
    for(auto& row:state.get_starting_point().child_range("side")){const bool own=row["side"].to_int()==1;row["is_local"]=own;if(own)row["controller"]="ai";}
    mp_game_metadata metadata(connection);metadata.is_host=true;metadata.queue_id=0;
    for(const auto& row:state.get_starting_point().child_range("side"))metadata.connected_players.insert(row["player_id"].str());
    std::cout<<"INDIVIDUAL HOST STARTED"<<std::endl;
    campaign_controller controller(state);controller.set_mp_info(&metadata);const auto result=controller.play_game();
    require(result==level_result::type::victory||result==level_result::type::defeat);
    connection.send_data(config{"leave_game"});std::cout<<"INDIVIDUAL HOST FINISHED"<<std::endl;return 0;
}
}
bool shared_fixture_companion(){
    static bool consumed=false;if(consumed)return false;consumed=true;
    try {
        const bool host=std::getenv("INDIVIDUAL_FIXTURE_HOST")!=nullptr;
        const int result=host?alliance_game::fixture_host():alliance_game::run_companion();
        if(result)throw std::runtime_error("Individual native engine failed");return true;
    }catch(const std::exception& e){std::cerr<<"FIXTURE ERROR at "<<alliance_game::fixture_stage<<": "<<e.what()<<std::endl;throw;}
}
bool shared_fixture_replay(){return false;}
void shared_fixture_decorate(config&){}
extern "C" int __wrap_main(int argc,char**argv){return __real_main(argc,argv);}
