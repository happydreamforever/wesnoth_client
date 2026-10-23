// SPDX-License-Identifier: GPL-2.0-or-later
// Disposable production-client integration; credentials are stdin-only.
#include "account_client.hpp"
#include "account_protocol.hpp"
#include "progression_protocol.hpp"
#include <iostream>
#include <stdexcept>
using namespace alliance::account;
namespace j=boost::json;
int main(int argc,char** argv) {
    std::string stage="input";
    try {
        if(argc!=2) throw std::runtime_error("input");
        auto trusted=read_public_profile(argv[1]);std::string raw;
        for(char c;std::cin.get(c);) {if(raw.size()>=4096) throw std::runtime_error("input");raw+=c;}
        const auto data=parse_document(raw);alliance::CancellationToken cancel;
        Request request;request.operation="register";request.username=std::string(data.at("username").as_string());
        request.password=std::string(data.at("password").as_string());request.starter_id="spearman";
        stage="register";auto created=perform_request(trusted,request,cancel);if(!created.ok) throw std::runtime_error("failed");
        request.operation="login";request.starter_id.clear();stage="login";
        auto logged=perform_request(trusted,request,cancel);if(!logged.ok) throw std::runtime_error("failed");
        auto extension=[&](const std::string& op,j::object body) {
            stage=op;Request call;call.operation=op;call.token=logged.token;call.progression_json=j::serialize(body);
            auto result=perform_request(trusted,call,cancel);if(!result.ok) throw std::runtime_error("failed");
            return parse_document(result.progression_json);
        };
        request={};request.operation="create_team";request.token=logged.token;request.name="Progression Team";request.area_id="area-1";
        stage="create_team";auto team=perform_request(trusted,request,cancel);if(!team.ok||!team.team) throw std::runtime_error("failed");
        extension("coordination_post",{{"team_id",team.team->id},{"text","Guard north. Protect the captain."}});
        auto orders=extension("coordination_orders",{{"team_id",team.team->id}});
        if(orders.at("orders").as_array().size()!=1) throw std::runtime_error("orders");
        for(int i=1;i<=3;++i) {
            auto catalog=extension("progression_catalog",{});
            if(!catalog.at("missions").as_array()[i-1].as_object().at("unlocked").as_bool()) throw std::runtime_error("unlock");
            const auto mission="alliance_training_"+std::to_string(i);
            auto started=extension("progression_start",{{"mission_id",mission}}).at("run").as_object();
            const auto id=std::string(started.at("id").as_string());
            extension("progression_status",{{"run_id",id}});
            j::object proof{{"run_id",id},{"mission_id",mission},{"mission_version","progression-campaign-v1"},
                {"nonce",started.at("nonce")},{"outcome","win"},{"replay_sha256",std::string(64,'a'+i)},
                {"format","alliance-native-campaign-result-v1"}};
            auto done=extension("progression_finish",proof);auto duplicate=extension("progression_finish",proof);
            if(done!=duplicate||done.at("profile").as_object().at("total_experience").as_int64()!=i*20)
                throw std::runtime_error("reward");
        }
        auto advanced=extension("progression_advance",{{"unit_type","Swordsman"}}).at("profile").as_object();
        if(advanced.at("unit_type").as_string()!="Swordsman"||advanced.at("level").as_int64()!=2||advanced.at("experience").as_int64()!=18)
            throw std::runtime_error("advancement");
        auto history=extension("progression_history",{});
        if(history.at("runs").as_array().size()!=3) throw std::runtime_error("history");
        auto final=extension("progression_start",{{"mission_id","alliance_training_1"}}).at("run").as_object();
        extension("progression_cancel",{{"run_id",final.at("id")}});
        request={};request.operation="logout";request.token=logged.token;stage="logout";
        if(!perform_request(trusted,request,cancel).ok) throw std::runtime_error("logout");
        std::cout<<"ALLIANCE PROGRESSION FLOW CHECK PASSED\n";return 0;
    } catch(...) {std::cerr<<"ERROR progression flow fixture stage="<<stage<<"\n";return 1;}
}
