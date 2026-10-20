// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "transport.hpp"
#include <string>
#include <optional>
#include <vector>

namespace alliance::account {
struct Request {
    std::string operation, username, password, starter_id, token;
    std::string name, area_id, team_id, after;
};
struct Profile {
    std::string account_id, username, unit_type, starter_id;
    int level = 0, experience = 0, total_experience = 0;
};
struct Area { std::string id, name; };
struct TeamSummary {
    std::string id, name, area_id, captain_id;
    int level = 0, experience = 0, rating = 0, member_count = 0;
};
struct Member {
    std::string id, username, unit_type;
    int level = 0;
};
struct Team {
    std::string id, name, area_id, area_name, captain_id;
    int level = 0, experience = 0, rating = 0, capacity = 0;
    std::vector<Member> members;
};
struct Reply {
    bool ok = false;
    std::string error_code, error_message, token;
    Profile profile;
    std::vector<Area> areas;
    std::vector<TeamSummary> teams;
    std::optional<Team> team;
    std::string next;
};
bool team_name_valid(const std::string&);
// Called only by a worker. One connection/request with an overall 10s deadline;
// never retries a potentially completed registration/team creation or logs credentials.
Reply perform_request(const TrustedProfile&, const Request&, const CancellationToken&);
// Public profile only; never reads a server key or database configuration.
TrustedProfile read_public_profile(const std::string& path);
}
