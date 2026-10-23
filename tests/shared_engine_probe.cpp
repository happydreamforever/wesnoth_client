// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/shared_launch.hpp"
#include "alliance_game/shared_scenario.hpp"
#include "alliance_game/shared_result.hpp"
#include "alliance_game/companion.hpp"
#include "alliance_native/account_client.hpp"
#include "serialization/parser.hpp"
#include "saved_game.hpp"
#include "game_config_manager.hpp"
#include "game_initialization/playcampaign.hpp"
#include <boost/json.hpp>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
extern "C" int __real_main(int,char**);
bool shared_fixture_companion() {
    static bool consumed=false;if(consumed||!std::getenv("ALLIANCE_SHARED_FIXTURE_COMPANION"))return false;consumed=true;
    const int result=alliance_game::run_companion();if(result!=0)throw std::runtime_error("Production companion did not acknowledge its native result.");
    std::cout<<"SHARED PRODUCTION COMPANION ACKNOWLEDGED"<<std::endl;return true;
}
bool shared_fixture_replay() {
    static bool consumed=false;const char* path=std::getenv("ALLIANCE_SHARED_FIXTURE_REPLAY");
    if(!path||consumed)return false;consumed=true;
    std::ifstream input(path,std::ios::binary);if(!input)throw std::runtime_error("Replay fixture is missing.");
    saved_game state(io::read_gz(input));
    game_config_manager::get()->load_game_config_for_create(true);
    campaign_controller controller(state,true);const auto result=controller.play_replay();
    if(result!=level_result::type::victory&&result!=level_result::type::defeat)throw std::runtime_error("Replay did not reach its native END.");
    std::cout<<"SHARED DISK REPLAY REACHED NATIVE END"<<std::endl;return true;
}
// Fixture-only deterministic ending after two real networked AI rounds. This
// event is added identically before each client's canonical-scenario digest.
void shared_fixture_decorate(config& scenario) {
    auto& start=scenario.add_child("event");start["name"]="prestart";
    start.add_child("lua")["code"]=R"(
        assert(#wesnoth.sides==4 or #wesnoth.sides==6 or #wesnoth.sides==8)
        local teams={}
        for i=1,#wesnoth.sides do teams[wesnoth.sides[i].team_name]=(teams[wesnoth.sides[i].team_name] or 0)+1 end
        local count=0 for _,n in pairs(teams) do assert(n==#wesnoth.sides/2) count=count+1 end assert(count==2)
        for i=1,#wesnoth.sides do
            assert(wesnoth.sides[i].gold==100)
            local leaders=wesnoth.units.find_on_map{side=i,canrecruit=true}
            assert(#leaders==1 and leaders[1].type:match('^AH_konrad_'))
            assert(leaders[1].variables.alliance_hero_id=='konrad')
            assert(leaders[1].variables.alliance_hero_level==2)
            local base=wesnoth.unit_types[leaders[1].type].max_hitpoints
            assert(leaders[1].max_hitpoints==math.floor(base*1.02+0.5))
        end
        std_print('SHARED REAL INITIAL CONFIG VERIFIED')
    )";
    auto& round=scenario.add_child("event");round["name"]="turn 2";
    round.add_child("lua")["code"]=R"(
        assert(wesnoth.current.turn==2)
        assert(#wesnoth.units.find_on_map{}>#wesnoth.sides,'AI seats must have recruited through the actual engine')
        std_print('SHARED REAL NETWORK ROUND VERIFIED')
    )";
    if(!std::getenv("ALLIANCE_SHARED_FIXTURE_TERMINAL")) {
    auto& end=scenario.add_child("event");end["name"]="turn 3";
    end.add_child("lua")["code"]=R"(
        std_print('SHARED REAL SECOND NETWORK ROUND VERIFIED')
        wesnoth.wml_actions.kill{side='2,3',fire_event=true,animate=false}
    )";
    }
    // No UI linger in the headless fixture; the native result hook still runs.
    auto& victory=scenario.add_child("event");victory["name"]="victory";
    victory.add_child("endlevel",config{"result","victory","carryover_report",false,"linger_mode",false,"bonus",false});
    auto& defeat=scenario.add_child("event");defeat["name"]="defeat";
    defeat.add_child("endlevel",config{"result","defeat","carryover_report",false,"linger_mode",false,"bonus",false});
}
extern "C" int __wrap_main(int argc,char** argv) {
    if(argc==3&&std::string(argv[1])=="--shared-guards") {
        alliance_game::SharedAdmission base;base.profile=alliance::account::read_public_profile(argv[2]);base.profile.port=15002;
        base.run_id="11111111-1111-4111-8111-111111111111";base.account_id="22222222-2222-4222-8222-222222222222";base.token=std::string(43,'A');
        int count=0;auto reject=[&](alliance_game::SharedAdmission a){try{alliance_game::queue_shared(std::move(a));}catch(const std::invalid_argument&){if(alliance_game::shared_pending())throw std::runtime_error("Invalid admission queued.");++count;return;}throw std::runtime_error("Invalid admission accepted.");};
        auto a=base;a.run_id="../bad";reject(a);a=base;a.account_id="bad";reject(a);a=base;a.token="short";reject(a);a=base;a.profile.port=15000;reject(a);a=base;a.upload_only=a.download_only=true;reject(a);
        a=base;a.after_turn=1;reject(a);a=base;a.captain_action="arbitrary";a.after_turn=1;reject(a);a=base;a.captain_action="surrender";reject(a);a.after_turn=101;reject(a);a.after_turn=1;a.computer_controlled=true;reject(a);a.computer_controlled=false;a.upload_only=true;reject(a);a.upload_only=false;a.download_only=true;reject(a);
        for(const auto& action:{"surrender","offer_draw","withdraw_draw"}){a=base;a.captain_action=action;a.after_turn=1;alliance_game::queue_shared(a);auto queued=alliance_game::take_pending_shared();if(!queued||queued->captain_action!=action||queued->after_turn!=1||alliance_game::shared_pending())throw std::runtime_error("Captain admission differs.");++count;}
        std::cout<<"PASS "<<count<<" shared admission guards without sockets"<<std::endl;return 0;
    }
    if(argc<6||std::string(argv[1])!="--shared-seat")return __real_main(argc,argv);
    try{
        std::ifstream input(argv[2]);std::ostringstream data;data<<input.rdbuf();
        const auto fixture=boost::json::parse(data.str()).as_object();
        const int index=std::stoi(argv[3]);const auto& actor=fixture.at("players").as_array().at(index).as_object();
        alliance_game::SharedAdmission a;
        a.profile=alliance::account::read_public_profile(std::getenv("WESNOTH_ALLIANCE_PROFILE"));a.profile.port=15002;
        a.run_id=std::string(fixture.at("run_id").as_string());a.account_id=std::string(actor.at("account_id").as_string());a.token=std::string(actor.at("token").as_string());a.computer_controlled=std::getenv("ALLIANCE_SHARED_FIXTURE_HUMAN")==nullptr;
        const char* mode=std::getenv("ALLIANCE_SHARED_FIXTURE_MODE");
        a.upload_only=mode&&std::string(mode)=="upload";a.download_only=mode&&std::string(mode)=="download";
        if(const char* action=std::getenv("ALLIANCE_SHARED_FIXTURE_CAPTAIN")){a.captain_action=action;a.after_turn=1;a.computer_controlled=false;}
        alliance_game::queue_shared(std::move(a));
        return __real_main(argc-4,argv+4);
    }catch(...){std::cerr<<"SHARED FIXTURE SETUP FAILED\n";return 1;}
}
