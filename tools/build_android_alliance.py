#!/usr/bin/env python3
"""Build the standalone Alliance Android transport check from completed prefixes.

Only this new stage is writable. Existing Boost/OpenSSL/runtime receipts are
verified read-only; no dependency build, download, APK or database is involved.
"""
import argparse
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import struct
import sys
import uuid

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]


def helper(name):
    spec = importlib.util.spec_from_file_location('alliance_' + name, REPO / 'tools' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


O = helper('build_android_boost')
N = helper('build_android_network')
A = helper('android_engine_artifacts')
B, M = O.B, O.M
BuildError = B.BuildError
KIND = 'wesnoth-android-alliance'
STAGE = 'build/android-alliance'
MARKER = '.wesnoth-android-alliance.json'
LOCK = '.wesnoth-alliance-operation.json'
ARTIFACTS = ('prefix/bin/android-alliance-check', 'prefix/lib/libc++_shared.so')
CONFIG_FILES = ('CMakeCache.txt', 'build.ninja', 'CMakeFiles/rules.ninja')
PROJECT_INPUTS = tuple(dict.fromkeys((*O.PROJECT_INPUTS, *N.PROJECT_INPUTS,
    'tools/android_engine_artifacts.py', 'tools/build_android_alliance.py',
    'tools/native-alliance/CMakeLists.txt', 'tools/native-alliance/AndroidCheck.cmake',
    'tools/native-alliance/android_check.cpp', 'tools/native-alliance/probe.cpp',
    'alliance_native/transport.cpp', 'alliance_native/transport.hpp')))


def checked(function, *args, **kwargs):
    # Preserved helpers load independent copies of the BuildError type.
    try:
        return function(*args, **kwargs)
    except (N.BuildError, A.BuildError) as error:
        raise BuildError(str(error)) from error


def record(path):
    value = B.read_json(checked(N.ninja_path, path))
    if not isinstance(value, dict):
        raise BuildError('Expected an object in Alliance metadata: ' + str(path))
    return value


def selected_inputs(repo):
    profile = B.profile_and_inputs(repo)[0]
    inputs = {name: B.fingerprint(B.require_file(checked(N.ninja_path, repo / name), 'Alliance input'))
              for name in PROJECT_INPUTS}
    return profile, {'project_files': inputs}


def prior_inputs(repo):
    prior = {}
    for stage, verify in (('boost', O.verify_receipt), ('network', N.verify_receipt)):
        print('Checking completed Android ' + stage + ' prerequisites...', flush=True)
        receipt = checked(verify, repo)
        prior[stage] = {
            'receipt': B.fingerprint(repo / ('build/android-' + stage) / 'receipt.json'),
            'prefix_inventory_sha256': B.sha256(B.json_bytes(receipt['prefix_files']))}
    runtime = B.fingerprint(repo / 'build/android-network/prefix/lib/libc++_shared.so')
    if B.fingerprint(repo / 'build/android-boost/prefix/lib/libc++_shared.so') != runtime:
        raise BuildError('Boost and Network must use the identical completed NDK C++ runtime.')
    prior['runtime'] = runtime
    return prior


def exclusive(path, data):
    checked(N.ninja_write_new, path, data)


@contextmanager
def operation(repo):
    """Bounded exact-byte lock creation/cleanup; abandoned operations fail closed."""
    parent = checked(N.ninja_path, repo / 'build')
    parent.mkdir(exist_ok=True)
    path = parent / LOCK
    data = B.json_bytes({'schema_version': 1, 'kind': KIND, 'token': uuid.uuid4().hex})
    try:
        exclusive(path, data)
    except FileExistsError as error:
        raise BuildError('Another or interrupted Alliance build is recorded. Preserve the lock and send the log.') from error
    try:
        yield
    finally:
        checked(N.ninja_unlink, path, data)


def prepare_stage(root, inputs, profile):
    checked(N.ninja_path, root)
    marker = {'schema_version': 1, 'kind': KIND, 'profile': profile, 'inputs': inputs}
    if root.exists():
        if record(root / MARKER) != marker:
            raise BuildError('Existing Alliance stage has different inputs. Nothing overwritten; send the log.')
        for path in root.rglob('*'):
            checked(N.ninja_path, path)
    else:
        root.mkdir()
        exclusive(root / MARKER, B.json_bytes(marker))
    return marker


def configure_once(root, binding, command, env):
    """Never regenerate existing Ninja metadata, including after failed configure."""
    complete, started = root / 'configured.json', root / 'configure-started.json'

    def records():
        return {name: B.fingerprint(B.require_file(checked(N.ninja_path, root / 'build' / name),
                                                 'Alliance configuration'))
                for name in CONFIG_FILES}

    if complete.exists():
        if record(complete) != {'schema_version': 1, 'kind': KIND + '-configured',
                                'binding': binding, 'files': records()}:
            raise BuildError('Alliance configuration or tools changed. Preserve the stage and send the log.')
        print('Reusing verified Alliance configuration; Ninja metadata is not regenerated.', flush=True)
        return
    if started.exists() or (root / 'build').exists():
        raise BuildError('Alliance configuration is incomplete. Preserve the stage; no reconfigure attempted.')
    exclusive(started, B.json_bytes(binding))
    M.run(command, env)
    exclusive(complete, B.json_bytes({'schema_version': 1, 'kind': KIND + '-configured',
                                     'binding': binding, 'files': records()}))


def install_files(repo, root):
    sources = {ARTIFACTS[0]: root / 'build/android-alliance-check',
               ARTIFACTS[1]: repo / 'build/android-network/prefix/lib/libc++_shared.so'}
    existing = M.inventory(root)
    if set(existing) - set(sources):
        raise BuildError('Unexpected files in the Alliance prefix. Nothing overwritten.')
    for name, source in sources.items():
        source = B.require_file(checked(N.ninja_path, source), 'Alliance artifact')
        target = checked(N.ninja_path, root / name)
        if target.exists():
            if B.fingerprint(target) != B.fingerprint(source):
                raise BuildError('Existing Alliance artifact differs. Nothing overwritten: ' + name)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            exclusive(target, source.read_bytes())


def validate_prefix(repo, root):
    files = M.inventory(root)
    if set(files) != set(ARTIFACTS) or any(value['bytes'] == 0 for value in files.values()):
        raise BuildError('Alliance prefix must contain exactly the Android check and C++ runtime.')
    validation = {}
    for name in ARTIFACTS:
        validation[name] = checked(A.inspect_elf, (root / name).read_bytes(),
                                   executable=name == ARTIFACTS[0], runtime=name == ARTIFACTS[1])
    needed = set(validation[ARTIFACTS[0]]['needed'])
    if not {'libc.so', 'libc++_shared.so'}.issubset(needed) or needed - {
            'libc.so', 'libc++_shared.so', 'libm.so', 'libdl.so', 'libz.so'}:
        raise BuildError('Alliance check has unexpected dynamic dependencies.')
    if files[ARTIFACTS[1]] != B.fingerprint(repo / 'build/android-network/prefix/lib/libc++_shared.so'):
        raise BuildError('Alliance runtime differs from the verified Network runtime.')
    return validation, files


def checked_receipt(repo, root, inputs, profile):
    saved = record(root / 'receipt.json')
    validation, files = validate_prefix(repo, root)
    if (saved.get('schema_version') != 1 or saved.get('kind') != KIND
            or saved.get('profile') != profile or saved.get('inputs') != inputs
            or saved.get('artifacts') != files or saved.get('validation') != validation
            or saved.get('device_executed') is not False):
        raise BuildError('Alliance receipt, prerequisites, source or artifacts differ. Nothing overwritten or sent.')
    if record(root / MARKER) != {'schema_version': 1, 'kind': KIND, 'profile': profile, 'inputs': inputs}:
        raise BuildError('Alliance stage ownership differs.')
    return saved


def verify_receipt(repo):
    checked(N.ninja_path, repo / 'build')
    if checked(N.ninja_read, repo / 'build' / LOCK) is not None:
        raise BuildError('An Alliance operation is in progress or interrupted; cannot verify concurrently.')
    profile, inputs = selected_inputs(repo)
    inputs['prerequisites'] = prior_inputs(repo)
    receipt = checked_receipt(repo, repo / STAGE, inputs, profile)
    print('ANDROID ALLIANCE ARTIFACTS VERIFIED', flush=True)
    return receipt


def build(repo, args):
    profile, inputs = selected_inputs(repo)
    inputs['prerequisites'] = prior_inputs(repo)
    root = repo / STAGE
    with operation(repo):
        prepare_stage(root, inputs, profile)
        if (root / 'receipt.json').exists():
            checked_receipt(repo, root, inputs, profile)
            print('Existing Alliance artifacts verified; no compilation needed.', flush=True)
        else:
            ndk = B.ndk_paths(args.ndk, B.host_tag())
            versions = B.check_tools(args.cmake, args.ninja, ndk)
            if B.fingerprint(ndk['runtime']) != inputs['prerequisites']['runtime']:
                raise BuildError('Selected NDK runtime differs from the completed prerequisite runtime.')
            env = M.isolated_environment(root / 'prefix')
            for name in ('CC', 'CXX', 'ASM', 'ASMFLAGS', 'AR', 'RANLIB', 'CMAKE_GENERATOR',
                         'CMAKE_GENERATOR_PLATFORM', 'CMAKE_GENERATOR_TOOLSET', 'BASH_ENV', 'ENV'):
                env.pop(name, None)
            command = M.cmake_arguments(args, ndk, root / 'prefix', repo / 'tools/native-alliance', root / 'build')
            command += ['-DBOOST_INCLUDE_DIR=' + (repo / 'build/android-boost/prefix/include').as_posix(),
                        '-DNETWORK_PREFIX=' + (repo / 'build/android-network/prefix').as_posix(),
                        '-DWESNOTH_ALLIANCE_ANDROID_CHECK=ON', '-DCMAKE_SUPPRESS_REGENERATION=ON']
            binding = {'inputs': inputs, 'tools': versions, 'repository': str(repo),
                       'paths': {name: str(getattr(args, name)) for name in ('ndk', 'cmake', 'ninja')}}
            configure_once(root, binding, command, env)
            M.run([args.cmake, '--build', root / 'build', '--target', 'android-alliance-check',
                   '--parallel', str(args.jobs), '--', '-d', 'keepdepfile'], env)
            install_files(repo, root)
            validation, files = validate_prefix(repo, root)
            current = selected_inputs(repo)[1]
            current['prerequisites'] = prior_inputs(repo)
            if current != inputs:
                raise BuildError('Alliance inputs changed during compilation. No success receipt written.')
            exclusive(root / 'receipt.json', B.json_bytes({
                'schema_version': 1, 'kind': KIND, 'profile': profile, 'inputs': inputs,
                'tools': versions, 'artifacts': files, 'validation': validation, 'device_executed': False}))
    print('ANDROID ALLIANCE BUILD PASS', flush=True)
    print('Receipt: ' + str(root / 'receipt.json'), flush=True)
    print('Android execution, account login and team gameplay are not tested by this build.', flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=REPO)
    for name in ('ndk', 'cmake', 'ninja'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--verify', '--verify-only', action='store_true')
    args = parser.parse_args(argv)
    try:
        if sys.platform == 'win32' and sys.version_info[:3] != (3, 14, 3):
            raise BuildError('Use the confirmed native Windows Python 3.14.3.')
        repo = args.repo.absolute()
        checked(N.ninja_path, repo)
        if args.verify:
            verify_receipt(repo)
        else:
            if not 1 <= args.jobs <= 32 or any(getattr(args, name) is None for name in ('ndk', 'cmake', 'ninja')):
                raise BuildError('Build requires --ndk, --cmake, --ninja and jobs between 1 and 32.')
            for name in ('ndk', 'cmake', 'ninja'):
                setattr(args, name, getattr(args, name).absolute())
            build(repo, args)
    except (BuildError, OSError, ValueError, UnicodeError, struct.error) as error:
        print('BLOCKED: ' + str(error), file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
