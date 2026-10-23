// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <string>
class config;
class wesnothd_connection;
namespace alliance_game {
void reset_casual_game() noexcept;
void observe_casual_message(const config&);
bool casual_game_active() noexcept;
bool casual_game_resumed() noexcept;
std::string casual_side_label(int side, const std::string& username);
bool choose_casual_side(int side);
bool casual_side_ready(int side, const std::string& username);
void apply_casual_side(config& side);
bool await_casual_start(wesnothd_connection& connection);
}
