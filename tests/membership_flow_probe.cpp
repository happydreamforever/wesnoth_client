// SPDX-License-Identifier: GPL-2.0-or-later
// Disposable native integration only. Credentials arrive through stdin, never argv.
#include "account_client.hpp"
#include "account_protocol.hpp"
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
        need(mode == "flow" || mode == "restart" || mode == "pending");
        const auto captain_name = text("captain_username");
        const auto applicant_name = text("applicant_username");
        const auto password = text("password");
        const auto team_name = text("team_name");
        alliance::CancellationToken cancellation;
        auto call = [&](const std::string& token, const std::string& operation,
                        const std::string& team_id = "", const std::string& account_id = "") {
            Request request;
            request.operation = operation;
            request.token = token;
            request.team_id = team_id;
            request.account_id = account_id;
            return perform_request(trusted, request, cancellation);
        };
        auto login = [&](const std::string& username) {
            Request request;
            request.operation = "login"; request.username = username; request.password = password;
            auto result = perform_request(trusted, request, cancellation);
            need(result.ok && token_valid(result.token));
            return result;
        };
        auto acknowledge_decision = [&](const std::string& token, const std::string& decision_id) {
            Request request;
            request.operation = "ack_decision";
            request.token = token;
            request.decision_id = decision_id;
            return perform_request(trusted, request, cancellation);
        };
        auto unchanged = [&](const Reply& actor) {
            const auto result = call(actor.token, "me");
            need(result.ok && result.profile.account_id == actor.profile.account_id &&
                 result.profile.unit_type == "Spearman" && result.profile.level == 1 &&
                 result.profile.experience == 0 && result.profile.total_experience == 0);
        };
        auto application_ids = [&](const std::string& token, const std::string& team_id) {
            Request request;
            request.operation = team_id.empty() ? "my_applications" : "team_applicants";
            request.token = token; request.team_id = team_id;
            std::set<std::string> ids;
            for(int page = 0; ; ++page) {
                need(page < 10);
                const auto reply = perform_request(trusted, request, cancellation);
                need(reply.ok);
                if(team_id.empty()) {
                    for(const auto& team : reply.teams) need(ids.insert(team.id).second);
                } else {
                    need(reply.team_id == team_id);
                    for(const auto& member : reply.applicants) {
                        need(member.unit_type == "Spearman" && member.level == 1);
                        need(ids.insert(member.id).second);
                    }
                }
                if(reply.next.empty()) break;
                request.after = reply.next;
            }
            return ids;
        };
        auto ack = [&](const Reply& result, const std::string& team_id,
                       const std::string& account_id, const char* status) {
            need(result.ok && result.team_id == team_id && result.account_id == account_id && result.status == status);
        };
        auto logout = [&](const Reply& actor) {
            need(call(actor.token, "logout").ok);
            const auto result = call(actor.token, "my_applications");
            need(!result.ok && result.error_code == "invalid_session");
        };
        if(mode == "flow") {
            stage = "register-players";
            for(const auto& username : {captain_name, applicant_name}) {
                Request request;
                request.operation = "register"; request.username = username;
                request.password = password; request.starter_id = "spearman";
                need(perform_request(trusted, request, cancellation).ok);
            }
        }
        stage = "login-applicant";
        const auto applicant = login(applicant_name);
        const auto applicant_id = applicant.profile.account_id;
        if(mode == "pending") {
            stage = "recover-pending-application";
            need(applicant_id == text("applicant_id"));
            const auto team_id = text("team_id");
            need(application_ids(applicant.token, "").count(team_id) == 1);
            const auto mine = call(applicant.token, "my_team");
            need(mine.ok && !mine.team);
            unchanged(applicant);
            logout(applicant);
            std::cout << "PASS native fresh login recovers the pending application without joining or XP\n";
        } else {
            stage = "login-captain";
            const auto captain = login(captain_name);
            const auto captain_id = captain.profile.account_id;
            std::string team_id;
            if(mode == "flow") {
                stage = "create-team";
                Request request;
                request.operation = "create_team"; request.token = captain.token;
                request.name = team_name; request.area_id = "area-1";
                const auto created = perform_request(trusted, request, cancellation);
                need(created.ok && created.team && created.team->members.size() == 1);
                team_id = created.team->id;
                stage = "apply-and-read";
                ack(call(applicant.token, "apply_team", team_id), team_id, applicant_id, "applied");
                need(application_ids(applicant.token, "").count(team_id) == 1);
                need(application_ids(captain.token, team_id).count(applicant_id) == 1);
                std::cout << "PASS application is visible to its player and current captain\n";
                stage = "captain-permission";
                for(const char* operation : {"team_applicants", "approve_application"}) {
                    const auto denied = call(applicant.token, operation, team_id,
                                             std::string(operation) == "approve_application" ? applicant_id : "");
                    need(!denied.ok && denied.error_code == "forbidden");
                }
                unchanged(applicant);
                std::cout << "PASS applicant cannot read or approve captain-only applications; session still works\n";
                stage = "withdraw";
                ack(call(applicant.token, "withdraw_application", team_id), team_id, applicant_id, "withdrawn");
                need(application_ids(applicant.token, "").count(team_id) == 0);
                need(application_ids(captain.token, team_id).count(applicant_id) == 0);
                const auto no_decisions = call(applicant.token, "application_decisions");
                need(no_decisions.ok && no_decisions.decisions.empty());
                stage = "reject";
                ack(call(applicant.token, "apply_team", team_id), team_id, applicant_id, "applied");
                ack(call(captain.token, "reject_application", team_id, applicant_id), team_id, applicant_id, "rejected");
                need(application_ids(applicant.token, "").count(team_id) == 0);
                need(application_ids(captain.token, team_id).count(applicant_id) == 0);
                auto mine = call(applicant.token, "my_team");
                need(mine.ok && !mine.team);
                std::cout << "PASS withdrawal and captain rejection remove applications without joining\n";
                stage = "unread-rejection";
                auto decisions = call(applicant.token, "application_decisions");
                need(decisions.ok && decisions.decisions.size() == 1);
                const auto rejected_id = decisions.decisions.front().id;
                need(decisions.decisions.front().team_id == team_id &&
                     decisions.decisions.front().team_name == team_name &&
                     decisions.decisions.front().status == "rejected" &&
                     decisions.decisions.front().unread);
                // Fetching and repeating the captain's decision must neither
                // consume the notification nor manufacture another event.
                ack(call(captain.token, "reject_application", team_id, applicant_id), team_id, applicant_id, "rejected");
                decisions = call(applicant.token, "application_decisions");
                need(decisions.ok && decisions.decisions.size() == 1 &&
                     decisions.decisions.front().id == rejected_id && decisions.decisions.front().unread);
                const auto captain_decisions = call(captain.token, "application_decisions");
                need(captain_decisions.ok && captain_decisions.decisions.empty());
                const auto foreign_ack = acknowledge_decision(captain.token, rejected_id);
                need(!foreign_ack.ok && foreign_ack.error_code == "not_found");
                unchanged(captain);
                stage = "relogin-unread-rejection";
                const auto fresh = login(applicant_name);
                decisions = call(fresh.token, "application_decisions");
                need(decisions.ok && decisions.decisions.size() == 1 &&
                     decisions.decisions.front().id == rejected_id && decisions.decisions.front().unread);
                for(int repeat = 0; repeat < 2; ++repeat) {
                    const auto read = acknowledge_decision(fresh.token, rejected_id);
                    need(read.ok && read.decision_id == rejected_id && read.status == "read");
                }
                decisions = call(fresh.token, "application_decisions");
                need(decisions.ok && decisions.decisions.size() == 1 &&
                     decisions.decisions.front().id == rejected_id && !decisions.decisions.front().unread);
                logout(fresh);
                std::cout << "PASS private unread rejection survives login; explicit acknowledgement retains read history\n";
                stage = "approve";
                ack(call(applicant.token, "apply_team", team_id), team_id, applicant_id, "applied");
                ack(call(captain.token, "approve_application", team_id, applicant_id), team_id, applicant_id, "approved");
                const auto duplicate = call(captain.token, "approve_application", team_id, applicant_id);
                need(!duplicate.ok && duplicate.error_code == "not_requested");
                std::cout << "PASS approval joins the player and a repeated approval cannot add a second member\n";
            } else {
                stage = "expected-persistent-identities";
                team_id = text("team_id");
                need(captain_id == text("captain_id") && applicant_id == text("applicant_id"));
            }
            stage = "both-player-rosters";
            for(const auto* actor : {&captain, &applicant}) {
                const auto mine = call(actor->token, "my_team");
                need(mine.ok && mine.team);
                const auto& team = *mine.team;
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
            }
            need(application_ids(applicant.token, "").count(team_id) == 0);
            need(application_ids(captain.token, team_id).count(applicant_id) == 0);
            stage = "persistent-decision-history";
            const auto history = call(applicant.token, "application_decisions");
            need(history.ok && history.decisions.size() == 2);
            const auto& approved = history.decisions[0];
            const auto& rejected = history.decisions[1];
            need(approved.id != rejected.id && approved.team_id == team_id && rejected.team_id == team_id &&
                 approved.team_name == team_name && rejected.team_name == team_name &&
                 approved.status == "approved" && approved.unread &&
                 rejected.status == "rejected" && !rejected.unread);
            std::cout << "PASS approved and rejected decisions retain independent unread state and newest-first history\n";
            std::cout << "PASS both players retrieve the same captain and two-member roster with unchanged XP\n";
            logout(applicant); logout(captain);
            std::cout << "PASS revoked sessions cannot access membership operations\n";
        }
        std::cout << "ALLIANCE MEMBERSHIP FLOW CHECK PASSED\n";
        return 0;
    } catch(...) {
        std::cerr << "ERROR membership flow fixture at " << stage << '\n';
        return 1;
    }
}
