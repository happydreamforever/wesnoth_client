// SPDX-License-Identifier: GPL-2.0-or-later
// Disposable native integration. Credentials enter through stdin, never argv.
#include "account_client.hpp"
#include "account_protocol.hpp"
#include <chrono>
#include <iostream>
#include <set>
#include <stdexcept>

int main(int argc, char** argv)
{
    using namespace alliance::account;
    const char* stage = "input";
    try {
        if(argc != 2) throw std::runtime_error("args");
        const auto trusted = read_public_profile(argv[1]);
        std::string raw;
        for(char c; std::cin.get(c);) {
            if(raw.size() >= 4096) throw std::runtime_error("stdin");
            raw += c;
        }
        const auto input = parse_document(raw);
        const std::string username(input.at("username").as_string());
        const std::string password(input.at("password").as_string());
        const std::string name(input.at("team_name").as_string());
        const bool restart = input.at("restart").as_bool();
        alliance::CancellationToken cancel;
        auto need = [](bool good) { if(!good) throw std::runtime_error("check"); };
        Request request;
        if(!restart) {
            stage = "register";
            request.operation = "register"; request.username = username;
            request.password = password; request.starter_id = "spearman";
            need(perform_request(trusted, request, cancel).ok);
        }
        stage = "login";
        request = {}; request.operation = "login"; request.username = username;
        request.password = password;
        const auto login = perform_request(trusted, request, cancel);
        need(login.ok && token_valid(login.token));
        const auto player_id = login.profile.account_id;
        auto call = [&](Request value) {
            value.token = login.token;
            return perform_request(trusted, value, cancel);
        };
        auto check_team = [&](const Reply& reply) {
            need(reply.ok && reply.team.has_value());
            const auto& team = *reply.team;
            need(team.name == name && team.area_id == "area-1" && team.area_name == "Area 1"
                 && team.captain_id == player_id && team.level == 1 && team.experience == 0
                 && team.rating == 1000 && team.capacity == 8 && team.members.size() == 1);
            const auto& member = team.members.front();
            need(member.id == player_id && member.username == username
                 && member.unit_type == "Spearman" && member.level == 1);
        };
        if(restart) {
            stage = "restart-profile";
            need(player_id == std::string(input.at("account_id").as_string()));
            request = {}; request.operation = "my_team";
            const auto mine = call(request);
            check_team(mine);
            need(mine.team->id == std::string(input.at("team_id").as_string()));
            std::cout << "PASS native fresh login and same saved team\n";
        } else {
            stage = "area-pages";
            request = {}; request.operation = "areas";
            std::set<std::string> areas;
            for(int page = 0; ; ++page) {
                need(page < 10);
                const auto result = call(request);
                need(result.ok);
                for(const auto& area : result.areas) need(areas.insert(area.id).second);
                if(result.next.empty()) break;
                request.after = result.next;
            }
            need(areas.count("area-1") == 1 && areas.size() >= 24);
            std::cout << "PASS bounded area pages through the real native bridge\n";
            stage = "no-team";
            request = {}; request.operation = "my_team";
            auto mine = call(request);
            need(mine.ok && !mine.team);
            stage = "create-team";
            request = {}; request.operation = "create_team";
            request.name = name; request.area_id = "area-1";
            const auto created = call(request);
            check_team(created);
            const auto team_id = created.team->id;
            std::cout << "PASS team creation makes the player captain with one of eight seats\n";
            stage = "my-team";
            request = {}; request.operation = "my_team";
            mine = call(request); check_team(mine); need(mine.team->id == team_id);
            stage = "team-detail";
            request = {}; request.operation = "team_detail"; request.team_id = team_id;
            check_team(call(request));
            stage = "duplicate-membership";
            request = {}; request.operation = "create_team";
            request.name = name + " Again"; request.area_id = "area-1";
            const auto duplicate = call(request);
            need(!duplicate.ok && duplicate.error_code == "already_member");
            std::cout << "PASS own team, detail and one-team membership rule\n";
            stage = "team-pages";
            request = {}; request.operation = "teams"; request.area_id = "area-1";
            std::set<std::string> teams;
            for(int page = 0; ; ++page) {
                need(page < 10);
                const auto result = call(request); need(result.ok);
                for(const auto& team : result.teams) need(teams.insert(team.id).second);
                if(result.next.empty()) break;
                request.after = result.next;
            }
            need(teams.size() >= 24 && teams.count(team_id) == 1);
            std::cout << "PASS bounded team pages include the created team exactly once\n";
        }
        stage = "unchanged-leader";
        request = {}; request.operation = "me";
        const auto me = call(request);
        need(me.ok && me.profile.account_id == player_id && me.profile.unit_type == "Spearman"
             && me.profile.level == 1 && me.profile.experience == 0 && me.profile.total_experience == 0);
        stage = "logout";
        request.operation = "logout"; need(call(request).ok);
        request.operation = "my_team";
        const auto revoked = call(request);
        need(!revoked.ok && revoked.error_code == "invalid_session");
        std::cout << "PASS team operations preserve leader XP and reject a revoked session\n";
        std::cout << "ALLIANCE TEAM FLOW CHECK PASSED\n";
        return 0;
    } catch(...) {
        // Fixed stage labels only; no arbitrary exceptions, bodies or secrets.
        std::cerr << "ERROR team flow fixture at " << stage << '\n';
        return 1;
    }
}
