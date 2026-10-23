// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "alliance_native/transport.hpp"
#include <optional>
#include <string>
namespace alliance_game {
struct CasualAdmission {
    alliance::TrustedProfile trusted;
    std::string ticket, username, account_id, database_generation;
    ~CasualAdmission();
};
// Tickets are obtained using the existing session and consumed exactly once by
// native multiplayer. The long-lived account token never enters stock login.
std::optional<CasualAdmission> prepare_casual_admission();
std::optional<std::string> casual_selection_ticket(const std::string& game_id, int side,
    const std::string& hero_id, const std::string& form_id);
void clear_casual_secret(std::string&) noexcept;
}
