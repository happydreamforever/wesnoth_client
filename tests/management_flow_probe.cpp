// SPDX-License-Identifier: GPL-2.0-or-later
// Disposable native integration only. Credentials arrive through stdin, never argv.
#include "account_client.hpp"
#include "account_protocol.hpp"
#include <algorithm>
#include <iostream>
#include <set>
#include <stdexcept>

int main(int argc, char** argv)
{
    using namespace alliance::account;
    const char* stage = "input";
    try {
        auto need = [](bool condition) { if(!condition) throw std::runtime_error("fixture"); };
        need(argc == 2);
        const auto trusted = read_public_profile(argv[1]);
        std::string raw;
        for(char value; std::cin.get(value);) {
            need(raw.size() < 8192);
            raw += value;
        }
        const auto input = parse_document(raw);
        auto text = [&](const char* key) { return std::string(input.at(key).as_string()); };
        const auto mode = text("mode");
        need(mode == "flow" || mode == "verify");
        const auto captain_name = text("captain_username"), applicant_name = text("applicant_username");
        const auto extra_name = text("extra_username"), password = text("password"), team_name = text("team_name");
        need(team_name.size() <= 32);
        alliance::CancellationToken cancellation;
        auto perform = [&](const Request& request) { return perform_request(trusted, request, cancellation); };
        auto call = [&](const std::string& token, const std::string& operation,
                        const std::string& team_id = "", const std::string& account_id = "") {
            Request request;
            request.operation = operation; request.token = token;
            request.team_id = team_id; request.account_id = account_id;
            return perform(request);
        };
        auto login = [&](const std::string& username) {
            Request request;
            request.operation = "login"; request.username = username; request.password = password;
            auto result = perform(request);
            need(result.ok && token_valid(result.token));
            return result;
        };
        auto ack = [&](const Reply& result, const std::string& team_id,
                       const std::string& account_id, const char* status) {
            need(result.ok && result.team_id == team_id && result.account_id == account_id && result.status == status);
        };
        auto unchanged = [&](const Reply& actor) {
            const auto result = call(actor.token, "me");
            need(result.ok && result.profile.account_id == actor.profile.account_id &&
                 result.profile.heroes.empty());
        };
        auto standings = [&](const std::string& token) {
            Request request;
            request.operation = "standings"; request.token = token; request.area_id = "area-1";
            auto result = perform(request);
            need(result.ok && result.teams.size() <= 20 && result.next.empty());
            return result;
        };
        if(mode == "flow") {
            stage = "register-players";
            for(const auto& username : {captain_name, applicant_name, extra_name}) {
                Request request;
                request.operation = "register"; request.username = username;
                request.password = password;
                need(perform(request).ok);
            }
        }
        stage = "login-players";
        const auto captain = login(captain_name), applicant = login(applicant_name), extra = login(extra_name);
        const auto captain_id = captain.profile.account_id, applicant_id = applicant.profile.account_id;
        std::string team_id;
        if(mode == "flow") {
            auto create = [&](const Reply& actor, const std::string& name) {
                Request request;
                request.operation = "create_team"; request.token = actor.token;
                request.name = name; request.area_id = "area-1";
                auto result = perform(request);
                need(result.ok && result.team && result.team->captain_id == actor.profile.account_id &&
                     result.team->members.size() == 1);
                return result.team->id;
            };
            stage = "create-main-and-scratch";
            team_id = create(captain, team_name);
            const auto scratch_id = create(extra, team_name + " Scratch");
            stage = "archive-with-pending-application";
            ack(call(applicant.token, "apply_team", team_id), team_id, applicant_id, "applied");
            ack(call(applicant.token, "apply_team", scratch_id), scratch_id, applicant_id, "applied");
            Request leave;
            leave.operation = "leave_team"; leave.token = extra.token; leave.team_id = scratch_id;
            const auto no_consent = perform(leave);
            need(!no_consent.ok && no_consent.error_code == "conflict");
            leave.allow_archive = true;
            const auto archived = perform(leave);
            ack(archived, scratch_id, extra.profile.account_id, "left");
            need(archived.archived);
            const auto detail = call(extra.token, "team_detail", scratch_id);
            need(!detail.ok && detail.error_code == "not_found");
            const auto pending = call(applicant.token, "my_applications");
            need(pending.ok && pending.teams.size() == 1 && pending.teams.front().id == team_id);
            for(const auto& entry : standings(extra.token).teams) need(entry.id != scratch_id);
            std::cout << "PASS sole-captain leave archives the scratch team and clears its application queue\n";
            stage = "approve-main-and-read-history";
            ack(call(captain.token, "approve_application", team_id, applicant_id), team_id, applicant_id, "approved");
            const auto history = call(applicant.token, "application_decisions");
            need(history.ok && history.decisions.size() == 1 && history.decisions.front().unread);
            Request read;
            read.operation = "ack_decision"; read.token = applicant.token;
            read.decision_id = history.decisions.front().id;
            need(perform(read).ok);
            stage = "management-permissions";
            const auto forbidden = call(applicant.token, "transfer_captain", team_id, captain_id);
            need(!forbidden.ok && forbidden.error_code == "forbidden");
            const auto blocked = call(captain.token, "leave_team", team_id);
            need(!blocked.ok && blocked.error_code == "captain_transfer_required");
            unchanged(captain); unchanged(applicant);
            std::cout << "PASS member cannot transfer captain and captain must transfer before leaving other members\n";
            stage = "transfer-and-leave";
            ack(call(captain.token, "transfer_captain", team_id, applicant_id), team_id, applicant_id, "transferred");
            const auto left = call(captain.token, "leave_team", team_id);
            ack(left, team_id, captain_id, "left");
            need(!left.archived);
            const auto none = call(captain.token, "my_team");
            need(none.ok && !none.team);
            const auto remaining = call(applicant.token, "my_team");
            need(remaining.ok && remaining.team && remaining.team->captain_id == applicant_id &&
                 remaining.team->members.size() == 1);
            stage = "restore-original-two-member-team";
            ack(call(captain.token, "apply_team", team_id), team_id, captain_id, "applied");
            ack(call(applicant.token, "approve_application", team_id, captain_id), team_id, captain_id, "approved");
            ack(call(applicant.token, "transfer_captain", team_id, captain_id), team_id, captain_id, "transferred");
            std::cout << "PASS transferred captain survives member leave and can approve rejoin then transfer back\n";
        }
        stage = "final-rosters-and-personal-leaders";
        for(const auto* actor : {&captain, &applicant}) {
            const auto mine = call(actor->token, "my_team");
            need(mine.ok && mine.team);
            const auto& team = *mine.team;
            if(team_id.empty()) team_id = team.id;
            need(team.id == team_id && team.name == team_name && team.area_id == "area-1" &&
                 team.captain_id == captain_id && team.members.size() == 2 && team.capacity == 8 &&
                 team.level == 1 && team.experience == 0 && team.rating == 1000);
            std::set<std::string> ids;
            for(const auto& member : team.members) {
                need(member.level == 1 && member.unit_type == "Spearman");
                need(member.username == (member.id == captain_id ? captain_name : applicant_name));
                need(ids.insert(member.id).second);
            }
            need(ids == std::set<std::string>{captain_id, applicant_id});
            unchanged(*actor);
            const auto pending = call(actor->token, "my_applications");
            need(pending.ok && pending.teams.empty());
        }
        const auto queue = call(captain.token, "team_applicants", team_id);
        need(queue.ok && queue.applicants.empty());
        const auto extra_team = call(extra.token, "my_team");
        need(extra_team.ok && !extra_team.team);
        unchanged(extra);
        stage = "preserved-decision-history";
        for(const auto* actor : {&captain, &applicant}) {
            const auto history = call(actor->token, "application_decisions");
            need(history.ok && history.decisions.size() == 1);
            const auto& decision = history.decisions.front();
            need(decision.team_id == team_id && decision.team_name == team_name && decision.status == "approved" &&
                 decision.unread == (actor == &captain));
        }
        const auto extra_history = call(extra.token, "application_decisions");
        need(extra_history.ok && extra_history.decisions.empty());
        std::cout << "PASS both players retain original captain, two-member roster, zero XP and independent read history\n";
        stage = "area-ranking-and-detail";
        const auto ranking = standings(captain.token);
        const auto found = std::find_if(ranking.teams.begin(), ranking.teams.end(),
            [&](const auto& entry) { return entry.id == team_id; });
        need(found != ranking.teams.end() && found->captain_id == captain_id && found->member_count == 2 &&
             found->level == 1 && found->experience == 0 && found->rating == 1000);
        const auto detail = call(applicant.token, "team_detail", team_id);
        need(detail.ok && detail.team && detail.team->id == found->id);
        std::cout << "PASS bounded area rankings match the current team and separate public detail\n";
        stage = "logout";
        for(const auto* actor : {&captain, &applicant, &extra}) {
            need(call(actor->token, "logout").ok);
            const auto denied = call(actor->token, "leave_team", team_id);
            need(!denied.ok && denied.error_code == "invalid_session");
        }
        std::cout << "ALLIANCE MANAGEMENT FLOW CHECK PASSED\n";
        return 0;
    } catch(...) {
        std::cerr << "ERROR management flow fixture at " << stage << '\n';
        return 1;
    }
}
