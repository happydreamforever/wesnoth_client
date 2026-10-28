"""Minimal pinned full-engine worker overlay; original engine stays untouched."""
from pathlib import Path
from hashlib import sha256
import json
from shared_connection_overlay import write_overlay
from engine_overlay import generate_config_manager_overlay

WORLD_SOURCES = ("world_storage", "world_scenario", "world_result", "world_launch", "world_worker")


def generate(client, out):
    client, out = Path(client), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((client / "engine/source-manifest.json").read_text())
    records = {row["path"]: row["sha256"] for row in manifest["files"]}
    def source(name):
        data = (client / "engine" / name).read_bytes()
        if sha256(data).hexdigest() != records.get(name):
            raise ValueError("Pinned native worker source differs: " + name)
        return data
    def once(data, before, after):
        if data.count(before) != 1: raise ValueError("Pinned native worker hook differs.")
        return data.replace(before, after)
    main = source("src/wesnoth.cpp")
    main = b'#include "alliance_game/world_worker.hpp"\n' + once(main,
        b'\t\tif(game->play_test() == false) {\n',
        b'\t\tif(alliance_game::world_worker_requested()) return alliance_game::run_world_worker();\n\t\tif(game->play_test() == false) {\n')
    (out / "wesnoth.cpp").write_bytes(main)
    play = source("src/game_initialization/playcampaign.cpp")
    play = b'#include "alliance_game/world_result.hpp"\n' + play
    play = once(play, b'\tif(is_replay_) {\n\t\tstate_.get_replay().set_pos(0);',
        b'\tif(is_replay_ || (mp_info_ && mp_info_->skip_replay && alliance_game::world_save(state_))) {\n\t\tstate_.get_replay().set_pos(0);')
    play = once(play, b'\tplaymp_controller playcontroller(state_.get_starting_point(), state_, mp_info_);',
        b'\tplaymp_controller playcontroller(state_.get_starting_point(), state_, mp_info_);\n\tif(mp_info_ && mp_info_->skip_replay && alliance_game::world_save(state_))playcontroller.enable_replay(true);')
    play = once(play, b'\tplaycontroller.update_savegame_snapshot();',
        b'\talliance_game::capture_world_units();\n\tplaycontroller.update_savegame_snapshot();')
    # This observes the actual engine END after its saved snapshot was captured.
    play = once(play, b'\t\tif(!end_level.proceed_to_next_level) {',
        b'\t\talliance_game::observe_world_completion(state_,res);\n\t\tif(!end_level.proceed_to_next_level) {')
    (out / "playcampaign.cpp").write_bytes(play)
    attack = source("src/actions/attack.cpp")
    for side in (b'a_', b'd_'):
        anchor = b'\t\tu.set_experience(u.experience() + ' + side + b'.xp_);'
        attack = once(attack, anchor, b'\t\talliance_game::record_world_experience(u, ' + side + b'.xp_);\n' + anchor)
    (out / "attack.cpp").write_bytes(b'#include "alliance_game/world_result.hpp"\n' + attack)
    advance = source("src/actions/advancement.cpp")
    anchor = b'\tu = resources::gameboard->units().insert(new_unit).first;'
    advance = once(advance, anchor, anchor + b'\n\talliance_game::record_world_form(*u);')
    (out / "advancement.cpp").write_bytes(b'#include "alliance_game/world_result.hpp"\n' + advance)
    lua = source("src/scripting/lua_unit.cpp")
    anchor = b'UNIT_SETTER("experience", int) {\n\tu.set_experience(value);\n}'
    lua = once(lua, anchor, anchor + b'\nUNIT_SETTER("unified_world_credit", int) {\n\talliance_game::record_world_experience(u, value);\n}')
    (out / "lua_unit.cpp").write_bytes(b'#include "alliance_game/world_result.hpp"\n' + lua)
    multiplayer = source("src/playmp_controller.cpp")
    anchor = b'void playmp_controller::process_oos(const std::string& err_msg) const\n{'
    multiplayer = once(multiplayer, anchor, anchor + b'\n\talliance_game::world_sync_error();')
    (out / "playmp_controller.cpp").write_bytes(b'#include "alliance_game/world_result.hpp"\n' + multiplayer)
    (out / "game_config_manager.cpp").write_bytes(generate_config_manager_overlay(source("src/game_config_manager.cpp")))
    write_overlay(client / "engine/src", out)
    (out / "world_json.cpp").write_text('#include <boost/json/src.hpp>\n')
    return {"wesnoth.cpp": "src/wesnoth.cpp", "playcampaign.cpp": "src/game_initialization/playcampaign.cpp",
        "attack.cpp": "src/actions/attack.cpp", "advancement.cpp": "src/actions/advancement.cpp",
        "lua_unit.cpp": "src/scripting/lua_unit.cpp", "playmp_controller.cpp": "src/playmp_controller.cpp", "game_config_manager.cpp": "src/game_config_manager.cpp",
        "wesnothd_connection.cpp": "src/wesnothd_connection.cpp", "wesnothd_connection.hpp": "src/wesnothd_connection.hpp"}
