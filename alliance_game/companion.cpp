// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/companion.hpp"
#include "alliance_game/individual_companion.hpp"
#include "alliance_game/shared_launch.hpp"
#include "alliance_game/shared_result.hpp"
#include "alliance_native/account_client.hpp"
#include "alliance_native/account_protocol.hpp"
#include "alliance_native/progression_protocol.hpp"
#include "video.hpp"
#include <openssl/crypto.h>
#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>

namespace alliance_game {
bool companion_requested() noexcept { return individual_companion_requested() || std::getenv("WESNOTH_ALLIANCE_COMPANION_PROFILE") != nullptr; }
int run_companion() noexcept {
    if(individual_companion_requested()) return run_individual_companion();
    // The launcher keeps passwords out of the process command line and sends
    // only a short-lived account token over this process's private stdin pipe.
    std::string input;
    struct Cleanup { std::string& s; ~Cleanup(){if(!s.empty())OPENSSL_cleanse(s.data(),s.size());} } cleanup{input};
    try {
        const char* path=std::getenv("WESNOTH_ALLIANCE_COMPANION_PROFILE");
        if(!video::headless() || !path || !*path) throw std::runtime_error("Companion mode requires the explicit headless launcher.");
        for(char ch;std::cin.get(ch);) {
            if(ch=='\n')break;
            if(input.size()>=4096)throw std::runtime_error("Companion admission is too long.");
            input+=ch;
        }
        namespace protocol=alliance::progression_protocol;
        auto message=alliance::account::parse_document(input);
        const bool captain_policy=message.contains("captain_action");
        if(captain_policy) protocol::keys(message,{"run_id","account_id","token","intent","captain_action","after_turn"});
        else protocol::keys(message,{"run_id","account_id","token","intent"});
        SharedAdmission admission;
        admission.profile=alliance::account::read_public_profile(path);
        admission.profile.port=15002;
        admission.run_id=protocol::text(message,"run_id",36);
        admission.account_id=protocol::text(message,"account_id",36);
        admission.token=protocol::text(message,"token",43);
        const auto intent=protocol::text(message,"intent",12);
        if(intent!="play"&&intent!="result_retry")throw std::runtime_error("Unsupported companion intent.");
        admission.upload_only=intent=="result_retry";
        admission.computer_controlled=!captain_policy;
        if(captain_policy) {
            if(admission.upload_only)throw std::runtime_error("A result retry cannot control a captain.");
            admission.captain_action=protocol::text(message,"captain_action",16);
            if(admission.captain_action!="surrender"&&admission.captain_action!="offer_draw"&&admission.captain_action!="withdraw_draw")
                throw std::runtime_error("Unsupported captain policy.");
            admission.after_turn=protocol::number(message,"after_turn",1,100);
        }
        const auto account=admission.account_id,run=admission.run_id;
        auto& token=message.at("token").as_string();
        if(!token.empty())OPENSSL_cleanse(token.data(),token.size());
        if(!input.empty())OPENSSL_cleanse(input.data(),input.size());
        input.clear();
        queue_shared(std::move(admission));
        std::cout<<(captain_policy
            ? "ALLIANCE COMPANION STARTING: authenticated turn-passing captain policy"
            : "ALLIANCE COMPANION STARTING: authenticated computer-controlled fixed seat")<<std::endl;
        run_pending_shared();
        const auto records=inspect_pending_shared(account);
        const auto found=std::find_if(records.begin(),records.end(),[&](const auto& r){return r.run_id==run;});
        if(found==records.end())throw std::runtime_error("No completed battle was recorded.");
        (void)verified_shared_replay(account,run);
        std::cout<<"ALLIANCE COMPANION FINISHED: replay verified; status="<<found->status<<std::endl;
        return found->status=="acknowledged"?0:2;
    } catch(...) {
        // Never print exception payloads, credential-bearing JSON or input.
        std::cerr<<"ALLIANCE COMPANION STOPPED: battle did not finish with a verified acknowledged result."<<std::endl;
        return 1;
    }
}
}
