"""Generate owned practice-dispatch sources without editing the pinned engine."""
from hashlib import sha256

ENGINE_SHA256 = "7c9f7534b0293f500a598e164570cbeefb7a624e7aa908e3b9db81631bb440c6"
LAUNCHER_SHA256 = "8420a1d9110647858b997d214974c770e45dc97a585829248fbadbc77da3dbe9"


def _replace(source, expected, include, before, after):
    if sha256(source).hexdigest() != expected:
        raise ValueError("Pinned engine source differs; original preserved.")
    if source.count(include) != 1 or source.count(before) != 1:
        raise ValueError("Pinned practice dispatch anchors differ; original preserved.")
    return source.replace(include, include + b'#include "alliance_game/battle_launch.hpp"\n#include "alliance_game/progression_plan.hpp"\n#include "alliance_game/progression_result.hpp"\n#include "alliance_game/shared_launch.hpp"\n#include "alliance_game/shared_result.hpp"\n#include "alliance_game/replay_launch.hpp"\n').replace(before, after)


def generate_engine_overlay(source: bytes) -> bytes:
    before = b'''\t\tcase title_screen::MP_LOCAL:
\t\t\tgame->play_multiplayer(game_launcher::mp_mode::LOCAL);
\t\t\tbreak;
'''
    after = before + b'''\t\tcase alliance_game::shared_title_result:
\t\tcase alliance_game::replay_title_result:
\t\tcase alliance_game::progression_title_result:
\t\tcase alliance_game::practice_title_result:
\t\t\tgame->play_multiplayer(game_launcher::mp_mode::LOCAL);
\t\t\tbreak;
'''
    result = _replace(source, ENGINE_SHA256, b'#include "game_launcher.hpp" // for game_launcher, etc\n', before, after)
    gate = b'static int get_gameloop_action(game_launcher& game)\n{\n'
    if result.count(gate) != 1: raise ValueError('Pinned startup login boundary differs.')
    result = b'#include "alliance_game/account_ui.hpp"\n' + result.replace(gate, gate + b'\tif(!alliance_game::ensure_player_session()) return title_screen::QUIT_GAME;\n')
    anchor = b'\t\tif(game->play_test() == false) {\n'
    if result.count(anchor) != 1:
        raise ValueError("Pinned initialized companion dispatch differs.")
    result = result.replace(b'#include "alliance_game/shared_launch.hpp"\n', b'#include "alliance_game/shared_launch.hpp"\n#include "alliance_game/companion.hpp"\n')
    result = result.replace(anchor, b'\t\tif(alliance_game::companion_requested()) return alliance_game::run_companion();\n'+anchor)
    result = b'#include "alliance_game/session_watch.hpp"\n#include "alliance_game/android_lifecycle.hpp"\n' + result
    exit_anchor = b'static void safe_exit(int res)\n{\n'
    if result.count(exit_anchor) != 1: raise ValueError('Pinned Android task exit differs.')
    result = result.replace(exit_anchor, exit_anchor + b'\talliance_game::logout_player();\n\talliance_game::finish_game_task();\n')
    loop = b'\twhile(true) {\n\t\tif(!game->has_load_data()) {'
    if result.count(loop) != 1: raise ValueError('Pinned session monitor lifetime differs.')
    return result.replace(loop, b'\tstd::unique_ptr<alliance_game::SessionWatch> account_connection_watch;\n\tif(!alliance_game::companion_requested()) account_connection_watch = std::make_unique<alliance_game::SessionWatch>();\n' + loop)



