// SPDX-License-Identifier: GPL-2.0-or-later
// Actual client codec/TLS boundary; disposable fixture credentials only via stdin.
#include "account_client.hpp"
#include "account_protocol.hpp"
#include <iostream>
#include <stdexcept>

int main(int argc, char** argv) {
    using namespace alliance::account;
    const char* phase="input";
    try {
        if(argc!=2) throw std::runtime_error("input");
        auto profile=read_public_profile(argv[1]);
        std::string raw;
        for(char c;std::cin.get(c);){if(raw.size()>=4096)throw std::runtime_error("input");raw+=c;}
        auto fixture=parse_document(raw); raw.clear();
        alliance::CancellationToken cancel;
        Request request;request.operation="register";
        request.username=std::string(fixture.at("username").as_string());
        request.password=std::string(fixture.at("password").as_string());
        phase="register";if(!perform_request(profile,request,cancel).ok)throw std::runtime_error("stage");
        request.operation="login";phase="login";auto login=perform_request(profile,request,cancel);
        if(!login.ok||!token_valid(login.token))throw std::runtime_error("stage");
        request={};request.operation="court_query";request.token=login.token;
        request.world_json=R"({"version":1,"query":"snapshot","payload":{}})";
        phase="snapshot";auto snapshot=perform_request(profile,request,cancel);
        if(!snapshot.ok)throw std::runtime_error("stage");
        auto doc=parse_document(snapshot.world_json);
        if(std::string(doc.at("actor_id").as_string())!=login.profile.account_id||doc.at("data").as_object().at("areas").as_array().size()!=128)
            throw std::runtime_error("stage");
        request.world_json=R"({"version":1,"query":"shop","payload":{}})";
        phase="shop";auto shop=perform_request(profile,request,cancel);
        if(!shop.ok||parse_document(shop.world_json).at("data").as_object().at("heroes").as_array().size()!=24)
            throw std::runtime_error("stage");
        request.operation="court_command";
        request.world_json=R"({"version":1,"operation":"request_cash","payload":{"command_id":"f7e94a50-245d-418c-a780-04e98b1ef8c6","kind":"personal","package":100}})";
        phase="cash_request";auto first=perform_request(profile,request,cancel);
        if(!first.ok)throw std::runtime_error("stage");
        phase="exact_retry";auto retry=perform_request(profile,request,cancel);
        if(!retry.ok||parse_document(first.world_json)!=parse_document(retry.world_json))throw std::runtime_error("stage");
        request.operation="court_query";
        request.world_json=R"({"version":1,"query":"area","payload":{"area_id":"A001"}})";
        phase="membership_error";auto unavailable=perform_request(profile,request,cancel);
        if(unavailable.ok||unavailable.error_code!="team_required")throw std::runtime_error("stage");
        request={};request.operation="logout";request.token=login.token;
        phase="logout";if(!perform_request(profile,request,cancel).ok)throw std::runtime_error("stage");
        std::cout<<"COURT NATIVE CLIENT FLOW PASSED\n";return 0;
    } catch(...) {std::cerr<<"Court client fixture failed at "<<phase<<"\n";return 1;}
}
