// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "transport.hpp"
#include <string>

namespace alliance::account {
struct Request {
    std::string operation, username, password, starter_id, token;
};
struct Profile {
    std::string account_id, username, unit_type, starter_id;
    int level = 0, experience = 0, total_experience = 0;
};
struct Reply {
    bool ok = false;
    std::string error_code, error_message, token;
    Profile profile;
};
// Called only by a worker. One connection/request with an overall 10s deadline;
// never retries a potentially completed registration or logs credentials.
Reply perform_request(const TrustedProfile&, const Request&, const CancellationToken&);
// Public profile only; never reads a server key or database configuration.
TrustedProfile read_public_profile(const std::string& path);
}