def generate_launcher_overlay(source: bytes) -> bytes:
    before = b'''bool game_launcher::play_multiplayer(mp_mode mode)
{
\tgame_config::set_debug(game_config::mp_debug);
\ttry {
'''
    after = before + b'''\t\tif(mode == mp_mode::LOCAL && alliance_game::practice_pending()) {
\t\t\talliance_game::run_pending_practice();
\t\t\treturn true;
\t\t}
'''
    after = after + b'\t\tif(mode == mp_mode::LOCAL && alliance_game::progression_pending()) {\n\t\t\talliance_game::run_pending_progression();\n\t\t\treturn true;\n\t\t}\n'
    after += b'''\t\tif(mode == mp_mode::LOCAL && alliance_game::shared_pending()) {
\t\t\talliance_game::run_pending_shared();
\t\t\treturn true;
\t\t}
\t\tif(mode == mp_mode::LOCAL && alliance_game::replay_pending()) {
\t\t\tconst auto replay = alliance_game::take_verified_alliance_replay();
\t\t\tif(!replay) return false;
\t\t\tload_data_ = savegame::load_game_metadata{savegame::save_index_class::default_saves_dir(), *replay, "", true, true, false};
\t\t\tload_data_->read_file();
\t\t\tif(load_prepared_game()) play_replay();
\t\t\treturn true;
\t\t}
'''
    result = _replace(source, LAUNCHER_SHA256, b'#include "game_launcher.hpp"\n', before, after)
    result = b'#include "alliance_game/story_progression.hpp"\n' + result
    selection = b'\treturn sp::select_campaign(state_, jump_to_campaign_);'
    if result.count(selection) != 1: raise ValueError('Pinned campaign registration boundary differs.')
    result = result.replace(selection, b'\tif(!sp::select_campaign(state_, jump_to_campaign_)) return false;\n\treturn alliance_game::begin_story_campaign(state_);')
    anchor = b'void game_launcher::launch_game(reload_mode reload)\n{\n'
    if result.count(anchor) != 1:
        raise ValueError("Pinned saved-game launch anchor differs.")
    return result.replace(anchor, anchor + b'\tif(!play_replay_ && (alliance_game::progression_save(state_) || alliance_game::shared_save(state_))) {\n\t\tgui2::show_error_message("Hero training saves cannot resume for account rewards. Shared battles must reconnect through Teams > Battle plans. Use the completed replay action for recorded games.");\n\t\treturn;\n\t}\n')


CAMPAIGN_SHA256 = "8d4443cad710e20489d18684d23c4e56e316858c7bc96877fe4d4828c64fea43"
SYNCED_COMMANDS_SHA256 = "6e83893e0fd88a9db18cf01d14bfab4a2530b906b7f650a3ac42906f59dffe9c"


def generate_synced_commands_overlay(source: bytes) -> bytes:
    """Record confirmed variable-only captain actions instead of undoing them."""
    if sha256(source).hexdigest() != SYNCED_COMMANDS_SHA256:
        raise ValueError("Pinned synchronized-command source differs; original preserved.")
    anchor = b'''SYNCED_COMMAND_HANDLER_FUNCTION(custom_command, child, /*spectator*/)
{
\tassert(resources::lua_kernel);
'''
    if source.count(anchor) != 1:
        raise ValueError("Pinned captain command commit boundary differs.")
    return source.replace(anchor, anchor + b'''\tif(child["name"] == "alliance_captain_v1") {
\t\tconst auto action = child.child_or_empty("data")["action"].str();
\t\tif(action == "surrender" || action == "offer_draw" || action == "withdraw_draw") {
\t\t\t// These explicitly confirmed decisions must reach replay and peers even
\t\t\t// when they change only WML variables and add no ordinary undo action.
\t\t\tsynced_context::block_undo();
\t\t}
\t}
''')

