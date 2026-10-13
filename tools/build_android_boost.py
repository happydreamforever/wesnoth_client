#!/usr/bin/env python3
"""Build pinned reusable Boost1.88 libraries offline for Android x86_64/API23.

Native Python/CMake/Ninja use NDK Clang directly. Previous ICU/font stages are
verified read-only; no host b2, MSYS compiler, download or target execution.
"""
import argparse
import importlib.util
import io
from pathlib import Path
import re
import struct
import sys
import tarfile

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('wesnoth_boost_icu', REPO / 'tools/build_android_icu.py')
I = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(I)
M, B = I.M, I.B
BuildError = B.BuildError
KIND = 'wesnoth-android-boost'
MARKER = '.wesnoth-android-boost.json'
PREFIX_STATE = '.prefix-state.json'
BOOST_ARCHIVE = 'boost_1_88_0.tar.bz2'
BOOST_ROOT = 'boost_1_88_0'
COMPONENTS = ('atomic', 'charconv', 'chrono', 'context', 'coroutine', 'filesystem', 'iostreams',
              'locale', 'process', 'program_options', 'random', 'thread')
COUNTS = dict(zip(COMPONENTS, (3, 2, 3, 5, 3, 9, 5, 24, 17, 11, 1, 3)))
PROJECT_INPUTS = tuple(dict.fromkeys((*I.PROJECT_INPUTS, 'tools/build_android_boost.py',
    'tools/native-boost/build-plan.json', 'tools/native-boost/CMakeLists.txt', 'tools/native-boost/probe.cpp')))
ARTIFACTS = ('prefix/bin/wesnoth-boost-check', 'prefix/lib/libc++_shared.so')
REQUIRED_ARCHIVES = tuple('libboost_' + name + '.a' for name in COMPONENTS) + ('libbz2.a',)


def pin(data):
    return {'bytes': len(data), 'sha256': B.sha256(data)}


def selected_inputs(repo):
    profile = I.selected_inputs(repo)[0]
    inputs = {name: B.fingerprint(B.require_file(repo / name, 'Boost input')) for name in PROJECT_INPUTS}
    plan = B.read_json(repo / 'tools/native-boost/build-plan.json')
    if (plan.get('schema_version') != 1 or plan.get('kind') != KIND + '-plan' or plan.get('version') != '1.88.0'
            or plan.get('recipe') != 'cmake-selected-upstream-sources' or set(plan.get('libraries', {})) != set(COMPONENTS)
            or {k: len(v) for k,v in plan['libraries'].items()} != COUNTS
            or len(plan.get('bzip2_sources', [])) != 7 or plan.get('public_headers', {}).get('count') != 16010
            or not re.fullmatch('[0-9a-f]{64}', str(plan.get('public_headers', {}).get('sha256', '')))
            or plan.get('features') != {'locale': 'icu-only', 'iostreams': ['zlib', 'bzip2'],
                'context': 'x86_64-sysv-elf-gas-fcontext', 'process': 'v2', 'thread': 'pthread'}):
        raise BuildError('Boost recipe must retain the selected upstream source lists and Android features.')
    for names in plan['libraries'].values():
        if len(names) != len(set(names)) or any(not re.fullmatch('src/[a-zA-Z0-9_/.-]+[.](cpp|S)', name) or '..' in name.split('/') for name in names):
            raise BuildError('Unsafe or duplicate Boost source list entry.')
    if any(not re.fullmatch('[a-z]+[.]c', name) for name in plan['bzip2_sources']):
        raise BuildError('Unsafe bzip2 source list entry.')
    manifest = B.read_json(repo / 'tools/android-native-archives.json')
    pins = {}
    for name in (BOOST_ARCHIVE, B.ARCHIVE_NAME):
        entries = [e for e in manifest.get('archives', []) if e.get('name') == name]
        if len(entries) != 1:
            raise BuildError('Expected exactly one pinned Boost/bzip2 source archive: ' + name)
        record = {key: entries[0].get(key) for key in ('bytes', 'sha256')}
        if type(record['bytes']) is not int or not 0 < record['bytes'] < 200_000_000 or not re.fullmatch('[0-9a-f]{64}', str(record['sha256'])):
            raise BuildError('Invalid Boost/bzip2 archive pin.')
        pins[name] = record
    return profile, inputs, plan, pins


def prior_inputs(repo):
    receipt = I.verify_receipt(repo)
    return {'icu': {'receipt': B.fingerprint(repo / 'build/android-icu/receipt.json'),
                    'prefix_files': receipt['prefix_files'], 'prerequisites': receipt['inputs']['prerequisites']}}


