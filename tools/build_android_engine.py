#!/usr/bin/env python3
"""Build the real Wesnoth 1.19.26 Android engine using completed offline prefixes.

Native Windows/Linux Python, CMake and Ninja call NDK r26d directly. Existing
sources and dependency stages are read-only. This stage makes libmain.so and a
runner for its real --version entry point; APK/activity/assets follow separately.
"""
import argparse
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import re
import struct
import sys
import uuid

REPO = Path(__file__).resolve().parents[1]


def helper(name):
    spec = importlib.util.spec_from_file_location('engine_' + name, REPO / 'tools' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


A = helper('android_engine_artifacts')
P = helper('prepare_android_source')
O = helper('build_android_boost')
L = helper('build_android_text_layout')
N = helper('build_android_network')
B, M = O.B, O.M
BuildError = B.BuildError
KIND = 'wesnoth-android-engine'
MARKER = '.wesnoth-android-engine.json'
LOCK = '.wesnoth-engine-operation.json'
ARTIFACTS = ('prefix/bin/wesnoth-engine-cli', 'prefix/lib/libmain.so',
             'prefix/lib/libc++_shared.so', 'prefix/lib/libSDL3.so',
             'prefix/lib/libSDL3_image.so', 'prefix/lib/libSDL3_mixer.so')
PROJECT_INPUTS = tuple(dict.fromkeys((*M.PROJECT_INPUTS, *O.PROJECT_INPUTS,
    *L.PROJECT_INPUTS, *N.PROJECT_INPUTS, 'tools/build_android_engine.py',
    'tools/android_engine_artifacts.py', 'tools/prepare_android_source.py',
    'tools/native-engine/CMakeLists.txt', 'tools/native-engine/config.h.in',
    'tools/native-engine/runner.cpp', 'tools/native-engine/build-plan.json')))


def checked_call(function, *args, **kwargs):
    # Each preserved helper loads its own BuildError class through importlib.
    try:
        return function(*args, **kwargs)
    except (A.BuildError, L.BuildError, N.BuildError, P.PreparationError) as error:
        raise BuildError(str(error)) from error


def selected_inputs(repo):
    profile = B.profile_and_inputs(repo)[0]
    inputs = {name: B.fingerprint(B.require_file(B.managed_path(repo, name), 'Engine input'))
              for name in PROJECT_INPUTS}
    sources, _ = checked_call(P.verified_source, repo / 'engine')
    # Only pinned headers/source lists may participate, including implicit includes.
    for directory in ('src', 'source_lists'):
        expected = {name: {'bytes': len(data), 'sha256': B.sha256(data)}
                    for name, data in sources.items() if name.startswith(directory + '/')}
        if M.inventory(repo / 'engine', directory) != expected:
            raise BuildError('Engine source tree contains changed or unrecorded inputs: ' + directory)
    cpp = ['wesnoth.cpp']
    for name in ('wesnoth', 'libwesnoth_core', 'libwesnoth', 'libwesnoth_sdl', 'libwesnoth_widgets'):
        cpp.extend(sources['source_lists/' + name].decode('utf-8').splitlines())
    cpp = list(dict.fromkeys(cpp))
    lua = sources['source_lists/lua'].decode('utf-8').splitlines()
    if len(cpp) != 537 or len(lua) != 31 or len(lua) != len(set(lua)):
        raise BuildError('The engine must retain all 537 C++ and 31 bundled Lua sources.')
    if any(not sources.get('src/' + name) for name in cpp + lua):
        raise BuildError('An upstream engine source list entry is absent.')
    plan = B.read_json(repo / 'tools/native-engine/build-plan.json')
    if (plan.get('schema_version') != 1 or plan.get('kind') != KIND + '-plan'
            or plan.get('version') != '1.19.26' or plan.get('upstream_commit') != P.UPSTREAM_COMMIT
            or plan.get('engine_cpp_count') != len(cpp) or plan.get('lua_cpp_count') != len(lua)
            or len(plan.get('libraries', [])) != 50 or len(set(plan['libraries'])) != 50):
        raise BuildError('The selected engine build plan differs.')
    for name in plan['libraries']:
        if not re.fullmatch(r'build/android-[a-z-]+/prefix/lib/lib[a-zA-Z0-9_.+-]+[.]a', name):
            raise BuildError('Unsafe engine dependency path.')
        B.require_file(B.managed_path(repo, name), 'Verified engine static library')
    return profile, {'project_files': inputs, 'upstream_commit': P.UPSTREAM_COMMIT,
                     'source_manifest': B.fingerprint(repo / 'engine/source-manifest.json')}, plan


def prior_inputs(repo):
    prior = {}
    for stage, verify in (('media', A.verify_media), ('text-layout', L.verify_receipt),
                          ('boost', O.verify_receipt), ('network', N.verify_receipt)):
        print('Checking completed Android ' + stage + ' prerequisites...', flush=True)
        receipt = checked_call(verify, repo)
        prior[stage] = {'receipt': B.fingerprint(repo / ('build/android-' + stage) / 'receipt.json'),
                        'prefix_inventory_sha256': B.sha256(B.json_bytes(receipt['prefix_files']))}
    runtime = B.fingerprint(repo / 'build/android-media/prefix/lib/libc++_shared.so')
    for stage in ('fonts', 'text-layout', 'boost', 'network'):
        if B.fingerprint(repo / ('build/android-' + stage) / 'prefix/lib/libc++_shared.so') != runtime:
            raise BuildError('Prerequisites do not share the identical NDK C++ runtime.')
    return prior


def exclusive(path, data):
    B.reject_link(path)
    with path.open('xb') as stream:
        stream.write(data)


@contextmanager
def operation(repo):
    directory = repo / 'build'
    B.reject_link(directory)
    directory.mkdir(exist_ok=True)
    path = directory / LOCK
    content = B.json_bytes({'kind': KIND, 'pid': os.getpid(), 'token': uuid.uuid4().hex})
    try:
        checked_call(N.ninja_write_new, path, content)
    except FileExistsError as error:
        raise BuildError('An engine operation lock exists. Preserve it and send the log; do not run concurrent builds.') from error
    try:
        yield
    finally:
        # Reuse the proven bounded Windows sharing retries, restricted to the
        # exact bytes of our own small lock. No dependency metadata is touched.
        checked_call(N.ninja_unlink, path, content)


def prepare_stage(root, inputs, profile):
    B.reject_link(root.parent)
    B.reject_link(root)
    marker = {'schema_version': 1, 'kind': KIND, 'profile': profile, 'inputs': inputs}
    if root.exists():
        if B.read_json(B.managed_path(root, MARKER)) != marker:
            raise BuildError('Existing engine stage has different inputs. Nothing overwritten; send the log.')
        for path in root.rglob('*'):
            B.reject_link(path)
    else:
        root.mkdir()
        exclusive(root / MARKER, B.json_bytes(marker))
    return marker


def configure_once(root, binding, command, env):
    """A fixed configuration can resume compilation without touching Ninja logs.

    CMake reconfiguration rewrites Ninja metadata and has failed on this Windows
    machine. This stage disables automatic regeneration, configures exactly once,
    then checks inputs and generated build files on every resume. An interrupted
    configure is preserved for diagnosis, never silently repeated over its files.
    """
    complete, started = root / 'configured.json', root / 'configure-started.json'
    names = ('CMakeCache.txt', 'build.ninja', 'CMakeFiles/rules.ninja', 'config.h')
    def records():
        return {name: B.fingerprint(B.require_file(B.managed_path(root / 'build', name), 'Engine configuration'))
                for name in names}
    if complete.exists():
        saved = B.read_json(complete)
        if saved != {'schema_version': 1, 'kind': KIND + '-configured', 'binding': binding, 'files': records()}:
            raise BuildError('Engine configuration or tools changed. Preserve the stage and send the log.')
        print('Reusing verified engine configuration; Ninja metadata is not regenerated.', flush=True)
        return
    if started.exists() or (root / 'build').exists():
        raise BuildError('Engine configuration is incomplete. Preserve this stage and send the log; no reconfigure attempted.')
    exclusive(started, B.json_bytes(binding))
    M.run(command, env)
    exclusive(complete, B.json_bytes({'schema_version': 1, 'kind': KIND + '-configured',
                                     'binding': binding, 'files': records()}))


def install_files(repo, root, ndk_tools):
    mapping = {ARTIFACTS[0]: root / 'build/wesnoth-engine-cli', ARTIFACTS[1]: root / 'build/libmain.so'}
    mapping.update({name: repo / 'build/android-media' / name for name in ARTIFACTS[2:]})
    if B.fingerprint(mapping[ARTIFACTS[2]]) != B.fingerprint(ndk_tools['runtime']):
        raise BuildError('Selected NDK runtime differs from completed libraries.')
    existing = M.inventory(root)
    if set(existing) - set(mapping):
        raise BuildError('Unexpected installed engine files. Nothing overwritten.')
    for relative, source in mapping.items():
        source = B.require_file(source, 'Engine artifact')
        target = B.managed_path(root, relative)
        if target.exists():
            if B.fingerprint(target) != B.fingerprint(source):
                raise BuildError('Installed engine artifact differs. Nothing overwritten: ' + relative)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            exclusive(target, source.read_bytes())


def validate_prefix(repo, root):
    files = M.inventory(root)
    if set(files) != set(ARTIFACTS) or any(record['bytes'] == 0 for record in files.values()):
        raise BuildError('Engine prefix must contain exactly the CLI and five shared libraries.')
    validation = {}
    for relative in ARTIFACTS:
        validation[relative] = checked_call(A.inspect_elf, (root / relative).read_bytes(),
            executable=relative == ARTIFACTS[0], runtime=relative == ARTIFACTS[2],
            required_exports=('SDL_main',) if relative == ARTIFACTS[1] else ())
    for relative in ARTIFACTS[2:]:
        if files[relative] != B.fingerprint(repo / 'build/android-media' / relative):
            raise BuildError('Packaged runtime library differs from the verified Media library: ' + relative)
    if 'libmain.so' not in validation[ARTIFACTS[0]]['needed']:
        raise BuildError('Engine CLI must invoke the actual shared game engine.')
    if not {'libSDL3.so', 'libSDL3_image.so', 'libSDL3_mixer.so', 'libc++_shared.so'}.issubset(validation[ARTIFACTS[1]]['needed']):
        raise BuildError('Game engine does not link all selected SDL/runtime libraries.')
    return validation, files


def verify_receipt(repo):
    B.reject_link(repo / 'build')
    if (repo / 'build' / LOCK).exists():
        raise BuildError('An engine operation is in progress or was interrupted; cannot verify concurrently.')
    profile, inputs, _ = selected_inputs(repo)
    inputs['prerequisites'] = prior_inputs(repo)
    root = repo / 'build/android-engine'
    B.reject_link(root)
    receipt = B.read_json(B.managed_path(root, 'receipt.json'))
    validation, files = validate_prefix(repo, root)
    if (receipt.get('schema_version') != 1 or receipt.get('kind') != KIND or receipt.get('profile') != profile
            or receipt.get('inputs') != inputs or receipt.get('artifacts') != files
            or receipt.get('validation') != validation or receipt.get('device_executed') is not False):
        raise BuildError('Engine receipt, prerequisites, source or artifacts differ. Nothing sent to Android.')
    print('ANDROID ENGINE ARTIFACTS VERIFIED', flush=True)
    return receipt


def build(repo, args):
    profile, inputs, plan = selected_inputs(repo)
    inputs['prerequisites'] = prior_inputs(repo)
    ndk_tools = B.ndk_paths(args.ndk, B.host_tag())
    versions = B.check_tools(args.cmake, args.ninja, ndk_tools)
    root = repo / 'build/android-engine'
    with operation(repo):
        prepare_stage(root, inputs, profile)
        receipt_path = root / 'receipt.json'
        if receipt_path.exists():
            # A completed stage is immutable; verify it instead of rewriting it.
            validation, files = validate_prefix(repo, root)
            saved = B.read_json(receipt_path)
            if (saved.get('inputs') != inputs or saved.get('artifacts') != files
                    or saved.get('validation') != validation or saved.get('kind') != KIND
                    or saved.get('schema_version') != 1 or saved.get('profile') != profile
                    or saved.get('device_executed') is not False):
                raise BuildError('Completed engine receipt differs. Nothing overwritten.')
            print('Existing engine artifacts verified; no compilation needed.', flush=True)
        else:
            env = M.isolated_environment(root / 'prefix')
            for name in ('CC', 'CXX', 'ASM', 'ASMFLAGS', 'AR', 'RANLIB', 'CMAKE_GENERATOR',
                         'CMAKE_GENERATOR_PLATFORM', 'CMAKE_GENERATOR_TOOLSET', 'BASH_ENV', 'ENV'):
                env.pop(name, None)
            command = M.cmake_arguments(args, ndk_tools, root / 'prefix', repo / 'tools/native-engine', root / 'build')
            command += ['-DREPO=' + repo.as_posix(), '-DCMAKE_SUPPRESS_REGENERATION=ON']
            binding = {'inputs': inputs, 'tools': versions, 'paths':
                       {name: str(getattr(args, name)) for name in ('ndk', 'cmake', 'ninja')},
                       'repository': str(repo)}
            configure_once(root, binding, command, env)
            M.run([args.cmake, '--build', root / 'build', '--parallel', str(args.jobs), '--', '-d', 'keepdepfile'], env)
            install_files(repo, root, ndk_tools)
            validation, files = validate_prefix(repo, root)
            current = selected_inputs(repo)[1]
            current['prerequisites'] = prior_inputs(repo)
            if current != inputs:
                raise BuildError('Engine inputs changed during compilation. No success receipt written.')
            exclusive(receipt_path, B.json_bytes({'schema_version': 1, 'kind': KIND, 'profile': profile,
                'inputs': inputs, 'tools': versions, 'artifacts': files, 'validation': validation,
                'device_executed': False}))
    print('ANDROID ENGINE BUILD PASS', flush=True)
    print('Receipt: ' + str(root / 'receipt.json'), flush=True)
    print('Actual Wesnoth engine built. Android execution, APK, game assets and gameplay are not yet verified.', flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('ndk', 'cmake', 'ninja'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--verify', '--verify-only', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.verify:
            verify_receipt(REPO)
        else:
            if not 1 <= args.jobs <= 32 or any(getattr(args, name) is None for name in ('ndk', 'cmake', 'ninja')):
                raise BuildError('Build requires --ndk, --cmake, --ninja and jobs between 1 and 32.')
            for name in ('ndk', 'cmake', 'ninja'):
                setattr(args, name, getattr(args, name).resolve())
            build(REPO, args)
    except (BuildError, OSError, ValueError, UnicodeError, struct.error) as error:
        print('BLOCKED: ' + str(error), file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
