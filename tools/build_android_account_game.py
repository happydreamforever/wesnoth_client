#!/usr/bin/env python3
"""Build the account-enabled game in separate owned stages, entirely offline.

Completed engine sources, objects, prefixes, APK and OBB are read-only. Exactly
567 verified base objects are relinked with the owned title-screen overlay and
account code; no completed dependency is rebuilt or reconfigured.
"""
import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import stat
import struct
import sys
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
PACKAGE = G.PACKAGE
ACTIVITY = PACKAGE + '.AccountSetupActivity'
APK, JNI_ROOT, LIBRARIES, ARTIFACTS = G.APK, G.JNI_ROOT, G.LIBRARIES, G.ARTIFACTS
TEMPLATES = dict(G.TEMPLATES)
TEMPLATES.update({name + '.java': 'app/src/main/java/org/wesnoth/offline/' + name + '.java'
                  for name in ('AccountSetupActivity', 'AlliancePublicProfile')})
NEW_INPUTS = ('tools/build_android_account_game.py', 'tools/native-account-game/CMakeLists.txt',
              'alliance_game/account_ui.cpp', 'alliance_game/account_ui.hpp', 'alliance_game/title_overlay.py',
              'alliance_native/transport.cpp', 'alliance_native/transport.hpp',
              'alliance_native/account_protocol.cpp', 'alliance_native/account_protocol.hpp',
              'alliance_native/account_client.cpp', 'alliance_native/account_client.hpp',
              *(str('tools/native-account-game/' + name) for name in TEMPLATES))
safe, regular, fingerprint, write_new = G.safe, G.regular, G.fingerprint, G.write_new
TITLE_SOURCE = 'gui/dialogs/title_screen.cpp'


def call(function, *args, **kwargs):
    return G.checked(function, *args, **kwargs)


def source_names(repo):
    names = ['wesnoth.cpp']
    for name in ('wesnoth', 'libwesnoth_core', 'libwesnoth', 'libwesnoth_sdl', 'libwesnoth_widgets', 'lua'):
        names.extend((repo / 'engine/source_lists' / name).read_text(encoding='utf-8').splitlines())
    names = list(dict.fromkeys(names))
    if len(names) != 568 or TITLE_SOURCE not in names:
        raise BuildError('Expected 568 pinned engine source files, including title_screen.cpp.')
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
    # Bind the original title object too, though it is intentionally not linked.
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


def cmake_literal(path):
    text = str(path).replace('\\', '/')
    if any(value in text for value in (';', '\n', '\r', '"', '$', '[', ']')):
        raise BuildError('Build path contains characters unsupported by the generated CMake list.')
    return '"' + text + '"'


def engine_generated(repo, prior):
    objects = prior['base_engine']['objects']
    lines = ['set(account_base_objects']
    lines.extend(' ' + cmake_literal(repo / record['path']) for name, record in sorted(objects.items()) if name != TITLE_SOURCE)
    lines.append(')\n')
    return {'objects.cmake': '\n'.join(lines).encode('utf-8'),
            'title_screen.cpp': title_overlay(repo),
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


def inspect_apk(apk, root):
    # The shared inspector verifies ZIP paths, exactly five identical ELF payloads
    # and the unchanged trusted OBB file index. Override only this stage's metadata.
    detail = G.inspect_apk(apk, root)
    detail.update(activity=ACTIVITY, version_code=1192601)
    return detail


def signature(apk, sdk, java, repo):
    aapt2, signer, _ = G.android_tools(sdk, java)
    badging = call(S.run, [aapt2, 'dump', 'badging', apk], capture=True)
    patterns = (r"(?m)^package: name='org\.wesnoth\.offline' versionCode='1192601' versionName='1\.19\.26'",
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
    args = parser.parse_args(argv)
    try:
        for name, value in vars(args).items():
            if isinstance(value, Path): setattr(args, name, safe(value))
        if args.verify:
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
