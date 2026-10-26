// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <cstdint>
#include <string>
class config;
class saved_game;
namespace alliance_game {
class IndividualRoomInteraction {
public:
    IndividualRoomInteraction() noexcept;
    ~IndividualRoomInteraction();
    IndividualRoomInteraction(const IndividualRoomInteraction&)=delete;
    IndividualRoomInteraction& operator=(const IndividualRoomInteraction&)=delete;
};
bool individual_room_interaction_active() noexcept;
void reset_individual_capability() noexcept;
bool individual_rooms_supported() noexcept;
void reset_individual_room() noexcept;
void observe_individual_room(const config& packet);
bool individual_room_active() noexcept;
std::int64_t individual_room_revision() noexcept;
std::string individual_room_summary();
std::string individual_room_status();
bool individual_start_requested() noexcept;
bool consume_individual_start() noexcept;
bool individual_start_authorized() noexcept;
void invalidate_individual_start() noexcept;
bool confirm_individual_ready();
bool review_individual_join(const config& room);
bool show_individual_details(const config& room, bool can_observe);
void show_individual_settings();
bool create_individual_room(saved_game& state);
bool configure_individual_creation(config& scenario, bool eligible);
const config* individual_lobby_room(const config& lobby, int id);
std::string individual_listing_status(const config& room);
std::string individual_listing_settings(const config& room);
std::string individual_listing_review(const config& room);
// nullopt is represented by -1: not a room token, use the stock text filter.
int individual_filter_token(const config* room, const std::string& token);
void reset_individual_filters() noexcept;
bool edit_individual_filters();
std::string individual_filter_label();
bool individual_filter_settings(const config* room);
}
