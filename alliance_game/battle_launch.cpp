// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/battle_launch.hpp"
#include "alliance_game/practice_plan.hpp"
#include "alliance_game/practice_scenario.hpp"
#include "cursor.hpp"
#include "events.hpp"
#include "game_config_manager.hpp"
#include "game_initialization/playcampaign.hpp"
#include "gui/dialogs/message.hpp"
#include "map/map.hpp"
#include "saved_game.hpp"
#include "units/types.hpp"
#include <stdexcept>

namespace alliance_game {
void run_pending_practice()
{
    const auto plan = take_pending_practice();
    if(!plan) return;
    saved_game state;
    // All input has been consumed before loading; a failure cannot queue an
    // automatic second launch. No Alliance request or state mutation occurs.
    try {
        auto* manager = game_config_manager::get();
        if(!manager) throw std::runtime_error("Game configuration is unavailable.");
        manager->load_game_config_for_create(true);
        const auto& game = manager->game_config();
        const auto source = game.find_child("multiplayer", "id", plan->scenario_id);
        const auto era = game.find_child("era", "id", "era_default");
        if(!source || !era) throw std::runtime_error("Practice data is unavailable.");
        config scenario = make_practice_scenario(*plan, *source, *era);
        saved_game::expand_map_file(scenario);
        const gamemap map(scenario["map_data"].str());
        if(map.num_valid_starting_positions() != static_cast<int>(plan->seats.size()))
            throw std::runtime_error("Practice map positions do not match.");
        for(const auto& seat : plan->seats) {
            const auto* type = unit_types.find(seat.unit_type);
            if(!type || type->level() != seat.level || !map.on_board(map.starting_position(seat.side)))
                throw std::runtime_error("Practice leader or starting position is unavailable.");
        }
        state.classification().type = campaign_type::type::multiplayer;
        state.classification().era_id = "era_default";
        state.classification().end_credits = false;
        state.mp_settings().name = scenario["name"].str();
        state.mp_settings().mp_scenario = scenario["id"].str();
        state.mp_settings().use_map_settings = true;
        state.mp_settings().shuffle_sides = false;
        state.mp_settings().num_turns = plan->settings.turn_limit;
        state.mp_settings().village_gold = 2;
        state.mp_settings().village_support = 1;
        state.mp_settings().xp_modifier = 100;
        state.mp_settings().fog_game = plan->settings.fog;
        state.mp_settings().shroud_game = false;
        state.mp_settings().allow_observers = false;
        state.mp_settings().private_replay = true;
        state.set_scenario(std::move(scenario));
        state.set_random_seed();
    } catch(const std::exception&) {
        gui2::show_error_message("Local practice could not load the saved lineup and installed game data. Your account and battle plan were not changed. [practice-load]");
        return;
    }
    events::discard_input();
    cursor::set(cursor::NORMAL);
    campaign_controller controller(state);
    // No mp metadata/connection is supplied: all sides run on this device.
    // Let the standard game-launcher boundary handle save-load and quit events.
    controller.play_game();
}
}