def selected_member(relative):
    parts = relative.split('/')
    return (relative.startswith('boost/') or relative == 'LICENSE_1_0.txt'
        or (len(parts) > 2 and parts[0] == 'libs' and parts[1] in COMPONENTS
            and (parts[2] in ('src', 'config') or relative.endswith('/CMakeLists.txt')
                 or relative.endswith('/build/Jamfile.v2') or relative.endswith('/build/Jamfile'))))


def archive_files(data, plan):
    """Single streaming pass, retaining only complete headers and selected sources.

    Unused documentation/tests are not extracted (including their long paths).
    The complete original archive's size/hash is verified before this function.
    """
    files, seen, spelling, total, count = {}, set(), {}, 0, 0
    with tarfile.open(fileobj=io.BytesIO(data), mode='r|bz2') as archive:
        for entry in archive:
            count += 1
            if count > 120000:
                raise BuildError('Boost archive exceeds member limit.')
            name = entry.name.rstrip('/') if entry.isdir() else entry.name
            parts = M.safe_name(name)
            if parts[0] != BOOST_ROOT:
                raise BuildError('Unexpected Boost archive root.')
            relative = '/'.join(parts[1:])
            if not selected_member(relative) or entry.isdir():
                continue
            if name in seen or not entry.isfile():
                raise BuildError('Duplicate, linked or special selected Boost member: ' + name)
            seen.add(name)
            for end in range(1, len(parts) + 1):
                component = '/'.join(parts[:end])
                if spelling.setdefault(component.casefold(), component) != component:
                    raise BuildError('Selected Boost paths collide on Windows: ' + name)
            total += entry.size
            if total > 180_000_000 or entry.size > 8_000_000:
                raise BuildError('Selected Boost source exceeds extraction size limits.')
            stream = archive.extractfile(entry)
            content = stream.read() if stream is not None else b''
            if len(content) != entry.size:
                raise BuildError('Incomplete selected Boost archive member: ' + name)
            files['sources/' + name] = content
    records = {name.removeprefix('sources/' + BOOST_ROOT + '/'): pin(value) for name,value in files.items()}
    if {'count': len(records), 'sha256': B.sha256(B.json_bytes(records))} != plan['source_inventory']:
        raise BuildError('Selected Boost source/header inventory differs from the pinned release.')
    headers = {k:v for k,v in records.items() if k.startswith('boost/')}
    if {'count': len(headers), 'sha256': B.sha256(B.json_bytes(headers))} != plan['public_headers']:
        raise BuildError('Complete Boost header inventory differs.')
    for component, names in plan['libraries'].items():
        for name in names:
            if not records.get('libs/' + component + '/' + name):
                raise BuildError('Selected Boost source is absent: ' + component + '/' + name)
    if records.get('LICENSE_1_0.txt') != plan['licenses']['boost']:
        raise BuildError('Boost license differs.')
    return files


def prepare_sources(root, files, profile, pins):
    B.reject_link(root.parent); B.reject_link(root)
    marker = {'schema_version': 1, 'kind': KIND, 'profile': profile, 'archives': pins,
              'source_files': {name: pin(value) for name,value in sorted(files.items())}}
    if root.exists():
        path = B.managed_path(root, MARKER)
        if not path.is_file() or B.read_json(path) != marker:
            raise BuildError('Existing Boost stage is unmanaged or uses different sources. Nothing overwritten.')
        verify_sources(root, marker)
        for relative in ('build', 'prefix'):
            B.reject_link(root / relative)
            for child in (root / relative).rglob('*'):
                B.reject_link(child)
        return marker
    root.mkdir(parents=True)
    for name, content in files.items():
        path = B.managed_path(root, name); path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(content)
    (root / MARKER).write_bytes(B.json_bytes(marker))
    return marker


def verify_sources(root, marker):
    if marker.get('kind') != KIND or M.inventory(root, 'sources') != marker.get('source_files'):
        raise BuildError('Managed Boost sources changed. Nothing overwritten.')


def verify_prefix(root):
    files = M.inventory(root)
    state = B.managed_path(root, PREFIX_STATE)
    if state.exists():
        if B.read_json(state) != {'schema_version': 1, 'kind': KIND, 'prefix_files': files}:
            raise BuildError('Installed Boost prefix changed or installation interrupted. Nothing overwritten.')
    elif files:
        raise BuildError('Existing Boost prefix has no owned file record. Nothing overwritten.')
    return files


def save_prefix_state(root):
    files = M.inventory(root)
    (root / PREFIX_STATE).write_bytes(B.json_bytes({'schema_version': 1, 'kind': KIND, 'prefix_files': files}))
    return files


