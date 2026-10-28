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
bool ensure_player_session(const std::string& required_account = {},
                           const std::string& required_generation = {},
                           const std::string& notice = {});
// Invalidate the device session without network access; used after connection loss.
void invalidate_player_session();
bool has_player_session() noexcept;
std::optional<PlayerSession> player_session();
bool player_request(alliance::account::Request&, alliance::account::Reply&);
void show_account_dialog();
void show_campaign_heroes(const std::string& campaign);
// Main-menu action; always clears this device session even if revocation fails.
void logout_player();
void show_teams_dialog();
// One-shot Campaign shortcut from an empty personal Company.
bool take_world_campaign_request() noexcept;
void show_training_dialog();
// Rechecks current ownership before returning a selected hero/form.
bool choose_owned_hero(std::string& hero_id, std::string& form_id);
}