def generate_campaign_overlay(source: bytes) -> bytes:
    if sha256(source).hexdigest() != CAMPAIGN_SHA256:
        raise ValueError("Pinned campaign source differs; original preserved.")
    include=b'#include "game_initialization/playcampaign.hpp"\n'
    anchor=b"\t\t// proceed_to_next_level <=> 'any human side received victory'"
    if source.count(include)!=1 or source.count(anchor)!=1:
        raise ValueError("Pinned campaign result anchors differ.")
    entry=b'level_result::type campaign_controller::play_game()\n{\n\tif(is_replay_) {'
    if source.count(entry)!=1:
        raise ValueError("Pinned shared reconnect replay cursor differs.")
    result=source.replace(entry,b'level_result::type campaign_controller::play_game()\n{\n\tif(alliance_game::shared_pending()) {\n\t\talliance_game::run_pending_shared();\n\t\treturn level_result::type::quit;\n\t}\n\tif(is_replay_ || (mp_info_ && mp_info_->skip_replay && alliance_game::shared_save(state_))) {')
    snapshot=b'\tstate_.set_snapshot(playcontroller.to_config());'
    if result.count(snapshot)!=1: raise ValueError('Pinned story snapshot boundary differs.')
    result=result.replace(snapshot,b'\talliance_game::capture_story_participants();\n'+snapshot)
    mp_snapshot=b'\tplaycontroller.update_savegame_snapshot();'
    if result.count(mp_snapshot)!=1: raise ValueError('Pinned multiplayer snapshot boundary differs.')
    result=result.replace(mp_snapshot,b'\talliance_game::capture_story_participants();\n'+mp_snapshot)
    controller=b'\tplaymp_controller playcontroller(state_.get_starting_point(), state_, mp_info_);\n'
    if result.count(controller)!=1:
        raise ValueError("Pinned shared reconnect controller differs.")
    result=result.replace(controller,controller+b'\tif(mp_info_ && mp_info_->skip_replay && alliance_game::shared_save(state_)) playcontroller.enable_replay(true);\n')
    prep = b'\t\t\tstate_.expand_mp_options();'
    if result.count(prep) != 1: raise ValueError('Pinned scenario account boundary differs.')
    result = result.replace(prep, prep + b'\n\t\t\tif(!alliance_game::prepare_story_scenario(state_, is_replay_)) return level_result::type::quit;')
    transition = b'\t\tstate_.convert_to_start_save();'
    if result.count(transition) != 1: raise ValueError('Pinned story transition boundary differs.')
    result = result.replace(transition, transition + b'\n\t\tif(!alliance_game::advance_story_scenario(state_)) {\n\t\t\tgui2::show_error_message("The next campaign checkpoint could not be saved. Resume the latest campaign save and keep its private game records. [story-transition]");\n\t\t\treturn level_result::type::quit;\n\t\t}')
    result = b'#include "alliance_game/story_progression.hpp"\n' + result
    return result.replace(include,include+b'#include "alliance_game/progression_result.hpp"\n#include "alliance_game/shared_launch.hpp"\n#include "alliance_game/shared_result.hpp"\n#include "alliance_game/replay_launch.hpp"\n').replace(
        anchor,b'\t\talliance_game::observe_story_completion(state_, res);\n\t\talliance_game::observe_progression_completion(state_, res);\n\t\talliance_game::observe_shared_completion(state_, res);\n\n'+anchor)

CONFIG_MANAGER_SHA256 = "e04dded36b2aa3c313861b4c441d51b0ffccaae4e0e2bd0f6ff63f3c52bc3f8a"
ATTACK_SHA256 = "336bf909f7426915cb66df6bb96ee3fbc5acec58a6fd78649ecf0c06aab68878"
ADVANCEMENT_SHA256 = "39b1e3dc616b1c94c972bd0aba6ca4d08e20190225a5ecc503edbc046b3414be"
LUA_UNIT_SHA256 = "cb76a18ef7db9064e9ac6c5e8284a851b5af49942658b9c8e87415e3ac9516eb"

def _hero_overlay(source, expected, anchor, replacement, include):
    if sha256(source).hexdigest() != expected or source.count(anchor) != 1:
        raise ValueError("Pinned hero engine boundary differs; original preserved.")
    return b'#include "alliance_game/' + include + b'"\n' + source.replace(anchor, replacement)

def generate_config_manager_overlay(source: bytes) -> bytes:
    anchor=b'unit_types.set_config(game_config().merged_children_view("units"));'
    return _hero_overlay(source, CONFIG_MANAGER_SHA256, anchor,
        b'unit_types.set_config(alliance_game::append_hero_unit_config(game_config().merged_children_view("units")));', b'hero_catalog.hpp')

def generate_attack_overlay(source: bytes) -> bytes:
    if sha256(source).hexdigest()!=ATTACK_SHA256:
        raise ValueError("Pinned combat XP source differs.")
    for who in (b'a_',b'd_'):
        anchor=b'\t\tu.set_experience(u.experience() + '+who+b'.xp_);'
        if source.count(anchor)!=1:raise ValueError("Pinned earned combat XP boundary differs.")
        source=source.replace(anchor,b'\t\talliance_game::record_story_experience(u, '+who+b'.xp_);\n'+anchor)
    return b'#include "alliance_game/story_progression.hpp"\n'+source

def generate_advancement_overlay(source: bytes) -> bytes:
    anchor=b'\tu = resources::gameboard->units().insert(new_unit).first;'
    return _hero_overlay(source, ADVANCEMENT_SHA256, anchor,
        anchor+b'\n\talliance_game::record_story_form(*u);', b'story_progression.hpp')

def generate_lua_unit_overlay(source: bytes) -> bytes:
    anchor=b'UNIT_SETTER("experience", int) {\n\tu.set_experience(value);\n}'
    return _hero_overlay(source,LUA_UNIT_SHA256,anchor,
        anchor+b'\nUNIT_SETTER("unified_story_credit", int) {\n\talliance_game::record_story_experience(u, value);\n}',b'story_progression.hpp')