def pkg_config_files():
    # Single complete development module avoids consumers omitting static
    # transitive libraries. ICU remains a separate verified relocatable prefix.
    order = ('locale', 'iostreams', 'process', 'filesystem', 'program_options', 'random',
             'coroutine', 'context', 'thread', 'chrono', 'atomic', 'charconv')
    text = ('prefix=${pcfiledir}/../..\nlibdir=${prefix}/lib\nincludedir=${prefix}/include\n'
            'Name: wesnoth-boost\nDescription: Boost1.88 Android static game dependencies\nVersion: 1.88.0\n'
            'Requires.private: icu-i18n = 78.3\nLibs: -L${libdir} ' + ' '.join('-lboost_' + n for n in order)
            + ' -lbz2\nLibs.private: -lz -lc++_shared -ldl -lm\n'
            'Cflags: -I${includedir} -DBOOST_ALL_NO_LIB -DBOOST_CHARCONV_NO_QUADMATH\n')
    return {'wesnoth-boost.pc': text.encode()}


def validate_prefix(root, plan):
    prefix = root / 'prefix'
    headers = {path.relative_to(prefix / 'include').as_posix(): B.fingerprint(path)
               for path in sorted((prefix / 'include/boost').rglob('*')) if path.is_file()}
    if {'count': len(headers), 'sha256': B.sha256(B.json_bytes(headers))} != plan['public_headers']:
        raise BuildError('Installed Boost headers differ from the complete release.')
    if B.fingerprint(B.require_file(prefix / 'include/bzlib.h', 'bzip2 header')) != plan['bzip2_header']:
        raise BuildError('Installed bzip2 header differs.')
    for name, expected in pkg_config_files().items():
        if B.require_file(prefix / 'lib/pkgconfig' / name, 'Boost metadata').read_bytes() != expected:
            raise BuildError('Boost development metadata differs or is not relocatable.')
    for name, key in [('LICENSE_1_0.txt', 'boost'), ('bzip2-LICENSE', 'bzip2')]:
        if B.fingerprint(B.require_file(prefix / 'share/licenses/wesnoth-boost' / name, 'Complete license')) != plan['licenses'][key]:
            raise BuildError('Installed Boost/bzip2 license differs.')
    static = {p.relative_to(prefix).as_posix(): M.inspect_static_archive(p) for p in sorted(prefix.rglob('*.a'))}
    expected = {'lib/libboost_' + name + '.a': count for name,count in COUNTS.items()}
    expected['lib/libbz2.a'] = 7
    if set(static) != set(expected) or any(static[name]['elf_object_count'] != count for name,count in expected.items()):
        raise BuildError('Boost archives do not contain the complete selected source lists.')
    runtime = {name: B.inspect_shared_elf(B.require_file(root / name, 'Boost artifact').read_bytes()) for name in ARTIFACTS}
    probe = runtime[ARTIFACTS[0]]
    if (probe['interpreter'] != '/system/bin/linker64' or probe['android_api_notes'] != [23]
            or not {'libc.so', 'libc++_shared.so', 'libz.so'}.issubset(probe['needed'])
            or not set(probe['needed']).issubset({'libc.so', 'libc++_shared.so', 'libz.so', 'libm.so', 'libdl.so'})):
        raise BuildError('Boost probe must target Android API23 with only NDK system/C++ dependencies.')
    files = M.inventory(root)
    for name,record in files.items():
        if record['bytes'] == 0 or name.endswith('.la') or ((name.endswith('.so') or '.so.' in name) and name != ARTIFACTS[1]):
            raise BuildError('Unexpected installed Boost file: ' + name)
    return {'static_libraries': static, 'runtime': runtime}, files


def verify_receipt(repo):
    profile, inputs, plan, pins = selected_inputs(repo)
    prior = prior_inputs(repo)
    root = repo / 'build/android-boost'
    B.reject_link(root.parent); B.reject_link(root)
    receipt = B.read_json(B.managed_path(root, 'receipt.json'))
    validation, files = validate_prefix(root, plan)
    if B.fingerprint(root / ARTIFACTS[1]) != B.fingerprint(repo / 'build/android-icu' / I.ARTIFACTS[1]):
        raise BuildError('Boost runtime differs from the verified ICU runtime.')
    if (receipt.get('schema_version') != 1 or receipt.get('kind') != KIND or receipt.get('profile') != profile
            or receipt.get('inputs') != {'project_files': inputs, 'archives': pins, 'prerequisites': prior}
            or receipt.get('validation') != validation or receipt.get('prefix_files') != files
            or receipt.get('artifacts') != {name: files[name] for name in ARTIFACTS}):
        raise BuildError('Boost receipt/input/artifact hashes differ. Preserve changes before rebuilding.')
    print('ANDROID BOOST ARTIFACTS VERIFIED', flush=True)
    return receipt


