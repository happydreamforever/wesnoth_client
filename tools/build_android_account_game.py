#!/usr/bin/env python3
"""Build the account-enabled game in separate owned stages, entirely offline.

Completed engine sources, objects, prefixes, APK and OBB are read-only. Verified
base objects are relinked with owned unified-game overlays and account code;
no completed dependency is rebuilt or reconfigured.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import stat
import struct
import sys
import time
import uuid
import zipfile

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('account_base_game', REPO / 'tools/build_android_game.py')
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)
E, B, S, M, N, A = G.E, G.B, G.S, G.E.M, G.N, G.A
BuildError = G.BuildError
KIND = 'wesnoth-android-account-game'
ENGINE_KIND = 'wesnoth-android-account-engine'
LOCK = '.wesnoth-account-game-operation.json'
MARKER = '.wesnoth-account-game.json'
ENGINE_MARKER = '.wesnoth-account-engine.json'
VERSION_CODE = 1192620
PREVIOUS_VERSION_CODES = frozenset((1192608, 1192609, 1192610, 1192611, 1192612, 1192613, 1192614, 1192615, 1192616, 1192617, 1192618, 1192619))
UPDATE_JOURNAL = '.wesnoth-account-game-update-1192620.json'
UPDATE_KIND = 'wesnoth-android-account-game-update'
UPDATE_STAGES = ('android-account-engine', 'android-account-game')
PACKAGE = G.PACKAGE
ACTIVITY = PACKAGE + '.AccountSetupActivity'
APK, JNI_ROOT, LIBRARIES, ARTIFACTS = G.APK, G.JNI_ROOT, G.LIBRARIES, G.ARTIFACTS
TEMPLATES = dict(G.TEMPLATES)
TEMPLATES.update({name + '.java': 'app/src/main/java/org/wesnoth/offline/' + name + '.java'
                  for name in ('AccountSetupActivity', 'AlliancePublicProfile', 'WesnothGameTask')})
TEMPLATES.update({name + '.java': 'app/src/main/java/org/libsdl/app/' + name + '.java'
                  for name in ('WesnothKeyboard', 'WesnothKeyboardState')})
NEW_INPUTS = ('tools/build_android_account_game.py', 'tools/native-account-game/CMakeLists.txt',
              'alliance_game/account_ui.cpp', 'alliance_game/account_ui.hpp', 'alliance_game/title_overlay.py',
              'alliance_game/engine_overlay.py',
              *(f'alliance_game/{name}.{suffix}' for name in ('session_watch', 'android_lifecycle') for suffix in ('cpp', 'hpp')), 'alliance_game/battle_launch.cpp', 'alliance_game/battle_launch.hpp',
              'alliance_game/practice_plan.cpp', 'alliance_game/practice_plan.hpp',
              'alliance_game/practice_scenario.cpp', 'alliance_game/practice_scenario.hpp',
              'alliance_native/transport.cpp', 'alliance_native/transport.hpp',
              'alliance_native/progression_protocol.hpp', 'alliance_game/progression_ui.inc', 'alliance_game/team_extension_ui.inc',
              *(f'alliance_game/progression_{name}.{suffix}' for name in ('plan', 'scenario', 'result') for suffix in ('hpp','cpp')),
              'alliance_game/progression_launch.cpp',
              *(f'alliance_game/shared_{name}.{suffix}' for name in ('launch','scenario','result') for suffix in ('hpp','cpp')),
              'alliance_game/shared_connection_overlay.py', 'alliance_game/shared_connection_state.hpp',
              'alliance_game/replay_launch.hpp', 'alliance_game/replay_launch.cpp',
              'alliance_game/companion.hpp', 'alliance_game/companion.cpp',
              'alliance_game/shared_controls.hpp', 'alliance_game/shared_controls.cpp',
              'alliance_native/account_protocol.cpp', 'alliance_native/account_protocol.hpp', 'alliance_native/unified_protocol.hpp',
              'alliance_native/account_client.cpp', 'alliance_native/account_client.hpp',
              *(f'alliance_game/{name}.{suffix}' for name in ('hero_catalog', 'story_progression', 'casual_session', 'casual_sides', 'individual_rooms', 'individual_companion', 'battle_ui', 'team_views') for suffix in ('hpp', 'cpp')),
              'alliance_game/hero_catalog.json', 'alliance_game/hero_catalog_data.inc',
              'alliance_game/story_adapters.inc', 'alliance_game/story_script_patches.hpp',
              'alliance_game/hero_unit_config.cpp', 'alliance_game/hero_unit_data.inc',
              'alliance_game/hero_units.cfg', 'alliance_game/hero_roster_source.json',
              'alliance_game/ordinary_mp_auth_overlay.py', 'alliance_game/ordinary_mp_sides_overlay.py',
              *(str('tools/native-account-game/' + name) for name in TEMPLATES))
safe, regular, fingerprint, write_new = G.safe, G.regular, G.fingerprint, G.write_new
TITLE_SOURCE = 'gui/dialogs/title_screen.cpp'
MAIN_SOURCE = 'wesnoth.cpp'
LAUNCHER_SOURCE = 'game_launcher.cpp'
CAMPAIGN_SOURCE = 'game_initialization/playcampaign.cpp'
CONNECTION_SOURCE = 'wesnothd_connection.cpp'
SYNCED_COMMANDS_SOURCE = 'synced_commands.cpp'
STORY_OVERLAYS = {'game_config_manager.cpp': 'generate_config_manager_overlay',
                  'actions/attack.cpp': 'generate_attack_overlay',
                  'actions/advancement.cpp': 'generate_advancement_overlay',
                  'scripting/lua_unit.cpp': 'generate_lua_unit_overlay'}
CASUAL_OVERLAYS = ('game_initialization/connect_engine.cpp', 'gui/dialogs/multiplayer/mp_staging.cpp',
                   'gui/dialogs/multiplayer/mp_join_game.cpp', 'gui/dialogs/multiplayer/mp_create_game.cpp')
MULTIPLAYER_SOURCE = 'game_initialization/multiplayer.cpp'
LOBBY_SOURCE = 'gui/dialogs/multiplayer/lobby.cpp'
REPLACED_SOURCES = frozenset((TITLE_SOURCE, MAIN_SOURCE, LAUNCHER_SOURCE, CAMPAIGN_SOURCE, CONNECTION_SOURCE,
                              SYNCED_COMMANDS_SOURCE, MULTIPLAYER_SOURCE, LOBBY_SOURCE, *STORY_OVERLAYS, *CASUAL_OVERLAYS))


def call(function, *args, **kwargs):
    return G.checked(function, *args, **kwargs)


def source_names(repo):
    names = ['wesnoth.cpp']
    for name in ('wesnoth', 'libwesnoth_core', 'libwesnoth', 'libwesnoth_sdl', 'libwesnoth_widgets', 'lua'):
        names.extend((repo / 'engine/source_lists' / name).read_text(encoding='utf-8').splitlines())
    names = list(dict.fromkeys(names))
    if len(names) != 568 or not REPLACED_SOURCES.issubset(names):
        raise BuildError('Expected 568 pinned engine source files, including title_screen.cpp, wesnoth.cpp and game_launcher.cpp.')
    return names


def base_objects(repo, engine_receipt):
    """Bind real existing object bytes and exact source mapping before reuse."""
    root = safe(repo / 'build/android-engine')
    configured = B.read_json(regular(root / 'configured.json', 'Original engine configuration receipt'))
    if (configured.get('kind') != E.KIND + '-configured' or configured.get('schema_version') != 1
            or configured.get('binding', {}).get('inputs') != engine_receipt['inputs']):
        raise BuildError('Original engine configuration does not match its completed receipt.')
    expected_files = ('CMakeCache.txt', 'CMakeFiles/rules.ninja', 'build.ninja', 'config.h')
    records = {name: fingerprint(root / 'build' / name) for name in expected_files}
    if configured.get('files') != records:
        raise BuildError('Original engine build configuration changed; no objects reused.')
    expected = source_names(repo)
    objects = {}
    directory = safe(root / 'build/CMakeFiles/main.dir')
    # CMake encodes the Windows drive/root in the intermediate object path.
    # Match the invariant engine/src suffix, then require exactly one per source.
    for path in directory.rglob('*'):
        safe(path)
        if not path.is_file() or not path.name.endswith('.o'):
            continue
        relative = path.relative_to(directory).as_posix()
        if '/engine/src/' not in '/' + relative:
            raise BuildError('Unexpected original engine object outside engine/src.')
        name = ('/' + relative).split('/engine/src/', 1)[1][:-2]
        if name not in expected or name in objects:
            raise BuildError('Unexpected or duplicate original engine object: ' + name)
        regular(path, 'Original engine object')
        with path.open('rb') as stream:
            header = stream.read(64)
        if B.elf_header(header) != 1:
            raise BuildError('Original engine object is not x86_64 ELF64 REL: ' + name)
        objects[name] = {'path': path.relative_to(repo).as_posix(), **fingerprint(path)}
    if set(objects) != set(expected):
        raise BuildError('Original engine objects are incomplete; preserve the stage and send the log.')
    # Bind all replaced original objects too; they remain untouched and are not linked.
    return {'objects': objects, 'configuration': records,
            'configuration_receipt': fingerprint(root / 'configured.json')}


def selected_inputs(repo):
    profile, base, wrapper = call(G.selected_inputs, repo)
    files = dict(base['project_files'])
    files.update({name: fingerprint(repo / name) for name in NEW_INPUTS})
    base['project_files'] = files
    return profile, base, wrapper


def prerequisites(repo):
    print('Checking the completed game engine, data and original object files...', flush=True)
    engine = call(E.verify_receipt, repo)
    call(G.D.verify_receipt, repo)
    return {'engine_receipt': fingerprint(repo / 'build/android-engine/receipt.json'),
            'data_receipt': fingerprint(repo / 'build/android-game-data/receipt.json'),
            'manifest': fingerprint(repo / 'build/android-game-data' / G.MANIFEST),
            'original_apk': fingerprint(repo / 'build/android-game' / APK),
            'base_engine': base_objects(repo, engine)}


def title_overlay(repo):
    spec = importlib.util.spec_from_file_location('account_title_overlay', repo / 'alliance_game/title_overlay.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.generate_title_overlay(regular(repo / 'engine/src' / TITLE_SOURCE, 'Pinned title source').read_bytes())


def main_overlay(repo):
    spec = importlib.util.spec_from_file_location('account_engine_overlay', repo / 'alliance_game/engine_overlay.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.generate_engine_overlay(regular(repo / 'engine/src' / MAIN_SOURCE, 'Pinned main-loop source').read_bytes())


def launcher_overlay(repo):
    spec = importlib.util.spec_from_file_location('account_launcher_overlay', repo / 'alliance_game/engine_overlay.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.generate_launcher_overlay(regular(repo / 'engine/src' / LAUNCHER_SOURCE, 'Pinned launcher source').read_bytes())


def campaign_overlay(repo):
    spec = importlib.util.spec_from_file_location('account_campaign_overlay', repo / 'alliance_game/engine_overlay.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.generate_campaign_overlay(regular(repo / 'engine/src' / CAMPAIGN_SOURCE, 'Pinned campaign source').read_bytes())


def shared_connection_overlay(repo):
    spec = importlib.util.spec_from_file_location('account_shared_connection_overlay', repo / 'alliance_game/shared_connection_overlay.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    generated = {name: transform(regular(repo / 'engine/src' / name, 'Pinned connection source').read_text(encoding='utf-8')).encode('utf-8')
            for name, transform in (('wesnothd_connection.cpp', module.transform_source),
                                    ('wesnothd_connection.hpp', module.transform_header))}
    sides = overlay_module(repo, 'ordinary_mp_sides_overlay')
    generated['wesnothd_connection.cpp'] = sides.add_socket_side_hooks(generated['wesnothd_connection.cpp'])
    return generated


def overlay_module(repo, name):
    spec = importlib.util.spec_from_file_location('account_' + name, repo / ('alliance_game/' + name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def unified_overlays(repo):
    engine = overlay_module(repo, 'engine_overlay')
    sides = overlay_module(repo, 'ordinary_mp_sides_overlay')
    auth = overlay_module(repo, 'ordinary_mp_auth_overlay')
    result = {}
    for name, transform in STORY_OVERLAYS.items():
        result[Path(name).name] = flatten_includes(repo, name, getattr(engine, transform)(regular(repo / 'engine/src' / name, 'Pinned story source').read_bytes()))
    for name in CASUAL_OVERLAYS:
        result[Path(name).name] = flatten_includes(repo, name, sides.generate_side_overlay(name, regular(repo / 'engine/src' / name, 'Pinned multiplayer source').read_bytes()))
    result['multiplayer.cpp'] = flatten_includes(repo, MULTIPLAYER_SOURCE, sides.add_auth_side_hooks(auth.generate_multiplayer_auth_overlay(
        regular(repo / 'engine/src' / MULTIPLAYER_SOURCE, 'Pinned multiplayer source').read_bytes())))
    result['lobby.cpp'] = flatten_includes(repo, LOBBY_SOURCE, auth.generate_lobby_overlay(regular(repo / 'engine/src' / LOBBY_SOURCE, 'Pinned lobby source').read_bytes()))
    return result


def flatten_includes(repo, original, source):
    """Keep relative header meaning when an owned copy has a flat stage path."""
    root = (repo / 'engine/src').resolve()
    parent = (root / original).parent
    def replace(match):
        candidate = (parent / match.group(1)).resolve()
        if not candidate.is_file() or not candidate.is_relative_to(root):
            return match.group(0)
        return '#include "' + candidate.relative_to(root).as_posix() + '"'
    return re.sub(r'#include "([^"\n]+)"', replace, source.decode('utf-8')).encode('utf-8')


def synced_commands_overlay(repo):
    spec = importlib.util.spec_from_file_location('account_synced_commands_overlay', repo / 'alliance_game/engine_overlay.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.generate_synced_commands_overlay(regular(repo / 'engine/src' / SYNCED_COMMANDS_SOURCE, 'Pinned command source').read_bytes())


def cmake_literal(path):
    text = str(path).replace('\\', '/')
    if any(value in text for value in (';', '\n', '\r', '"', '$', '[', ']')):
        raise BuildError('Build path contains characters unsupported by the generated CMake list.')
    return '"' + text + '"'


def engine_generated(repo, prior):
    objects = prior['base_engine']['objects']
    lines = ['set(account_base_objects']
    lines.extend(' ' + cmake_literal(repo / record['path']) for name, record in sorted(objects.items()) if name not in REPLACED_SOURCES)
    lines.append(')\n')
    return {**shared_connection_overlay(repo), **unified_overlays(repo), 'objects.cmake': '\n'.join(lines).encode('utf-8'),
            'title_screen.cpp': title_overlay(repo),
            'wesnoth.cpp': main_overlay(repo),
            'game_launcher.cpp': launcher_overlay(repo),
            'playcampaign.cpp': campaign_overlay(repo),
            'synced_commands.cpp': synced_commands_overlay(repo),
            'config.h': regular(repo / 'build/android-engine/build/config.h', 'Original config header').read_bytes()}


def prepare_owned(root, kind, marker_name, inputs, files):
    marker = {'schema_version': 1, 'kind': kind, 'inputs': inputs,
              'files': {name: {'bytes': len(data), 'sha256': B.sha256(data)} for name, data in files.items()}}
    safe(root)
    if root.exists():
        if B.read_json(regular(root / marker_name, 'Owned stage marker')) != marker:
            raise BuildError('Existing account stage has different inputs. Preserve it and send the log.')
    else:
        root.mkdir()
        write_new(root / marker_name, B.json_bytes(marker))
    for name, data in files.items():
        target = safe(root / name)
        if not target.exists():
            write_new(target, data)
        if fingerprint(target) != marker['files'][name]:
            raise BuildError('Owned account stage input changed: ' + name)
    return marker


def verify_owned(root, kind, marker_name, inputs):
    marker = B.read_json(regular(root / marker_name, 'Owned account stage marker'))
    if (marker.get('schema_version') != 1 or marker.get('kind') != kind or marker.get('inputs') != inputs
            or not isinstance(marker.get('files'), dict)):
        raise BuildError('Account stage ownership or current inputs differ.')
    for name, record in marker['files'].items():
        if (not isinstance(name, str) or name.startswith('/') or '\\' in name or ':' in name
                or any(part in ('', '.', '..') for part in name.split('/'))):
            raise BuildError('Invalid owned account stage path.')
        if fingerprint(root / name) != record:
            raise BuildError('Owned account stage input changed: ' + name)
    for path in root.rglob('*'):
        safe(path)
        name = path.relative_to(root).as_posix()
        if path.is_file() and name.startswith(('app/src/', 'gradle/')):
            if name not in marker['files'] and name not in ARTIFACTS[1:]:
                raise BuildError('Unexpected account source or payload file: ' + name)
    return marker


@contextmanager
def operation(repo):
    directory = safe(repo / 'build')
    directory.mkdir(exist_ok=True)
    path = directory / LOCK
    record = B.json_bytes({'kind': KIND, 'pid': os.getpid(), 'token': uuid.uuid4().hex})
    try:
        write_new(path, record)
    except FileExistsError as error:
        raise BuildError('Another account build is active or interrupted. Preserve the lock and send the log.') from error
    try:
        yield
    finally:
        call(N.ninja_unlink, path, record)


def engine_validation(repo, root):
    result = {}
    for name in LIBRARIES:
        path = root / 'prefix/lib' / name
        data = regular(path, 'Account engine library').read_bytes()
        result[name] = {'artifact': fingerprint(path), 'elf': call(A.inspect_elf, data,
            runtime=name == 'libc++_shared.so', required_exports=('SDL_main',) if name == 'libmain.so' else ())}
        if name != 'libmain.so' and fingerprint(path) != fingerprint(repo / 'build/android-engine/prefix/lib' / name):
            raise BuildError('Account stage dependency copy differs from the completed engine.')
    return result


def engine_build(repo, args, inputs):
    root = safe(repo / 'build/android-account-engine')
    files = engine_generated(repo, inputs['prerequisites'])
    marker = prepare_owned(root, ENGINE_KIND, ENGINE_MARKER, inputs, files)
    if (root / 'receipt.json').exists():
        engine_verify(repo, inputs)
        print('Completed account engine reused without compilation.', flush=True)
        return
    tools = B.ndk_paths(args.ndk, B.host_tag())
    versions = B.check_tools(args.cmake, args.ninja, tools)
    binding = {'inputs': inputs, 'tools': versions,
               'paths': {name: str(getattr(args, name)) for name in ('ndk', 'cmake', 'ninja')}, 'repo': str(repo)}
    configured = root / 'configured.json'
    names = ('CMakeCache.txt', 'build.ninja', 'CMakeFiles/rules.ninja', 'config.h')
    def records():
        return {name: fingerprint(root / 'build' / name) for name in names}
    env = M.isolated_environment(root / 'prefix')
    for name in ('CC', 'CXX', 'ASM', 'ASMFLAGS', 'AR', 'RANLIB', 'CMAKE_GENERATOR',
                 'CMAKE_GENERATOR_PLATFORM', 'CMAKE_GENERATOR_TOOLSET', 'BASH_ENV', 'ENV'):
        env.pop(name, None)
    if configured.exists():
        if B.read_json(configured) != {'binding': binding, 'files': records()}:
            raise BuildError('Account engine configuration changed; preserve the stage.')
    else:
        if (root / 'configure-started.json').exists() or (root / 'build').exists():
            raise BuildError('Account engine configuration was interrupted; preserve the stage.')
        write_new(root / 'configure-started.json', B.json_bytes(binding))
        command = M.cmake_arguments(args, tools, root / 'prefix', repo / 'tools/native-account-game', root / 'build')
        command += ['-DREPO=' + repo.as_posix(), '-DACCOUNT_STAGE=' + root.as_posix(), '-DCMAKE_SUPPRESS_REGENERATION=ON']
        M.run(command, env)
        write_new(configured, B.json_bytes({'binding': binding, 'files': records()}))
    M.run([args.cmake, '--build', root / 'build', '--parallel', str(args.jobs), '--', '-d', 'keepdepfile'], env)
    for name in LIBRARIES:
        source = root / 'build/libmain.so' if name == 'libmain.so' else repo / 'build/android-engine/prefix/lib' / name
        dest = root / 'prefix/lib' / name
        if dest.exists():
            if fingerprint(dest) != fingerprint(source):
                raise BuildError('Account engine installed output differs; preserve it.')
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Small supporting copies and main are written exclusively; no old stage path is a destination.
            with regular(source, 'Completed account engine output').open('rb') as inp, dest.open('xb') as out:
                while chunk := inp.read(1024 * 1024): out.write(chunk)
    validation = engine_validation(repo, root)
    verify_owned(root, ENGINE_KIND, ENGINE_MARKER, inputs)
    write_new(root / 'receipt.json', B.json_bytes({'kind': ENGINE_KIND, 'schema_version': 1,
        'inputs': inputs, 'files': marker['files'], 'validation': validation, 'device_executed': False}))
    print('ANDROID ACCOUNT ENGINE BUILD PASS', flush=True)


def engine_verify(repo, inputs):
    root = safe(repo / 'build/android-account-engine')
    marker = verify_owned(root, ENGINE_KIND, ENGINE_MARKER, inputs)
    receipt = B.read_json(regular(root / 'receipt.json', 'Account engine receipt'))
    if receipt != {'kind': ENGINE_KIND, 'schema_version': 1, 'inputs': inputs, 'files': marker['files'],
                   'validation': engine_validation(repo, root), 'device_executed': False}:
        raise BuildError('Account engine receipt or current artifact differs.')
    return receipt


def game_generated(repo, profile, wrapper, source_files):
    files = call(G.generated_files, repo, profile, wrapper, source_files)
    for template, output in TEMPLATES.items():
        content = regular(repo / 'tools/native-account-game' / template, 'Account APK template').read_text(encoding='utf-8')
        for key, value in profile.items():
            content = content.replace('@' + key.upper() + '@', str(value))
        if re.search(r'@[A-Z_]+@', content):
            raise BuildError('Unresolved account APK template setting.')
        files[output] = content.encode('utf-8')
    sdl_name = 'app/src/main/java/org/libsdl/app/SDLActivity.java'
    sdl = files[sdl_name].decode('utf-8')
    edits = {
        'mTextEdit.setInputType(input_type);': 'mTextEdit.setInputType(WesnothKeyboard.inputType(input_type));',
        '        if (event.getAction() == KeyEvent.ACTION_DOWN) {\n            onNativeKeyDown(keyCode);':
            '        event = WesnothKeyboard.event(event);\n\n        if (event.getAction() == KeyEvent.ACTION_DOWN) {\n            onNativeKeyDown(keyCode);',
    }
    for before, after in edits.items():
        if sdl.count(before) != 1: raise BuildError('Pinned SDL keyboard boundary differs; original preserved.')
        sdl = sdl.replace(before, after)
    for direction in ('Down', 'Up'):
        before = '            onNativeKey' + direction + '(keyCode);'
        if sdl.count(before) != 1: raise BuildError('Pinned SDL modifier delivery differs; original preserved.')
        sdl = sdl.replace(before, before + '\n            WesnothKeyboard.delivered(event);')
    files[sdl_name] = sdl.encode('utf-8')
    return files


def native_validation(repo, root):
    result = {}
    for name in LIBRARIES:
        path, source = root / JNI_ROOT / name, repo / 'build/android-account-engine/prefix/lib' / name
        result[name] = G.library_validation(path, source, name)
        record = B.read_json(regular(root / 'native-records' / (name + '.json'), 'Account native record'))
        if record != {'artifact': fingerprint(path), 'validation': result[name]}:
            raise BuildError('Account APK native payload changed: ' + name)
    return result


def inspect_apk(apk, root, version_code=VERSION_CODE):
    # The shared inspector verifies ZIP paths, exactly five identical ELF payloads
    # and the unchanged trusted OBB file index. Override only this stage's metadata.
    detail = G.inspect_apk(apk, root)
    detail.update(activity=ACTIVITY, version_code=version_code)
    return detail


def signature(apk, sdk, java, repo):
    aapt2, signer, _ = G.android_tools(sdk, java)
    badging = call(S.run, [aapt2, 'dump', 'badging', apk], capture=True)
    patterns = (rf"(?m)^package: name='org\.wesnoth\.offline' versionCode='{VERSION_CODE}' versionName='1\.19\.26'",
                r"(?m)^sdkVersion:'23'$", r"(?m)^targetSdkVersion:'35'$",
                rf"(?m)^launchable-activity: name='{re.escape(ACTIVITY)}'", r"(?m)^native-code: 'x86_64'\s*$")
    if not all(re.search(pattern, badging) for pattern in patterns):
        raise BuildError('Account APK package, activity, version, API or ABI differs.')
    def signer_digest(path):
        output = call(S.run, [java, '-jar', signer, 'verify', '--verbose', '--print-certs', path], capture=True)
        found = re.findall(r'(?m)^Signer #1 certificate SHA-256 digest: ([0-9a-f]{64})$', output)
        if len(found) != 1 or re.search(r'(?m)^Signer #[2-9]', output):
            raise BuildError('Expected one verified APK signer.')
        return found[0]
    if signer_digest(apk) != signer_digest(repo / 'build/android-game' / APK):
        raise BuildError('Account APK signer differs from the original game. No app uninstall or data reset is allowed.')


def verify_receipt(repo, sdk=None, java=None):
    if safe(repo / 'build' / LOCK).exists():
        raise BuildError('Account build is active or interrupted; preserve its lock.')
    profile, inputs, _ = selected_inputs(repo)
    inputs['prerequisites'] = prerequisites(repo)
    engine_verify(repo, inputs)
    inputs['account_engine_receipt'] = fingerprint(repo / 'build/android-account-engine/receipt.json')
    root = safe(repo / 'build/android-account-game')
    marker = verify_owned(root, KIND, MARKER, inputs)
    validation = {'libraries': native_validation(repo, root), 'apk': inspect_apk(root / APK, root)}
    expected = {'schema_version': 1, 'kind': KIND, 'profile': profile, 'inputs': inputs,
                'project_files': marker['files'], 'validation': validation,
                'artifacts': {name: fingerprint(root / name) for name in ARTIFACTS}, 'device_executed': False}
    if B.read_json(regular(root / 'receipt.json', 'Account APK receipt')) != expected:
        raise BuildError('Account APK receipt or artifacts differ; no Android changes made.')
    if (sdk is None) != (java is None):
        raise BuildError('Signature verification requires SDK and Java together.')
    if sdk is not None:
        signature(root / APK, sdk, java, repo)
    print('ANDROID ACCOUNT GAME ARTIFACTS VERIFIED', flush=True)
    return expected


def update_json(path):
    """Read only bounded public build metadata, never external/private files."""
    data = call(N.ninja_read, path, 8 * 1024 * 1024)
    if data is None:
        raise BuildError('Account update metadata is missing; preserve all stages.')
    try:
        import json
        def unique_pairs(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('duplicate key')
                result[key] = value
            return result
        value = json.loads(data, object_pairs_hook=unique_pairs)
        if not isinstance(value, dict):
            raise ValueError('object required')
        return value
    except (ValueError, UnicodeError) as error:
        raise BuildError('Account update metadata could not be parsed; existing files were preserved.') from error


def stage_snapshot(root):
    """Bind every old stage file, including empty Gradle bookkeeping files."""
    root = safe(root)
    if not root.is_dir():
        raise BuildError('Expected an existing ordinary account stage directory.')
    files, directories = {}, []
    for path in sorted(root.rglob('*')):
        safe(path)
        info = path.stat()
        name = path.relative_to(root).as_posix()
        if stat.S_ISDIR(info.st_mode):
            directories.append(name)
        elif stat.S_ISREG(info.st_mode):
            if info.st_size:
                files[name] = fingerprint(path)
            else:
                # Fingerprint normally rejects empty build inputs; empty cache
                # and lock files are nevertheless part of a preserved stage.
                data = call(N.ninja_read, path, 0)
                if data != b'':
                    raise BuildError('An empty account stage file changed during inspection.')
                files[name] = {'bytes': 0, 'sha256': B.sha256(data)}
        else:
            raise BuildError('Account stage contains a special file; nothing was renamed.')
    return {'files': files, 'directories': directories}


def previous_version(root, marker, receipt):
    """Bind an allowed old release to its checked generated project and receipt."""
    validation = receipt.get('validation')
    apk = validation.get('apk') if isinstance(validation, dict) else None
    version = apk.get('version_code') if isinstance(apk, dict) else None
    if type(version) is not int or version not in PREVIOUS_VERSION_CODES:
        raise BuildError('Expected a completed account APK from versions 1192608 through 1192619; nothing was renamed.')
    if 'app/build.gradle' not in marker['files']:
        raise BuildError('The previous account version is not bound to its owned Gradle input.')
    data = call(N.ninja_read, root / 'app/build.gradle', 64 * 1024)
    try:
        text = data.decode('utf-8')
    except (AttributeError, UnicodeError) as error:
        raise BuildError('The previous owned Gradle version could not be read.') from error
    declarations = re.findall(r'(?m)^\s*versionCode\s+([0-9]+)\s*$', text)
    if (len(re.findall(r'\bversionCode\b', text)) != 1 or len(declarations) != 1
            or int(declarations[0]) != version):
        raise BuildError('The previous APK receipt and owned Gradle version differ; nothing was renamed.')
    return version


def completed_previous_stages(repo, profile, current_inputs):
    """Validate old completion receipts without comparing old source hashes to new ones."""
    engine_root = safe(repo / 'build' / UPDATE_STAGES[0])
    game_root = safe(repo / 'build' / UPDATE_STAGES[1])
    before = {name: stage_snapshot(repo / 'build' / name) for name in UPDATE_STAGES}
    owner = update_json(engine_root / ENGINE_MARKER)
    previous_inputs = owner.get('inputs')
    if (not isinstance(previous_inputs, dict) or previous_inputs.get('prerequisites') != current_inputs['prerequisites']
            or set(previous_inputs) != set(current_inputs)):
        raise BuildError('The previous account build does not bind the preserved original game and prerequisites.')
    # Both validators recalculate generated-source and output hashes/ELF facts.
    # Only the source-input comparison deliberately uses the saved binding.
    engine_verify(repo, previous_inputs)
    game_inputs = dict(previous_inputs, account_engine_receipt=fingerprint(engine_root / 'receipt.json'))
    marker = verify_owned(game_root, KIND, MARKER, game_inputs)
    receipt = update_json(game_root / 'receipt.json')
    version = previous_version(game_root, marker, receipt)
    # A pending later transaction can leave older completed source directories
    # in place before its first move. Do not start another upgrade on top of it
    # or reinterpret its immutable journal as a 1192620 update.
    for later in PREVIOUS_VERSION_CODES:
        if later > version and safe(repo / 'build' / ('.wesnoth-account-game-update-' + str(later) + '.json')).exists():
            raise BuildError('A later account update journal exists beside older completed stages. Preserve it and send the log.')
    validation = {'libraries': native_validation(repo, game_root),
                  'apk': inspect_apk(game_root / APK, game_root, version)}
    expected = {'schema_version': 1, 'kind': KIND, 'profile': profile, 'inputs': game_inputs,
                'project_files': marker['files'], 'validation': validation,
                'artifacts': {name: fingerprint(game_root / name) for name in ARTIFACTS}, 'device_executed': False}
    if receipt != expected:
        raise BuildError('Expected the completed previous account APK receipt and unchanged artifacts; nothing was renamed.')
    after = {name: stage_snapshot(repo / 'build' / name) for name in UPDATE_STAGES}
    if after != before:
        raise BuildError('Previous account stages changed during completion verification; nothing was renamed.')
    return after


def checked_update_journal(record, current_inputs):
    if (set(record) != {'schema_version', 'kind', 'target_version', 'target_inputs', 'stages'}
            or type(record['schema_version']) is not int or record['schema_version'] != 1
            or record['kind'] != UPDATE_KIND or type(record['target_version']) is not int
            or record['target_version'] != VERSION_CODE or record['target_inputs'] != current_inputs
            or not isinstance(record['stages'], dict) or set(record['stages']) != set(UPDATE_STAGES)):
        raise BuildError('The saved account update does not match this source/prerequisite set. Preserve it and send the log.')
    suffixes = set()
    for name in UPDATE_STAGES:
        entry = record['stages'][name]
        if not isinstance(entry, dict) or set(entry) != {'backup', 'snapshot'}:
            raise BuildError('Invalid account update stage record.')
        match = re.fullmatch(re.escape(name) + r'-before-battle-ui-(\d{8}T\d{6}Z-[0-9a-f]{32})', str(entry['backup']))
        if not match:
            raise BuildError('Invalid account backup location; no paths were changed.')
        suffixes.add(match.group(1))
        snapshot = entry['snapshot']
        if (not isinstance(snapshot, dict) or set(snapshot) != {'files', 'directories'}
                or not isinstance(snapshot['files'], dict) or not snapshot['files']
                or not isinstance(snapshot['directories'], list)):
            raise BuildError('Invalid account stage snapshot.')
        for path in [*snapshot['files'], *snapshot['directories']]:
            if (not isinstance(path, str) or '\\' in path or ':' in path
                    or any(part in ('', '.', '..') for part in path.split('/'))):
                raise BuildError('Invalid account snapshot path.')
        for value in snapshot['files'].values():
            if (not isinstance(value, dict) or set(value) != {'bytes', 'sha256'}
                    or type(value['bytes']) is not int or value['bytes'] < 0
                    or not isinstance(value['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', value['sha256'])):
                raise BuildError('Invalid account snapshot fingerprint.')
    if len(suffixes) != 1:
        raise BuildError('Account backup transaction identifiers differ.')
    return record


def preserve_stage(source, destination, snapshot):
    """Retry sharing failures only while the exact checked source still exists."""
    if stage_snapshot(source) != snapshot:
        raise BuildError('The account stage changed before backup; preserve it and send the log.')
    original = source.stat()
    for attempt in range(len(N.NINJA_RETRY_DELAYS) + 1):
        if safe(destination).exists():
            raise BuildError('Account backup destination already exists; nothing was overwritten.')
        current = safe(source).stat()
        if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
            raise BuildError('Account stage identity changed before backup.')
        try:
            source.rename(destination)
            break
        except OSError as error:
            if (getattr(error, 'winerror', None) not in (5, 32, 33)
                    or attempt == len(N.NINJA_RETRY_DELAYS)):
                raise
            if safe(destination).exists() or not safe(source).exists() or stage_snapshot(source) != snapshot:
                raise BuildError('Account stage changed after a sharing failure; the update was stopped.') from error
            print('Retrying unchanged account stage backup: ' + source.name, flush=True)
            time.sleep(N.NINJA_RETRY_DELAYS[attempt])
    if stage_snapshot(destination) != snapshot:
        raise BuildError('The preserved account stage changed; keep both paths and send the log.')
    print('Completed account stage preserved: ' + str(destination), flush=True)


def prepare_update(repo):
    """Journal and preserve both completed stages; reruns resume partial moves/builds."""
    with operation(repo):
        profile, inputs, _ = selected_inputs(repo)
        inputs['prerequisites'] = prerequisites(repo)
        journal = safe(repo / 'build' / UPDATE_JOURNAL)
        if journal.exists():
            record = checked_update_journal(update_json(journal), inputs)
            print('Resuming the recorded account update; retained backups will not be replaced.', flush=True)
        else:
            snapshots = completed_previous_stages(repo, profile, inputs)
            token = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex
            record = {'schema_version': 1, 'kind': UPDATE_KIND, 'target_version': VERSION_CODE,
                      'target_inputs': inputs, 'stages': {name: {
                          'backup': name + '-before-battle-ui-' + token, 'snapshot': snapshots[name]}
                          for name in UPDATE_STAGES}}
            checked_update_journal(record, inputs)
            write_new(journal, B.json_bytes(record))
        # The immutable journal survives a failure between directory moves.
        # An absent backup must still have the exact old source snapshot; a
        # completed move is verified before any remaining move is attempted.
        for name in UPDATE_STAGES:
            entry = record['stages'][name]
            backup = safe(repo / 'build' / entry['backup'])
            if backup.exists():
                if stage_snapshot(backup) != entry['snapshot']:
                    raise BuildError('An account backup changed; preserve it and send the log.')
            else:
                preserve_stage(safe(repo / 'build' / name), backup, entry['snapshot'])
        for entry in record['stages'].values():
            if stage_snapshot(repo / 'build' / entry['backup']) != entry['snapshot']:
                raise BuildError('An account backup changed during preparation; preserve all stages and send the log.')
        # New in-progress/completed stages may exist on a retry. Check ownership
        # but leave detailed configure/build resume decisions to the builder.
        engine = safe(repo / 'build' / UPDATE_STAGES[0])
        game = safe(repo / 'build' / UPDATE_STAGES[1])
        if engine.exists():
            verify_owned(engine, ENGINE_KIND, ENGINE_MARKER, inputs)
        if game.exists():
            game_inputs = dict(inputs, account_engine_receipt=fingerprint(engine / 'receipt.json'))
            verify_owned(game, KIND, MARKER, game_inputs)
        current = selected_inputs(repo)[1]
        current['prerequisites'] = prerequisites(repo)
        if current != inputs:
            raise BuildError('Sources or prerequisites changed during account update preparation; no build was started.')
        print('ANDROID ACCOUNT UPDATE PREPARED', flush=True)
        print('Both previous stages are retained. Build with the existing offline account helper.', flush=True)
        return record


def build(repo, args):
    root = safe(repo / 'build/android-account-game')
    if (root / 'receipt.json').exists():
        result = verify_receipt(repo, args.sdk, args.java)
        print('Existing account APK reused without rebuilding.', flush=True)
        print('ANDROID ACCOUNT GAME BUILD PASS', flush=True)
        return result
    profile, inputs, wrapper = selected_inputs(repo)
    inputs['prerequisites'] = prerequisites(repo)
    pin, archive = call(S.verified_archive, repo, args.archive_directory)
    if pin != G.ARCHIVE:
        raise BuildError('SDL Java archive differs from the unchanged pin.')
    generated = game_generated(repo, profile, wrapper, call(S.archive_files, archive))
    strip, _ = G.strip_tool(args.ndk, B.host_tag())
    G.android_tools(args.sdk, args.java)
    call(S.require_cached_gradle, args.gradle_user_home, profile)
    env = os.environ.copy()
    for name in tuple(env):
        if name in ('GRADLE_OPTS', 'JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS', 'JDK_JAVA_OPTIONS') or name.startswith('ORG_GRADLE_PROJECT_'):
            env.pop(name, None)
    env.update(JAVA_HOME=str(args.java.parent.parent), ANDROID_HOME=str(args.sdk),
               ANDROID_SDK_ROOT=str(args.sdk), GRADLE_USER_HOME=str(args.gradle_user_home))
    with operation(repo):
        engine_build(repo, args, inputs)
        engine_verify(repo, inputs)
        inputs['account_engine_receipt'] = fingerprint(repo / 'build/android-account-engine/receipt.json')
        marker = prepare_owned(root, KIND, MARKER, inputs, generated)
        for name in LIBRARIES:
            source = regular(repo / 'build/android-account-engine/prefix/lib' / name, 'Account engine library')
            dest = safe(root / JNI_ROOT / name)
            record_path = root / 'native-records' / (name + '.json')
            if record_path.exists():
                detail = G.library_validation(dest, source, name)
                if B.read_json(record_path) != {'artifact': fingerprint(dest), 'validation': detail}:
                    raise BuildError('Existing account packaged library changed: ' + name)
            else:
                if dest.exists():
                    raise BuildError('Unrecorded account packaged library exists; preserve stage.')
                dest.parent.mkdir(parents=True, exist_ok=True)
                call(S.run, [strip, '--strip-debug', '-o', dest, source])
                detail = G.library_validation(dest, source, name)
                write_new(record_path, B.json_bytes({'artifact': fingerprint(dest), 'validation': detail}))
        call(S.run, [args.java, '-classpath', root / 'gradle/wrapper/gradle-wrapper.jar',
            'org.gradle.wrapper.GradleWrapperMain', '--offline', '--no-daemon', '--console=plain',
            '--project-dir', root, '--project-cache-dir', root / 'gradle-cache', ':app:assembleDebug'], env=env)
        validation = {'libraries': native_validation(repo, root), 'apk': inspect_apk(root / APK, root)}
        signature(root / APK, args.sdk, args.java, repo)
        verify_owned(root, KIND, MARKER, inputs)
        current = selected_inputs(repo)[1]
        current['prerequisites'] = prerequisites(repo)
        current['account_engine_receipt'] = fingerprint(repo / 'build/android-account-engine/receipt.json')
        if current != inputs:
            raise BuildError('Account build inputs changed during the build; no success receipt written.')
        result = {'schema_version': 1, 'kind': KIND, 'profile': profile, 'inputs': inputs,
                  'project_files': marker['files'], 'validation': validation,
                  'artifacts': {name: fingerprint(root / name) for name in ARTIFACTS}, 'device_executed': False}
        write_new(root / 'receipt.json', B.json_bytes(result))
    print('ANDROID ACCOUNT GAME BUILD PASS', flush=True)
    print('APK: ' + str(root / APK), flush=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('ndk', 'cmake', 'ninja', 'sdk', 'java', 'archive-directory'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--gradle-user-home', type=Path, default=Path.home() / '.gradle')
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--verify', '--verify-only', action='store_true')
    parser.add_argument('--prepare-update', action='store_true', help='Preserve completed 1192608, 1192609, 1192610, 1192611, 1192612, 1192613, 1192614, 1192615, 1192616, 1192617, 1192618 or 1192619 stages before the 1192620 battle interface update.')
    args = parser.parse_args(argv)
    try:
        for name, value in vars(args).items():
            if isinstance(value, Path): setattr(args, name, safe(value))
        if args.prepare_update:
            if args.verify:
                raise BuildError('Choose update preparation or verification, not both.')
            prepare_update(REPO)
        elif args.verify:
            verify_receipt(REPO, args.sdk, args.java)
        else:
            if not 1 <= args.jobs <= 16 or any(getattr(args, name) is None for name in ('ndk', 'cmake', 'ninja', 'sdk', 'java')):
                raise BuildError('Specify existing NDK, CMake, Ninja, SDK and Java; jobs must be 1-16.')
            build(REPO, args)
    except (BuildError, OSError, ValueError, UnicodeError, struct.error, zipfile.BadZipFile) as error:
        print('BLOCKED: ' + str(error), file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
