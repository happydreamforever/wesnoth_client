// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "alliance_native/account_client.hpp"
#include <optional>
#include <string>
namespace alliance_game {
struct PlayerSession {
    alliance::TrustedProfile trusted;
    alliance::account::Profile account;
    std::string token;
    ~PlayerSession();
};
// Startup gate. Cancelling authentication exits the game; there is no guest path.
bool ensure_player_session();
std::optional<PlayerSession> player_session();
bool player_request(alliance::account::Request&, alliance::account::Reply&);
void show_account_dialog();
void show_teams_dialog();
void show_training_dialog();
// Rechecks current ownership before returning a selected hero/form.
bool choose_owned_hero(std::string& hero_id, std::string& form_id);
}
