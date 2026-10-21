// SPDX-License-Identifier: GPL-2.0-or-later
// Real client entry point for disposable peer tests. Secrets enter through
// stdin; stdout contains only fixed result metadata and boolean shape checks.
#include "account_client.hpp"
#include "account_protocol.hpp"

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>

namespace {
std::string field(const boost::json::object& object, const char* name)
{
    const auto* value = object.if_contains(name);
    if(!value) return {};
    if(!value->is_string()) throw std::runtime_error("input");
    return std::string(value->as_string());
}
std::string safe_error(const std::string& value)
{
    for(const char* allowed : {"", "invalid_input", "invalid_password", "invalid_credentials",
          "invalid_session", "forbidden", "suspended", "conflict", "rate_limited",
          "timeout", "cancelled", "identity", "protocol", "unavailable",
          "profile_rejected", "document_rejected", "team_failed", "already_member", "busy", "not_found", "capacity", "not_requested", "not_member", "captain_transfer_required",
          "planning_exists", "invalid_map", "invalid_lineup", "different_area", "stale_proposal", "approval_required", "invalid_state"}) {
        if(value == allowed) return value;
    }
    return "unrecognized_error";
}
void output(const alliance::account::Reply& reply, long long elapsed_ms)
{
    boost::json::object result{
        {"ok", reply.ok}, {"error_code", safe_error(reply.error_code)},
        {"has_token", !reply.token.empty()},
        {"has_profile", !reply.profile.account_id.empty()},
        {"level", reply.profile.level}, {"elapsed_ms", elapsed_ms},
        {"area_count", reply.areas.size()}, {"team_count", reply.teams.size()},
        {"has_next", !reply.next.empty()}, {"has_team", reply.team.has_value()},
        {"member_count", reply.team ? reply.team->members.size() : 0},
        {"team_level", reply.team ? reply.team->level : 0},
        {"applicant_count", reply.applicants.size()},
        {"has_team_id", !reply.team_id.empty()}, {"has_account_id", !reply.account_id.empty()},
        {"decision_count", reply.decisions.size()},
        {"unread_count", std::count_if(reply.decisions.begin(), reply.decisions.end(),
            [](const auto& decision) { return decision.unread; })},
        {"has_decision_id", !reply.decision_id.empty()},
        {"status", reply.status}, {"archived", reply.archived}, {"has_more", reply.has_more},
        {"map_count", reply.maps.size()}, {"battle_count", reply.battles.size()}, {"has_battle", reply.battle.has_value()},
        {"battle_state", reply.battle ? reply.battle->state : ""},
        {"battle_validity", reply.battle ? reply.battle->validity : ""}};
    std::cout << boost::json::serialize(result) << "\n";
}
}

int main(int argc, char** argv)
{
    using namespace alliance::account;
    try {
        if(argc != 2) throw std::runtime_error("arguments");
        std::string raw;
        for(char c; std::cin.get(c);) {
            if(raw.size() >= 16384) throw std::runtime_error("input");
            raw += c;
        }
        auto input = parse_document(raw);
        if(field(input, "mode") == "parse_document") {
            Reply result;
            try {
                parse_document(field(input, "document"));
                result.ok = true;
            } catch(...) {
                result.error_code = "document_rejected";
            }
            output(result, 0);
            return 0;
        }
        alliance::TrustedProfile profile;
        try {
            profile = read_public_profile(argv[1]);
        } catch(...) {
            Reply result;
            result.error_code = "profile_rejected";
            output(result, 0);
            return 0;
        }
        Request request;
        request.operation = field(input, "operation");
        request.username = field(input, "username");
        request.password = field(input, "password");
        request.starter_id = field(input, "starter_id");
        request.token = field(input, "token");
        request.name = field(input, "name");
        request.area_id = field(input, "area_id");
        request.team_id = field(input, "team_id");
        request.account_id = field(input, "account_id");
        request.after = field(input, "after");
        request.decision_id = field(input, "decision_id");
        request.battle_id = field(input, "battle_id");
        request.opponent_team_id = field(input, "opponent_team_id");
        request.map_id = field(input, "map_id");
        if(const auto* ready = input.if_contains("ready")) {
            if(!ready->is_bool()) throw std::runtime_error("input");
            request.ready = ready->as_bool();
        }
        if(const auto* lineups = input.if_contains("lineups")) {
            if(!lineups->is_array()) throw std::runtime_error("input");
            for(const auto& value : lineups->as_array()) {
                if(!value.is_object()) throw std::runtime_error("input");
                Lineup lineup;
                lineup.team_id = field(value.as_object(), "team_id");
                const auto& ids = value.as_object().at("account_ids").as_array();
                for(const auto& id : ids) lineup.account_ids.emplace_back(id.as_string());
                request.lineups.push_back(std::move(lineup));
            }
        }
        if(const auto* allow_archive = input.if_contains("allow_archive")) {
            if(!allow_archive->is_bool()) throw std::runtime_error("input");
            request.allow_archive = allow_archive->as_bool();
        }
        int cancel_after_ms = 0;
        if(const auto* delay = input.if_contains("cancel_after_ms")) {
            if(!delay->is_int64() || delay->as_int64() < 0 || delay->as_int64() > 5000)
                throw std::runtime_error("cancel");
            cancel_after_ms = static_cast<int>(delay->as_int64());
        }
        alliance::CancellationToken cancel;
        std::mutex mutex;
        std::condition_variable wake;
        bool done = false;
        std::thread timer;
        if(cancel_after_ms != 0) {
            timer = std::thread([&] {
                std::unique_lock<std::mutex> lock(mutex);
                if(!wake.wait_for(lock, std::chrono::milliseconds(cancel_after_ms),
                                 [&] { return done; })) cancel.cancel();
            });
        }
        const auto start = std::chrono::steady_clock::now();
        auto reply = perform_request(profile, request, cancel);
        const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start).count();
        {
            std::lock_guard<std::mutex> lock(mutex);
            done = true;
        }
        wake.notify_all();
        if(timer.joinable()) timer.join();
        output(reply, elapsed);
        return 0;
    } catch(...) {
        std::cerr << "ERROR account response probe fixture failed\n";
        return 1;
    }
}
