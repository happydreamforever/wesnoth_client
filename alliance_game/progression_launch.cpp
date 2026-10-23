// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/progression_plan.hpp"
#include "alliance_game/progression_result.hpp"
#include "alliance_game/progression_scenario.hpp"
#include "cursor.hpp"
#include "events.hpp"
#include "game_config_manager.hpp"
#include "game_initialization/playcampaign.hpp"
#include "gui/dialogs/message.hpp"
#include "map/map.hpp"
#include "saved_game.hpp"
#include "units/types.hpp"
#include <stdexcept>
#include <memory>
namespace alliance_game {
void run_pending_progression() {
    const auto run=take_pending_progression();if(!run)return;
    saved_game state;
    std::unique_ptr<ProgressionActiveLaunch> active;
    try {
        auto* manager=game_config_manager::get();if(!manager)throw std::runtime_error("Missing game configuration.");
        manager->load_game_config_for_create(true);
        const auto era=manager->game_config().find_child("era","id","era_default");if(!era)throw std::runtime_error("Missing mission era.");
        auto scenario=make_progression_scenario(*run,*era);
        const gamemap map(scenario["map_data"].str());
        const auto* unit=unit_types.find(run->unit_type);
        if(!unit||unit->level()!=run->leader_level||map.num_valid_starting_positions()!=static_cast<int>(scenario.child_count("side")))throw std::runtime_error("Mission map or leader is unavailable.");
        state.classification().type=campaign_type::type::multiplayer;
        state.classification().era_id="era_default";state.classification().end_credits=false;
        state.mp_settings().name=scenario["name"].str();state.mp_settings().mp_scenario=scenario["id"].str();
        state.mp_settings().use_map_settings=true;state.mp_settings().shuffle_sides=false;
        state.mp_settings().num_turns=scenario["turns"].to_int();state.mp_settings().fog_game=true;
        state.mp_settings().shroud_game=false;state.mp_settings().xp_modifier=100;state.mp_settings().allow_observers=false;
        state.mp_settings().private_replay=true;state.set_scenario(std::move(scenario));state.set_random_seed();
        register_progression_run(*run);
        active=std::make_unique<ProgressionActiveLaunch>(*run);
    } catch(...) {
        gui2::show_error_message("The registered mission could not start safely. Open Alliance to inspect or cancel the active run before trying a new one. [progression-load]");return;
    }
    events::discard_input();cursor::set(cursor::NORMAL);
    campaign_controller controller(state);
    // Real end-of-level hook captures before the engine clears the replay.
    // Exceptions/quit unwind this scope, so a later stock Load cannot report.
    controller.play_game();
}
}