def build(repo, args):
    profile, inputs, plan, pins = selected_inputs(repo)
    prior = prior_inputs(repo)
    files = archive_files(M.archive_data(repo, BOOST_ARCHIVE, pins[BOOST_ARCHIVE]), plan)
    bzip_files = B.archive_files(M.archive_data(repo, B.ARCHIVE_NAME, pins[B.ARCHIVE_NAME]))
    files.update(bzip_files)
    ndk_tools = B.ndk_paths(args.ndk, B.host_tag())
    versions = B.check_tools(args.cmake, args.ninja, ndk_tools)
    root = repo / 'build/android-boost'
    marker = prepare_sources(root, files, profile, pins)
    verify_prefix(root)
    receipt_path = B.managed_path(root, 'receipt.json')
    if receipt_path.exists(): receipt_path.unlink()
    prefix = root / 'prefix'
    (prefix / 'lib/pkgconfig').mkdir(parents=True, exist_ok=True)
    env = M.isolated_environment(prefix)
    for name in ('CC', 'CXX', 'ASM', 'ASMFLAGS', 'AR', 'RANLIB', 'CMAKE_GENERATOR', 'CMAKE_GENERATOR_PLATFORM', 'CMAKE_GENERATOR_TOOLSET'):
        env.pop(name, None)
    save_prefix_state(root)
    output = root / 'build'
    M.run(M.cmake_arguments(args, ndk_tools, prefix, repo / 'tools/native-boost', output)
          + ['-DBOOST_SOURCE=' + (root / 'sources' / BOOST_ROOT).as_posix(),
             '-DBZIP2_SOURCE=' + (root / 'sources' / B.SOURCE_ROOT).as_posix(),
             '-DICU_PREFIX=' + (repo / 'build/android-icu/prefix').as_posix()], env)
    M.run([args.cmake, '--build', output, '--parallel', str(args.jobs)], env)
    print('Installing complete Boost header/library prefix...', flush=True)
    M.run([args.cmake, '--install', output], env)
    for name, content in pkg_config_files().items():
        (prefix / 'lib/pkgconfig' / name).write_bytes(content)
    runtime = B.require_file(repo / 'build/android-icu' / I.ARTIFACTS[1], 'Verified ICU C++ runtime')
    if B.fingerprint(runtime) != B.fingerprint(ndk_tools['runtime']):
        raise BuildError('Selected NDK runtime differs from the verified ICU stage.')
    (root / ARTIFACTS[1]).write_bytes(runtime.read_bytes())
    save_prefix_state(root)
    mapping = B.require_file(output / 'probe.map', 'Boost linker map').read_text(encoding='utf-8')
    for name in (*REQUIRED_ARCHIVES, *I.REQUIRED_ARCHIVES):
        if name + '(' not in mapping:
            raise BuildError('Boost probe linker map lacks members from ' + name)
    validation, records = validate_prefix(root, plan)
    print(f'Validated {len(records)} installed files and 93 Android objects.', flush=True)
    verify_sources(root, marker)
    if any(B.fingerprint(repo / name) != value for name,value in inputs.items()) or prior_inputs(repo) != prior:
        raise BuildError('Boost inputs or prior ICU files changed during compilation; no receipt written.')
    receipt = {'schema_version': 1, 'kind': KIND, 'profile': profile,
               'inputs': {'project_files': inputs, 'archives': pins, 'prerequisites': prior}, 'tools': versions,
               'artifacts': {name: records[name] for name in ARTIFACTS}, 'prefix_files': records,
               'validation': validation, 'device_executed': False}
    with receipt_path.open('xb') as stream: stream.write(B.json_bytes(receipt))
    print('ANDROID BOOST BUILD PASS', flush=True)
    print(f'Prefix: {prefix}\nReceipt: {receipt_path}', flush=True)
    print('Offline Boost libraries built; Android execution remains untested.', flush=True)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('ndk', 'cmake', 'ninja'): parser.add_argument('--' + name, type=Path)
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--verify', '--verify-only', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.verify:
            verify_receipt(REPO); return 0
        if not 1 <= args.jobs <= 64 or any(getattr(args,name) is None for name in ('ndk','cmake','ninja')):
            raise BuildError('Build requires --ndk, --cmake, --ninja and --jobs between 1 and 64.')
        for name in ('ndk','cmake','ninja'): setattr(args, name, getattr(args,name).resolve())
        build(REPO,args)
    except (BuildError, OSError, ValueError, UnicodeError, tarfile.TarError, struct.error) as error:
        print('BLOCKED: ' + str(error), file=sys.stderr, flush=True); return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
