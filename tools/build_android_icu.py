#!/usr/bin/env python3
"""Build pinned static ICU78.3 and linked locale data offline for Androidx86_64/API23.

Native Python/CMake/Ninja drive NDK Clang directly on Windows and Linux. No host
ICU, Autoconf, MSYS conversion, data download or target execution is required.
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
SPEC = importlib.util.spec_from_file_location('wesnoth_icu_fonts', REPO / 'tools/build_android_fonts.py')
F = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(F)
M, B = F.M, F.B
BuildError = B.BuildError
KIND = 'wesnoth-android-icu'
MARKER = '.wesnoth-android-icu.json'
PREFIX_STATE = '.prefix-state.json'
ARCHIVE = 'icu4c-78.3-sources.tgz'
PROJECT_INPUTS = tuple(dict.fromkeys((*F.PROJECT_INPUTS, 'tools/build_android_icu.py',
    'tools/native-icu/build-plan.json', 'tools/native-icu/sources.json',
    'tools/native-icu/CMakeLists.txt', 'tools/native-icu/probe.cpp')))
ARTIFACTS = ('prefix/bin/wesnoth-icu-check', 'prefix/lib/libc++_shared.so')
REQUIRED_ARCHIVES = ('libicudata.a', 'libicuuc.a', 'libicui18n.a')
REQUIRED_HEADERS = ('unicode/utypes.h', 'unicode/uvernum.h', 'unicode/uchar.h', 'unicode/normalizer2.h',
                    'unicode/normlzr.h', 'unicode/coll.h', 'unicode/numfmt.h', 'unicode/brkiter.h', 'unicode/regex.h')
REQUIRED_PC = ('icu-data.pc', 'icu-uc.pc', 'icu-i18n.pc')


def pin(data):
    return {'bytes': len(data), 'sha256': B.sha256(data)}


def selected_inputs(repo):
    profile, _, _, _ = F.selected_inputs(repo)
    inputs = {name: B.fingerprint(B.require_file(repo / name, 'ICU input')) for name in PROJECT_INPUTS}
    plan = B.read_json(repo / 'tools/native-icu/build-plan.json')
    if (plan.get('schema_version') != 1 or plan.get('kind') != 'wesnoth-android-icu-plan'
            or plan.get('archive') != ARCHIVE or plan.get('source_dir') != 'icu'
            or plan.get('version') != '78.3' or plan.get('unicode_version') != '17.0'
            or plan.get('recipe') != 'cmake-upstream-source-lists-linked-data'
            or plan.get('source_counts') != {'common': 202, 'i18n': 254}
            or plan.get('static_libraries') != list(REQUIRED_ARCHIVES)
            or plan.get('data_symbol') != 'icudt78_dat' or plan.get('data_alignment') != 16
            or plan.get('public_headers', {}).get('count') != 203
            or not re.fullmatch('[0-9a-f]{64}', str(plan.get('public_headers', {}).get('sha256', '')))
            or set(plan.get('sources', {})) != {'source/common/sources.txt', 'source/i18n/sources.txt', 'source/data/in/icudt78l.dat', 'LICENSE'}):
        raise BuildError('ICU recipe must retain the pinned upstream sources, complete data, static targets and license.')
    manifest = B.read_json(repo / 'tools/native-icu/sources.json')
    entries = manifest.get('archives', [])
    if (manifest.get('schema_version') != 1 or manifest.get('kind') != 'wesnoth-android-icu-sources'
            or len(entries) != 1 or entries[0].get('name') != ARCHIVE or entries[0].get('version') != '78.3'
            or entries[0].get('source_dir') != 'icu'):
        raise BuildError('Expected exactly the pinned ICU78.3 source archive.')
    pins = {ARCHIVE: {key: entries[0].get(key) for key in ('bytes', 'sha256')}}
    for record in [*pins.values(), *plan['sources'].values()]:
        if (type(record.get('bytes')) is not int or not 0 < record['bytes'] < 40_000_000
                or re.fullmatch('[0-9a-f]{64}', str(record.get('sha256', ''))) is None):
            raise BuildError('ICU source pin has invalid size/hash.')
    return profile, inputs, plan, pins


def prior_inputs(repo):
    """Verify the existing font receipt using its unchanged parser and validators."""
    profile, inputs, _, pins = F.selected_inputs(repo)
    root = repo / 'build/android-fonts'
    B.reject_link(root.parent)
    B.reject_link(root)
    receipt = B.read_json(B.managed_path(root, 'receipt.json'))
    # validate_prefix uses the root only to reject absolute host paths in metadata;
    # no NDK tool or executable is accessed by this validator.
    validation, files = F.validate_prefix(root, {'cmake_toolchain': repo / 'unused-ndk/build/cmake/android.toolchain.cmake'})
    if (receipt.get('schema_version') != 1 or receipt.get('kind') != F.KIND or receipt.get('profile') != profile
            or receipt.get('inputs') != {'project_files': inputs, 'archives': pins}
            or receipt.get('validation') != validation or receipt.get('prefix_files') != files
            or receipt.get('artifacts') != {name: files[name] for name in F.ARTIFACTS}):
        raise BuildError('Completed font receipt/input/artifact hashes differ. Preserve prior files.')
    print('ANDROID FONTS ARTIFACTS VERIFIED', flush=True)
    return {'fonts': {'receipt': B.fingerprint(root / 'receipt.json'), 'prefix_files': files}}


def archive_files(data, plan):
    files, seen, spelling, total = {}, set(), {}, 0
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as archive:
        for entry in archive:
            name = entry.name.rstrip('/') if entry.isdir() else entry.name
            parts = M.safe_name(name)
            if parts[0] != 'icu' or name in seen:
                raise BuildError('Unexpected or duplicate ICU source member: ' + name)
            seen.add(name)
            if len(seen) > 3000:
                raise BuildError('ICU archive has too many members.')
            for end in range(1, len(parts) + 1):
                component = '/'.join(parts[:end])
                if spelling.setdefault(component.casefold(), component) != component:
                    raise BuildError('ICU source paths collide on Windows: ' + name)
            if entry.isdir():
                continue
            if not entry.isfile() or len(parts) < 2:
                raise BuildError('Linked or special ICU source member: ' + name)
            total += entry.size
            if total > 130_000_000 or entry.size > 40_000_000:
                raise BuildError('ICU source exceeds extraction size limits.')
            stream = archive.extractfile(entry)
            content = stream.read() if stream is not None else b''
            if len(content) != entry.size:
                raise BuildError('Incomplete ICU archive member: ' + name)
            files['sources/' + name] = content
    for name, expected in plan['sources'].items():
        if pin(files.get('sources/icu/' + name, b'')) != expected:
            raise BuildError('Pinned ICU source list/data/license differs: ' + name)
    headers = {name.split('/unicode/', 1)[1]: pin(content) for name, content in files.items()
               if name.endswith('.h') and any(name.startswith('sources/icu/source/' + part + '/unicode/') for part in ('common', 'i18n'))}
    header_inventory = {'unicode/' + name: record for name, record in headers.items()}
    if {'count': len(headers), 'sha256': B.sha256(B.json_bytes(header_inventory))} != plan['public_headers']:
        raise BuildError('ICU public header set differs from the complete release.')
    for component, count in plan['source_counts'].items():
        names = files['sources/icu/source/' + component + '/sources.txt'].decode().split()
        if len(names) != count or len(set(names)) != count:
            raise BuildError('ICU upstream source-list count/uniqueness differs.')
        for name in names:
            if not re.fullmatch('[a-zA-Z0-9_-]+[.]cpp', name) or not files.get('sources/icu/source/' + component + '/' + name):
                raise BuildError('ICU source list contains an invalid/missing source: ' + name)
    return files


def data_assembly(path):
    # Clang's integrated assembler accepts forward-slash native Windows paths,
    # including spaces, in the quoted incbin operand. No host assembler is used.
    name = path.as_posix()
    if any(c in name for c in ('"', '\\', '\n', '\r', '\x00')):
        raise BuildError('Unsupported ICU data path quoting.')
    return ('.section .rodata.icudt78_dat,"a",@progbits\n.p2align 4\n'
            '.globl icudt78_dat\n.type icudt78_dat,@object\nicudt78_dat:\n'
            f'.incbin "{name}"\n.size icudt78_dat, .-icudt78_dat\n'
            '.section .note.GNU-stack,"",@progbits\n').encode()


def prepare_sources(root, files, profile, pins):
    B.reject_link(root.parent)
    B.reject_link(root)
    generated = {'toolchain/icudata.S': data_assembly(root / 'sources/icu/source/data/in/icudt78l.dat')}
    records = lambda values: {name: pin(value) for name, value in sorted(values.items())}
    marker = {'schema_version': 1, 'kind': KIND, 'profile': profile, 'archives': pins,
              'source_files': records(files), 'generated_files': records(generated)}
    if root.exists():
        path = B.managed_path(root, MARKER)
        if not path.is_file() or B.read_json(path) != marker:
            raise BuildError('Existing ICU stage is unmanaged or uses different source/toolchain inputs. Nothing overwritten.')
        verify_sources(root, marker)
        for relative in ('build', 'prefix'):
            B.reject_link(root / relative)
            for child in (root / relative).rglob('*'):
                B.reject_link(child)
        return marker
    root.mkdir(parents=True)
    for name, content in {**files, **generated}.items():
        path = B.managed_path(root, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(content)
    (root / MARKER).write_bytes(B.json_bytes(marker))
    return marker


def verify_sources(root, marker):
    if (marker.get('schema_version') != 1 or marker.get('kind') != KIND
            or M.inventory(root, 'sources') != marker.get('source_files')
            or M.inventory(root, 'toolchain') != marker.get('generated_files')):
        raise BuildError('Managed ICU sources/toolchain changed. Nothing overwritten.')


def verify_prefix(root):
    files = M.inventory(root)
    state = B.managed_path(root, PREFIX_STATE)
    if state.exists():
        if B.read_json(state) != {'schema_version': 1, 'kind': KIND, 'prefix_files': files}:
            raise BuildError('Installed ICU prefix changed or installation interrupted. Nothing overwritten.')
    elif files:
        raise BuildError('Existing ICU prefix has no owned file record. Nothing overwritten.')
    return files


def save_prefix_state(root):
    files = M.inventory(root)
    (root / PREFIX_STATE).write_bytes(B.json_bytes({'schema_version': 1, 'kind': KIND, 'prefix_files': files}))
    return files


def pkg_config_files():
    result = {}
    for name, library, requires in [('icu-data', 'icudata', ''), ('icu-uc', 'icuuc', 'icu-data = 78.3'), ('icu-i18n', 'icui18n', 'icu-uc = 78.3')]:
        text = ('prefix=${pcfiledir}/../..\nlibdir=${prefix}/lib\nincludedir=${prefix}/include\n'
                f'Name: {name}\nDescription: ICU78.3 Android static library\nVersion: 78.3\n'
                f'Libs: -L${{libdir}} -l{library}\nCflags: -I${{includedir}} -DU_STATIC_IMPLEMENTATION\n')
        if requires:
            text += 'Requires.private: ' + requires + '\n'
        if name == 'icu-uc':
            text += 'Libs.private: -lc++_shared -ldl -lm\n'
        result[name + '.pc'] = text.encode()
    return result


def data_object_pin(archive):
    """Read the sole regular ELF object and its aligned linked data symbol."""
    data = archive.read_bytes()
    if not data.startswith(b'!<arch>\n'):
        raise BuildError('ICU data is not an ar archive.')
    objects, offset = [], 8
    while offset < len(data):
        header = data[offset:offset + 60]
        if len(header) != 60 or header[58:60] != b'`\n':
            raise BuildError('Malformed ICU data archive.')
        size = int(header[48:58]); offset += 60
        member = data[offset:offset + size]
        if len(member) != size:
            raise BuildError('Truncated ICU data archive.')
        if header[:16].strip() not in (b'/', b'//', b'/SYM64/'):
            objects.append(member)
        offset += size + size % 2
    if len(objects) != 1 or offset != len(data):
        raise BuildError('ICU data archive must contain exactly one ELF object.')
    obj = objects[0]
    if B.elf_header(obj) != 1:
        raise BuildError('ICU data must be an x86_64 ELF64 relocatable object.')
    shoff = struct.unpack_from('<Q', obj, 40)[0]
    shentsize, shnum = struct.unpack_from('<HH', obj, 58)
    if shentsize != 64 or shoff + shnum * 64 > len(obj):
        raise BuildError('Invalid ICU data ELF sections.')
    sections = [struct.unpack_from('<IIQQQQIIQQ', obj, shoff + n * 64) for n in range(shnum)]
    for section in sections:
        if section[1] != 2:
            continue
        if section[9] != 24 or section[6] >= len(sections):
            raise BuildError('Invalid ICU data symbol table.')
        strings = sections[section[6]]
        names = obj[strings[4]:strings[4] + strings[5]]
        for at in range(section[4], section[4] + section[5], 24):
            name, info, _, index, value, size = struct.unpack_from('<IBBHQQ', obj, at)
            if names[name:].split(b'\0', 1)[0] != b'icudt78_dat':
                continue
            if index >= len(sections):
                raise BuildError('Invalid ICU data section index.')
            segment = sections[index]
            if (info != 0x11 or segment[1] != 1 or segment[2] != 2 or segment[8] < 16
                    or value % 16 or value + size > segment[5] or segment[4] + segment[5] > len(obj)):
                raise BuildError('ICU data symbol must be a 16-byte aligned read-only global object.')
            return pin(obj[segment[4] + value:segment[4] + value + size])
    raise BuildError('ICU packaged data symbol is absent.')


def validate_prefix(root, plan):
    prefix = root / 'prefix'
    for name in REQUIRED_HEADERS:
        B.require_file(prefix / 'include' / name, 'ICU development header')
    headers = {path.relative_to(prefix / 'include').as_posix(): B.fingerprint(path)
               for path in sorted((prefix / 'include').rglob('*')) if path.is_file()}
    if {'count': len(headers), 'sha256': B.sha256(B.json_bytes(headers))} != plan['public_headers']:
        raise BuildError('Installed ICU headers differ from the complete release.')
    for name, expected in pkg_config_files().items():
        if B.require_file(prefix / 'lib/pkgconfig' / name, 'ICU metadata').read_bytes() != expected:
            raise BuildError('ICU development metadata differs or is not relocatable: ' + name)
    license_pin = B.fingerprint(B.require_file(prefix / 'share/licenses/wesnoth-icu/LICENSE', 'ICU complete license'))
    if license_pin != plan['sources']['LICENSE']:
        raise BuildError('ICU complete Unicode/third-party license differs.')
    static = {p.relative_to(prefix).as_posix(): M.inspect_static_archive(p) for p in sorted(prefix.rglob('*.a'))}
    expected = {'lib/libicudata.a': 1, 'lib/libicuuc.a': 202, 'lib/libicui18n.a': 254}
    if set(static) != set(expected) or any(static[name]['elf_object_count'] != count for name, count in expected.items()):
        raise BuildError('ICU archives do not contain the complete selected source lists.')
    data_pin = data_object_pin(prefix / 'lib/libicudata.a')
    if data_pin != plan['sources']['source/data/in/icudt78l.dat']:
        raise BuildError('ICU linked data bytes differ from the complete pinned package.')
    runtime = {name: B.inspect_shared_elf(B.require_file(root / name, 'ICU artifact').read_bytes()) for name in ARTIFACTS}
    probe = runtime[ARTIFACTS[0]]
    if (probe['interpreter'] != '/system/bin/linker64' or probe['android_api_notes'] != [23]
            or not {'libc.so', 'libc++_shared.so'}.issubset(probe['needed'])
            or not set(probe['needed']).issubset({'libc.so', 'libc++_shared.so', 'libm.so', 'libdl.so'})):
        raise BuildError('ICU probe must target Android API23 with only NDK system/C++ dependencies.')
    records = M.inventory(root)
    for name, record in records.items():
        if record['bytes'] == 0 or name.endswith('.la') or ((name.endswith('.so') or '.so.' in name) and name != ARTIFACTS[1]):
            raise BuildError('Unexpected installed ICU file: ' + name)
    return {'static_libraries': static, 'runtime': runtime, 'packaged_data': data_pin}, records


def verify_receipt(repo):
    profile, inputs, plan, pins = selected_inputs(repo)
    prior = prior_inputs(repo)
    root = repo / 'build/android-icu'
    B.reject_link(root.parent); B.reject_link(root)
    receipt = B.read_json(B.managed_path(root, 'receipt.json'))
    validation, files = validate_prefix(root, plan)
    if B.fingerprint(root / ARTIFACTS[1]) != B.fingerprint(repo / 'build/android-fonts' / ARTIFACTS[1]):
        raise BuildError('ICU runtime differs from the verified font runtime.')
    if (receipt.get('schema_version') != 1 or receipt.get('kind') != KIND or receipt.get('profile') != profile
            or receipt.get('inputs') != {'project_files': inputs, 'archives': pins, 'prerequisites': prior}
            or receipt.get('validation') != validation or receipt.get('prefix_files') != files
            or receipt.get('artifacts') != {name: files[name] for name in ARTIFACTS}):
        raise BuildError('ICU receipt/input/artifact hashes differ. Preserve changes before rebuilding.')
    print('ANDROID ICU ARTIFACTS VERIFIED', flush=True)
    return receipt


def build(repo, args):
    profile, inputs, plan, pins = selected_inputs(repo)
    prior = prior_inputs(repo)
    data = M.archive_data(repo, ARCHIVE, pins[ARCHIVE], repo / 'build/android-icu-sources')
    files = archive_files(data, plan)
    ndk_tools = B.ndk_paths(args.ndk, B.host_tag())
    versions = B.check_tools(args.cmake, args.ninja, ndk_tools)
    root = repo / 'build/android-icu'
    marker = prepare_sources(root, files, profile, pins)
    verify_prefix(root)
    receipt_path = B.managed_path(root, 'receipt.json')
    if receipt_path.exists():
        receipt_path.unlink()
    prefix = root / 'prefix'
    (prefix / 'lib/pkgconfig').mkdir(parents=True, exist_ok=True)
    env = M.isolated_environment(prefix)
    for name in ('CC', 'CXX', 'ASM', 'ASMFLAGS', 'AR', 'RANLIB', 'CMAKE_GENERATOR', 'CMAKE_GENERATOR_PLATFORM', 'CMAKE_GENERATOR_TOOLSET'):
        env.pop(name, None)
    save_prefix_state(root)
    output = root / 'build'
    M.run(M.cmake_arguments(args, ndk_tools, prefix, repo / 'tools/native-icu', output)
          + ['-DICU_SOURCE=' + (root / 'sources/icu').as_posix(), '-DICU_DATA_ASSEMBLY=' + (root / 'toolchain/icudata.S').as_posix()], env)
    M.run([args.cmake, '--build', output, '--parallel', str(args.jobs)], env)
    M.run([args.cmake, '--install', output], env)
    for name, content in pkg_config_files().items():
        (prefix / 'lib/pkgconfig' / name).write_bytes(content)
    runtime = B.require_file(repo / 'build/android-fonts' / ARTIFACTS[1], 'Verified C++ runtime')
    if B.fingerprint(runtime) != B.fingerprint(ndk_tools['runtime']):
        raise BuildError('Selected NDK C++ runtime differs from the verified font stage.')
    (root / ARTIFACTS[1]).write_bytes(runtime.read_bytes())
    save_prefix_state(root)
    mapping = B.require_file(output / 'probe.map', 'ICU linker map').read_text(encoding='utf-8')
    for name in REQUIRED_ARCHIVES:
        if name + '(' not in mapping:
            raise BuildError('ICU probe linker map lacks members from ' + name)
    validation, records = validate_prefix(root, plan)
    verify_sources(root, marker)
    if any(B.fingerprint(repo / name) != value for name, value in inputs.items()) or prior_inputs(repo) != prior:
        raise BuildError('ICU inputs or prior font files changed during compilation; no receipt written.')
    receipt = {'schema_version': 1, 'kind': KIND, 'profile': profile,
               'inputs': {'project_files': inputs, 'archives': pins, 'prerequisites': prior}, 'tools': versions,
               'artifacts': {name: records[name] for name in ARTIFACTS}, 'prefix_files': records,
               'validation': validation, 'device_executed': False}
    with receipt_path.open('xb') as stream:
        stream.write(B.json_bytes(receipt))
    print('ANDROID ICU BUILD PASS', flush=True)
    print(f'Prefix: {prefix}\nReceipt: {receipt_path}', flush=True)
    print('Offline ICU libraries and linked data built; Android execution remains untested.', flush=True)
    return receipt


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
            return 0
        if not 1 <= args.jobs <= 64 or any(getattr(args, name) is None for name in ('ndk', 'cmake', 'ninja')):
            raise BuildError('Build requires --ndk, --cmake, --ninja and --jobs between 1 and 64.')
        for name in ('ndk', 'cmake', 'ninja'):
            setattr(args, name, getattr(args, name).resolve())
        build(REPO, args)
    except (BuildError, OSError, ValueError, UnicodeError, tarfile.TarError, struct.error) as error:
        print('BLOCKED: ' + str(error), file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
