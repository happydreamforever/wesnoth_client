// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "transport.hpp"
#include <exception>
#include <string>
#include <optional>
#include <vector>
#include <cstdint>

namespace alliance::account {
struct Lineup { std::string team_id; std::vector<std::string> account_ids; };
struct Request {
    std::string operation, username, password, starter_id, token;
    std::string name, area_id, team_id, account_id, after, decision_id;
    bool allow_archive = false;
    std::string battle_id, opponent_team_id, map_id;
    std::string hero_id, form_id, game_id;
    int side = 0;
    std::vector<Lineup> lineups;
    bool ready = false;
    // Closed progression extension; validated before opening any socket.
    std::string progression_json;
};
struct Hero {
    std::string hero_id, name;
    std::int64_t hero_level = 1, experience = 0, total_experience = 0, revision = 0;
    std::vector<std::string> forms;
};
struct Profile {
    std::string account_id, username, database_generation;
    std::vector<Hero> heroes;
};
struct Area { std::string id, name; };
struct TeamSummary {
    std::string id, name, area_id, captain_id;
    int level = 0, experience = 0, rating = 0, member_count = 0;
};
struct Member {
    std::string id, username;
    int hero_count = 0;
};
struct Team {
    std::string id, name, area_id, area_name, captain_id;
    int level = 0, experience = 0, rating = 0, capacity = 0;
    std::vector<Member> members;
};
struct Decision {
    std::string id, team_id, team_name, status, created_at;
    bool unread = false;
};
struct BattleMap {
    std::string id, name, scenario_id;
    int size = 0;
    std::vector<std::vector<int>> sides;
};
struct BattleTeamName { std::string id, name; };
struct BattleSummary {
    std::string id, area_id, map_id, state, created_at;
    std::vector<BattleTeamName> teams;
};
struct BattlePlayer {
    std::string id, username, unit_type;
    int level = 0, side = 0; // Native unit tier, never permanent hero level.
    bool ready = false;
    std::string hero_id, form_id;
    std::int64_t hero_level = 0, hero_revision = 0;
    bool selection_present = false;
};
struct BattleTeam {
    std::string id, name, captain_id;
    bool approved = false;
    std::vector<BattlePlayer> players;
};
struct Battle {
    std::string id, area_id, state, created_at, validity;
    BattleMap map;
    std::vector<BattleTeam> teams;
};
struct Reply {
    std::string progression_json;
    bool ok = false;
    std::string error_code, error_message, token;
    std::string ticket, selection_ticket, username, database_generation, expires_at;
    Profile profile;
    std::vector<Area> areas;
    std::vector<TeamSummary> teams;
    std::optional<Team> team;
    std::string next;
    std::vector<Member> applicants;
    std::string team_id, account_id, status, decision_id;
    std::vector<Decision> decisions;
    bool archived = false, has_more = false;
    std::vector<BattleMap> maps;
    std::vector<BattleSummary> battles;
    std::optional<Battle> battle;
    std::optional<BattlePlayer> selection;
};
bool catalog_id_valid(const std::string&);
bool team_name_valid(const std::string&);
// Same strict public battle projection used by account responses and the shared relay.
Battle parse_shared_battle_descriptor(const std::string&);
// Called only by a worker. One connection/request with an overall 10s deadline;
// never retries a potentially completed registration or team mutation, and never logs credentials.
Reply perform_request(const TrustedProfile&, const Request&, const CancellationToken&);
enum class ProfileReadFailure {
    file_unavailable, file_read, document_invalid, identity_invalid,
    pin_mismatch, certificate_rejected, crypto_failure, internal_failure
};
// Safe to display: the exception stores only a fixed category, never a path,
// profile field, library exception string or request material.
class ProfileReadError final : public std::exception {
public:
    explicit ProfileReadError(ProfileReadFailure failure) noexcept : failure_(failure) {}
    ProfileReadFailure failure() const noexcept { return failure_; }
    const char* what() const noexcept override;
private:
    ProfileReadFailure failure_;
};
// Public profile only; never reads a server key or database configuration.
TrustedProfile read_public_profile(const std::string& path);
}
