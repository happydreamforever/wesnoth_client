"""Generate owned practice-dispatch sources without editing the pinned engine."""
from hashlib import sha256

ENGINE_SHA256 = "7c9f7534b0293f500a598e164570cbeefb7a624e7aa908e3b9db81631bb440c6"
LAUNCHER_SHA256 = "8420a1d9110647858b997d214974c770e45dc97a585829248fbadbc77da3dbe9"


def _replace(source, expected, include, before, after):
    if sha256(source).hexdigest() != expected:
        raise ValueError("Pinned engine source differs; original preserved.")
    if source.count(include) != 1 or source.count(before) != 1:
        raise ValueError("Pinned practice dispatch anchors differ; original preserved.")
    return source.replace(include, include + b'#include "alliance_game/battle_launch.hpp"\n').replace(before, after)


def generate_engine_overlay(source: bytes) -> bytes:
    before = b'''\t\tcase title_screen::MP_LOCAL:
\t\t\tgame->play_multiplayer(game_launcher::mp_mode::LOCAL);
\t\t\tbreak;
'''
    after = before + b'''\t\tcase alliance_game::practice_title_result:
\t\t\tgame->play_multiplayer(game_launcher::mp_mode::LOCAL);
\t\t\tbreak;
'''
    return _replace(source, ENGINE_SHA256, b'#include "game_launcher.hpp" // for game_launcher, etc\n', before, after)


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
    return _replace(source, LAUNCHER_SHA256, b'#include "game_launcher.hpp"\n', before, after)
