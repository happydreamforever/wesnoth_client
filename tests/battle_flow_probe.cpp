// SPDX-License-Identifier: GPL-2.0-or-later
// Disposable native integration. Credentials enter through stdin, never argv.
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
        auto need = [](bool value) { if(!value) throw std::runtime_error("fixture"); };
        need(argc == 2);
        const auto trusted = read_public_profile(argv[1]);
        std::string raw;
        for(char c; std::cin.get(c);) { need(raw.size() < 8192); raw += c; }
        const auto data = parse_document(raw);
        auto text = [&](const char* key) { return std::string(data.at(key).as_string()); };
        const auto mode = text("mode"), password = text("password");
        need(mode == "flow" || mode == "verify" || mode == "cancel");
        const std::vector<std::string> names{text("captain_username"), text("member_username"),
            text("opponent_username"), text("opponent_member_username")};
        const std::vector<std::string> team_names{text("team_name"), text("opponent_team_name")};
        alliance::CancellationToken cancellation;
        auto perform = [&](Request input) { return perform_request(trusted, input, cancellation); };
        std::vector<Reply> actors;
        if(mode == "flow") {
            stage = "register-four-players";
            for(const auto& username : names) {
                Request input;
                input.operation = "register"; input.username = username; input.password = password;
                need(perform(input).ok);
            }
        }
        stage = "login-four-players";
        for(const auto& username : names) {
            Request input;
            input.operation = "login"; input.username = username; input.password = password;
            auto reply = perform(input);
            need(reply.ok && token_valid(reply.token));
            actors.push_back(std::move(reply));
        }
        auto call = [&](int actor, const std::string& operation, const std::string& battle_id = "", bool ready = false) {
            Request input;
            input.operation = operation; input.token = actors[actor].token; input.battle_id = battle_id; input.ready = ready;
            return perform(input);
        };
        std::vector<std::string> team_ids;
        if(mode == "flow") {
            stage = "create-two-teams";
            for(int i = 0; i < 2; ++i) {
                Request input;
                input.operation = "create_team"; input.token = actors[i * 2].token;
                input.name = team_names[i]; input.area_id = "area-1";
                const auto reply = perform(input);
                need(reply.ok && reply.team && reply.team->members.size() == 1);
                team_ids.push_back(reply.team->id);
                input = {}; input.operation = "apply_team"; input.token = actors[i * 2 + 1].token; input.team_id = team_ids[i];
                need(perform(input).ok);
                input.operation = "approve_application"; input.token = actors[i * 2].token;
                input.account_id = actors[i * 2 + 1].profile.account_id;
                need(perform(input).ok);
            }
        } else {
            stage = "recover-two-teams";
            for(int i : {0, 2}) {
                const auto mine = call(i, "my_team");
                need(mine.ok && mine.team && mine.team->name == team_names[i / 2]);
                team_ids.push_back(mine.team->id);
            }
        }
        stage = "map-catalog";
        Request maps;
        maps.operation = "battle_maps"; maps.token = actors[0].token; maps.area_id = "area-1";
        const auto catalog = perform(maps);
        need(catalog.ok && catalog.maps.size() == 3);
        std::set<std::string> map_ids;
        for(const auto& map : catalog.maps) map_ids.insert(map.id);
        need(map_ids == std::set<std::string>{"isars-cross", "hexcake", "mokena-prairie"});
        std::string battle_id;
        if(mode == "flow") {
            Request proposal;
            proposal.operation = "propose_battle"; proposal.token = actors[0].token;
            proposal.team_id = team_ids[0]; proposal.opponent_team_id = team_ids[1]; proposal.map_id = "isars-cross";
            proposal.lineups = {{team_ids[0], {actors[0].profile.account_id, actors[1].profile.account_id}},
                                {team_ids[1], {actors[2].profile.account_id, actors[3].profile.account_id}}};
            stage = "member-proposal-denied";
            auto member_proposal = proposal; member_proposal.token = actors[1].token;
            const auto denied = perform(member_proposal);
            need(!denied.ok && denied.error_code == "forbidden");
            stage = "save-unapproved-proposal";
            const auto created = perform(proposal);
            need(created.ok && created.battle && created.battle->state == "draft");
            battle_id = created.battle->id;
            for(const auto& team : created.battle->teams) {
                need(!team.approved);
                for(const auto& player : team.players) need(!player.ready);
            }
            const auto duplicate = perform(proposal);
            need(!duplicate.ok && duplicate.error_code == "planning_exists");
            std::cout << "PASS fixed 2v2 proposal has no implicit approval or readiness; duplicate drafts are blocked\n";
            stage = "approval-and-readiness-permissions";
            const auto early = call(0, "battle_ready", battle_id, true);
            need(!early.ok && early.error_code == "approval_required");
            for(const auto* operation : {"approve_battle", "cancel_battle"}) {
                const auto denied_action = call(1, operation, battle_id);
                need(!denied_action.ok && denied_action.error_code == "forbidden");
            }
            const auto first = call(0, "approve_battle", battle_id);
            need(first.ok && first.battle && first.battle->teams[0].approved && !first.battle->teams[1].approved);
            const auto second = call(2, "approve_battle", battle_id);
            need(second.ok && second.battle && second.battle->teams[0].approved && second.battle->teams[1].approved);
            for(int i = 0; i < 4; ++i) need(call(i, "battle_ready", battle_id, true).ok);
            const auto unready = call(1, "battle_ready", battle_id, false);
            need(unready.ok && unready.battle && !unready.battle->teams[0].players[1].ready);
            need(call(1, "battle_ready", battle_id, true).ok);
            std::cout << "PASS both captains explicitly approve and each selected player controls only their readiness\n";
        }
        stage = "recover-bounded-plan-list";
        for(int actor = 0; actor < 4; ++actor) {
            Request request;
            request.operation = "my_battles"; request.token = actors[actor].token;
            bool found = false;
            for(int page = 0; ; ++page) {
                need(page < 10);
                const auto list = perform(request);
                need(list.ok && list.battles.size() <= 20);
                for(const auto& summary : list.battles) {
                    if(summary.teams[0].id != team_ids[0] || summary.teams[1].id != team_ids[1]) continue;
                    need(!found && summary.map_id == "isars-cross" && summary.area_id == "area-1");
                    if(battle_id.empty()) battle_id = summary.id;
                    need(summary.id == battle_id && summary.state == "draft");
                    found = true;
                }
                if(list.next.empty()) break;
                request.after = list.next;
            }
            need(found);
        }
        if(mode == "cancel") {
            stage = "captain-cancellation";
            for(int repeat = 0; repeat < 2; ++repeat) {
                const auto reply = call(0, "cancel_battle", battle_id);
                need(reply.ok && reply.battle && reply.battle->state == "cancelled");
            }
            for(const auto* operation : {"approve_battle", "battle_ready"}) {
                const auto reply = call(0, operation, battle_id, std::string(operation) == "battle_ready");
                need(!reply.ok && reply.error_code == "invalid_state");
            }
            const auto withdrawal = call(1, "battle_ready", battle_id, false);
            need(!withdrawal.ok && withdrawal.error_code == "invalid_state");
            std::cout << "PASS current captain cancels draft idempotently and cancelled plans reject approval and readiness\n";
        }
        stage = "fixed-detail-and-unchanged-progression";
        for(int actor = 0; actor < 4; ++actor) {
            const auto loaded = call(actor, "battle_detail", battle_id);
            need(loaded.ok && loaded.battle);
            const auto& battle = *loaded.battle;
            need(battle.id == battle_id && battle.map.id == "isars-cross" && battle.area_id == "area-1" &&
                 battle.state == (mode == "cancel" ? "cancelled" : "draft") && battle.validity == "valid");
            for(int i = 0; i < 2; ++i) {
                const auto& team = battle.teams[i];
                need(team.id == team_ids[i] && team.name == team_names[i] && team.captain_id == actors[i * 2].profile.account_id && team.approved);
                for(int j = 0; j < 2; ++j) {
                    const auto& player = team.players[j];
                    need(player.id == actors[i * 2 + j].profile.account_id && player.username == names[i * 2 + j] &&
                         player.unit_type == "Spearman" && player.level == 1 && player.ready);
                }
            }
            const auto mine = call(actor, "my_team");
            need(mine.ok && mine.team && mine.team->id == team_ids[actor / 2] && mine.team->members.size() == 2 &&
                 mine.team->captain_id == actors[(actor / 2) * 2].profile.account_id && mine.team->level == 1 &&
                 mine.team->experience == 0 && mine.team->rating == 1000);
            const auto me = call(actor, "me");
            need(me.ok && me.profile.account_id == actors[actor].profile.account_id && me.profile.heroes.empty());
            const auto list = call(actor, "my_battles");
            need(list.ok && list.battles.size() == 1 && list.battles.front().id == battle_id &&
                 list.battles.front().state == battle.state);
        }
        std::cout << "PASS all four players retrieve fixed lineups, approvals and readiness with unchanged teams and zero XP\n";
        stage = "logout";
        for(int actor = 0; actor < 4; ++actor) {
            need(call(actor, "logout").ok);
            const auto denied = call(actor, "my_battles");
            need(!denied.ok && denied.error_code == "invalid_session");
        }
        std::cout << "ALLIANCE BATTLE PLANNING FLOW CHECK PASSED\n";
        return 0;
    } catch(...) {
        std::cerr << "ERROR battle planning flow fixture at " << stage << '\n';
        return 1;
    }
}
