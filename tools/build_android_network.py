#!/usr/bin/env python3
"""Build pinned OpenSSL3.4.1/curl8.1.1 offline for Android x86_64/API23.

Upstream Perl/Make only generate OpenSSL files. Native CMake/Ninja run NDK
Clang directly. Existing fonts/runtime are verified without rebuilding them.
"""
import argparse
import importlib.util
import io
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tarfile

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('wesnoth_network_fonts', REPO / 'tools/build_android_fonts.py')
F = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(F)
M, B = F.M, F.B
BuildError = B.BuildError
KIND = 'wesnoth-android-network'
MARKER = '.wesnoth-android-network.json'
PREFIX_STATE = '.prefix-state.json'
ARCHIVES = {'openssl-3.4.1.tar.gz': 'openssl-3.4.1', 'curl-8.1.1.tar.xz': 'curl-8.1.1'}
PROJECT_INPUTS = tuple(dict.fromkeys((*F.PROJECT_INPUTS, 'tools/build_android_network.py',
    'tools/native-network/build-plan.json', 'tools/native-network/CMakeLists.txt',
    'tools/native-network/configure-openssl.sh', 'tools/native-network/export-openssl.pl',
    'tools/native-network/probe.cpp')))
ARTIFACTS = ('prefix/bin/wesnoth-network-check', 'prefix/lib/libc++_shared.so')
REQUIRED_ARCHIVES = ('libcrypto.a', 'libssl.a', 'libcurl.a')


def pin(data):
    return {'bytes': len(data), 'sha256': B.sha256(data)}


def selected_inputs(repo):
    profile = F.selected_inputs(repo)[0]
    inputs = {name: B.fingerprint(B.require_file(repo / name, 'Network input')) for name in PROJECT_INPUTS}
    plan = B.read_json(repo / 'tools/native-network/build-plan.json')
    if (plan.get('schema_version') != 1 or plan.get('kind') != KIND + '-plan'
            or plan.get('versions') != {'openssl': '3.4.1', 'curl': '8.1.1'}
            or plan.get('recipe') != 'openssl-upstream-generation-native-cmake-curl'
            or set(plan.get('openssl_graph', {})) != {'libcrypto', 'libssl'}
            or plan.get('object_counts') != {'libcrypto.a': 942, 'libssl.a': 90, 'libcurl.a': 165}
            or plan.get('public_headers', {}).get('count') != 153 or plan.get('prefix_file_count') != 164
            or len(plan.get('openssl_generated', [])) != 47):
        raise BuildError('Network recipe must retain the pinned libraries and generator route.')
    header_paths = plan.get('public_header_paths', {})
    if not isinstance(header_paths, dict) or set(header_paths) != {'openssl', 'curl'}:
        raise BuildError('Network recipe requires explicit OpenSSL/curl public header paths.')
    for component, count in (('openssl', 141), ('curl', 12)):
        names = header_paths[component]
        if (not isinstance(names, list) or len(names) != count
                or any(not isinstance(name, str) or not re.fullmatch(component + r'/[A-Za-z0-9_-]+[.]h', name) for name in names)
                or names != sorted(set(names)) or len({name.casefold() for name in names}) != count):
            raise BuildError('Unsafe, duplicate or incomplete explicit network header paths.')
    for target, graph in plan['openssl_graph'].items():
        for field in ('sources', 'includes', 'defines'):
            names = graph[field]
            if names != sorted(set(names)) or any(not re.fullmatch('[a-zA-Z0-9_/.-]+', n) or '..' in n.split('/') for n in names):
                raise BuildError('Unsafe or duplicate OpenSSL graph entry.')
        if len(graph['sources']) != {'libcrypto': 942, 'libssl': 90}[target]:
            raise BuildError('OpenSSL source count differs.')
    manifest = B.read_json(repo / 'tools/android-native-archives.json')
    pins = {}
    for name in ARCHIVES:
        entries = [e for e in manifest.get('archives', []) if e.get('name') == name]
        if len(entries) != 1:
            raise BuildError('Expected exactly one pinned network archive: ' + name)
        record = {key: entries[0].get(key) for key in ('bytes', 'sha256')}
        if type(record['bytes']) is not int or not 0 < record['bytes'] < 30_000_000 or not re.fullmatch('[0-9a-f]{64}', str(record['sha256'])):
            raise BuildError('Invalid network archive pin.')
        pins[name] = record
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


def selected_member(root, relative):
    parts = relative.split('/')
    if root.startswith('openssl'):
        return True
    return len(parts) == 1 or parts[0] in {'CMake', 'include', 'lib', 'scripts'}


def archive_files(data, source_root):
    files, seen, spelling, total = {}, set(), {}, 0
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as archive:
        for index, entry in enumerate(archive):
            if index > 30000: raise BuildError('Network archive exceeds member limit.')
            name = entry.name.rstrip('/') if entry.isdir() else entry.name
            parts = M.safe_name(name)
            if parts[0] != source_root: raise BuildError('Unexpected network archive root.')
            relative = '/'.join(parts[1:])
            if not selected_member(source_root, relative) or entry.isdir(): continue
            if name in seen or not entry.isfile(): raise BuildError('Duplicate, linked or special network member: ' + name)
            seen.add(name)
            for end in range(1, len(parts) + 1):
                component = '/'.join(parts[:end])
                if spelling.setdefault(component.casefold(), component) != component:
                    raise BuildError('Selected network paths collide on Windows: ' + name)
            total += entry.size
            if total > 100_000_000 or entry.size > 10_000_000: raise BuildError('Network source extraction size limit exceeded.')
            stream = archive.extractfile(entry)
            content = stream.read() if stream is not None else b''
            if len(content) != entry.size: raise BuildError('Incomplete network archive member.')
            files['sources/' + name] = content
    return files


def inventory_pin(files):
    return {'count': len(files), 'sha256': B.sha256(B.json_bytes(files))}


def prepare_sources(root, files, profile, pins):
    B.reject_link(root.parent); B.reject_link(root)
    marker = {'schema_version': 1, 'kind': KIND, 'profile': profile, 'archives': pins,
              'source_files': {name: pin(value) for name, value in sorted(files.items())}}
    if root.exists():
        path = B.managed_path(root, MARKER)
        if not path.is_file() or B.read_json(path) != marker:
            raise BuildError('Existing network stage is unmanaged or uses different sources. Nothing overwritten.')
        verify_sources(root, marker)
        for child in root.rglob('*'): B.reject_link(child)
        return marker
    root.mkdir(parents=True)
    for name, content in files.items():
        path = B.managed_path(root, name); path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (root / MARKER).write_bytes(B.json_bytes(marker))
    return marker


def verify_sources(root, marker):
    if M.inventory(root, 'sources') != marker['source_files']:
        raise BuildError('Managed network sources changed. Nothing overwritten.')


def verify_prefix(root):
    files = M.inventory(root)
    state = B.managed_path(root, PREFIX_STATE)
    if state.exists():
        if B.read_json(state) != {'schema_version': 1, 'kind': KIND, 'prefix_files': files}:
            raise BuildError('Installed network prefix changed or installation interrupted. Nothing overwritten.')
    elif files: raise BuildError('Existing network prefix has no owned file record. Nothing overwritten.')
    return files


def save_prefix_state(root):
    files = M.inventory(root)
    (root / PREFIX_STATE).write_bytes(B.json_bytes({'schema_version': 1, 'kind': KIND, 'prefix_files': files}))
    return files


def pkg_config_files():
    header = 'prefix=${pcfiledir}/../..\nlibdir=${prefix}/lib\nincludedir=${prefix}/include\n'
    result = {}
    for name, description, version, libs, requires, extra in [
        ('libcrypto', 'OpenSSL static crypto', '3.4.1', '-lcrypto', '', '-ldl'),
        ('libssl', 'OpenSSL static TLS', '3.4.1', '-lssl', 'libcrypto = 3.4.1', ''),
        ('openssl', 'OpenSSL static development', '3.4.1', '', 'libssl = 3.4.1 libcrypto = 3.4.1', ''),
        ('libcurl', 'curl static HTTP HTTPS', '8.1.1', '-lcurl', 'openssl = 3.4.1', '-lz')]:
        result[name + '.pc'] = (header + f'Name: {name}\nDescription: {description}\nVersion: {version}\nRequires.private: {requires}\nLibs: -L${{libdir}} {libs}\nLibs.private: {extra}\nCflags: -I${{includedir}}' + (' -DCURL_STATICLIB' if name == 'libcurl' else '') + '\n').encode()
    return result


def verify_public_headers(include_root, names, expected, description):
    # An explicit release list has identical semantics on Windows and POSIX.
    # glob('*.h') additionally includes OpenSSL's two VMS-only .H files on Windows.
    headers = {name: B.fingerprint(B.require_file(B.managed_path(include_root, name), description))
               for name in names}
    if inventory_pin(headers) != expected:
        raise BuildError(description + ' differ from the pinned generated/development headers.')
    return headers


def validate_prefix(root, plan):
    prefix = root / 'prefix'
    names = plan['public_header_paths']['openssl'] + plan['public_header_paths']['curl']
    verify_public_headers(prefix / 'include', names, plan['public_headers'], 'Installed network headers')
    for name, data in pkg_config_files().items():
        if B.require_file(prefix / 'lib/pkgconfig' / name, 'Network metadata').read_bytes() != data:
            raise BuildError('Network development metadata differs or is not relocatable.')
    for name, expected in plan['licenses'].items():
        if B.fingerprint(B.require_file(prefix / 'share/licenses/wesnoth-network' / name, 'Complete license')) != expected:
            raise BuildError('Installed network license differs.')
    archives = sorted(prefix.rglob('*.a'))
    if any(path.parent != prefix / 'lib' for path in archives):
        raise BuildError('Network static libraries must be installed directly in lib.')
    static = {p.name: M.inspect_static_archive(p) for p in archives}
    if set(static) != set(REQUIRED_ARCHIVES) or any(static[name]['elf_object_count'] != count for name, count in plan['object_counts'].items()):
        raise BuildError('Network archives do not contain the complete selected Android source lists.')
    runtime = {name: B.inspect_shared_elf(B.require_file(root / name, 'Network artifact').read_bytes()) for name in ARTIFACTS}
    probe = runtime[ARTIFACTS[0]]
    if (probe['interpreter'] != '/system/bin/linker64' or probe['android_api_notes'] != [23]
            or not {'libc.so', 'libc++_shared.so', 'libz.so'}.issubset(probe['needed'])
            or not set(probe['needed']).issubset({'libc.so', 'libc++_shared.so', 'libz.so', 'libm.so', 'libdl.so'})):
        raise BuildError('Network probe must use Android API23 and only selected NDK dependencies.')
    files = M.inventory(root)
    if len(files) != plan['prefix_file_count']:
        raise BuildError('Unexpected network prefix file count.')
    for name, record in files.items():
        if record['bytes'] == 0 or name.endswith('.la') or ((name.endswith('.so') or '.so.' in name) and name != ARTIFACTS[1]):
            raise BuildError('Unexpected installed network file: ' + name)
    return {'static_libraries': static, 'runtime': runtime}, files


def verify_receipt(repo):
    profile, inputs, plan, pins = selected_inputs(repo)
    prior = prior_inputs(repo)
    root = repo / 'build/android-network'
    B.reject_link(root.parent); B.reject_link(root)
    receipt = B.read_json(B.managed_path(root, 'receipt.json'))
    validation, files = validate_prefix(root, plan)
    if B.fingerprint(root / ARTIFACTS[1]) != B.fingerprint(repo / 'build/android-fonts/prefix/lib/libc++_shared.so'):
        raise BuildError('Network C++ runtime differs from verified fonts runtime.')
    if (receipt.get('schema_version') != 1 or receipt.get('kind') != KIND or receipt.get('profile') != profile
            or receipt.get('inputs') != {'project_files': inputs, 'archives': pins, 'prerequisites': prior}
            or receipt.get('validation') != validation or receipt.get('prefix_files') != files
            or receipt.get('artifacts') != {name: files[name] for name in ARTIFACTS}):
        raise BuildError('Network receipt/input/artifact hashes differ. Preserve changes before rebuilding.')
    print('ANDROID NETWORK ARTIFACTS VERIFIED', flush=True)
    return receipt


def generation_binding(repo, plan):
    return {'scripts': {name: B.fingerprint(repo / 'tools/native-network' / name)
                        for name in ('configure-openssl.sh', 'export-openssl.pl')},
            'recipe': {name: plan[name] for name in ('configure_patch', 'openssl_graph', 'openssl_headers', 'openssl_generated', 'public_header_paths')},
            'macro_flags': ['--target=x86_64-linux-android23', '-dM', '-E', '-x', 'c'],
            'ndk_revision': '26.3.11579264'}


def verify_generated(repo, root, plan):
    work = root / 'work/openssl-3.4.1'
    state = root / '.generated-state.json'
    if not work.exists():
        if state.exists(): raise BuildError('Orphaned OpenSSL generation state. Nothing overwritten.')
        return False
    if not state.exists() or B.read_json(state) != {'inputs': generation_binding(repo, plan), 'files': M.inventory(root, 'work')}:
        raise BuildError('OpenSSL generation area/recipe changed or interrupted. Preserve it before retrying.')
    if B.fingerprint(work / 'Configure') != plan['configure_patch']['after'] or B.read_json(work / 'portable-library-graph.json') != plan['openssl_graph']:
        raise BuildError('Existing OpenSSL patched Configure/source graph differs.')
    verify_public_headers(work / 'include', plan['public_header_paths']['openssl'], plan['openssl_headers'],
                          'Existing generated OpenSSL public headers')
    return True


def generate_openssl(repo, root, args, env, plan, ndk_tools):
    work = root / 'work/openssl-3.4.1'
    state = root / '.generated-state.json'
    if verify_generated(repo, root, plan):
        print('Verified existing OpenSSL generation; no regeneration needed.', flush=True)
        return work
    shutil.copytree(root / 'sources/openssl-3.4.1', work)
    original = (work / 'Configure').read_bytes()
    needle = b'open(PIPE, "$cc -dM -E -x c /dev/null 2>&1 |");'
    replacement = b'open(PIPE, "<", "wesnoth-ndk-predefined.txt") or die "Missing native NDK macro input";'
    if pin(original) != plan['configure_patch']['before'] or original.count(needle) != 1:
        raise BuildError('Pinned OpenSSL Configure macro-probe location differs.')
    patched = original.replace(needle, replacement)
    if pin(patched) != plan['configure_patch']['after']:
        raise BuildError('Pinned OpenSSL Configure macro-probe patch differs.')
    (work / 'Configure').write_bytes(patched)
    empty = work / 'wesnoth-empty.c'
    empty.write_bytes(b'')
    compiler = ndk_tools['clang']
    macro_path = work / 'wesnoth-ndk-predefined.txt'
    result = subprocess.run([str(compiler), '--target=x86_64-linux-android23', '-dM', '-E', '-x', 'c', str(empty), '-o', str(macro_path)], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise BuildError('Native NDK macro detection failed: ' + result.stderr.decode('utf-8', 'replace'))
    macros = macro_path.read_text(encoding='utf-8')
    if not all(line in macros.splitlines() for line in ['#define __ANDROID__ 1', '#define __ANDROID_API__ __ANDROID_MIN_SDK_VERSION__', '#define __ANDROID_MIN_SDK_VERSION__ 23', '#define __x86_64__ 1']):
        raise BuildError('Native NDK macro input does not identify Android x86_64/API23.')
    if os.name == 'nt':
        if not args.msys_root: raise BuildError('Windows generation requires --msys-root for existing Perl/Make/Bash.')
        bash = B.require_file(args.msys_root / 'usr/bin/bash.exe', 'MSYS Bash')
        for name in ('perl.exe', 'make.exe', 'cygpath.exe'):
            B.require_file(args.msys_root / 'usr/bin' / name, 'MSYS generator')
    else:
        bash = Path('/usr/bin/bash')
    M.run([bash, '--noprofile', '--norc', (repo / 'tools/native-network/configure-openssl.sh').as_posix(), args.ndk, work, repo / 'tools/native-network', str(args.jobs)], env)
    graph = B.read_json(work / 'portable-library-graph.json')
    if graph != plan['openssl_graph']: raise BuildError('OpenSSL generated source graph differs from audited upstream Android graph.')
    if (work / 'generated-targets.txt').read_text(encoding='utf-8').splitlines() != plan['openssl_generated']:
        raise BuildError('OpenSSL generated target list differs.')
    for name in plan['openssl_generated']: B.require_file(work / name, 'Generated OpenSSL input')
    verify_public_headers(work / 'include', plan['public_header_paths']['openssl'], plan['openssl_headers'],
                          'OpenSSL generated public headers')
    state.write_bytes(B.json_bytes({'inputs': generation_binding(repo, plan), 'files': M.inventory(root, 'work')}))
    return work


def build(repo, args):
    profile, inputs, plan, pins = selected_inputs(repo)
    prior = prior_inputs(repo)
    files = {}
    for name, source in ARCHIVES.items():
        extracted = archive_files(M.archive_data(repo, name, pins[name]), source)
        records = {k: pin(v) for k, v in extracted.items()}
        if inventory_pin(records) != plan['source_inventories'][source]:
            raise BuildError('Selected network archive inventory differs.')
        files.update(extracted)
    ndk_tools = B.ndk_paths(args.ndk, B.host_tag())
    versions = B.check_tools(args.cmake, args.ninja, ndk_tools)
    root = repo / 'build/android-network'
    marker = prepare_sources(root, files, profile, pins)
    verify_prefix(root)
    verify_generated(repo, root, plan)
    receipt_path = B.managed_path(root, 'receipt.json')
    if receipt_path.exists(): receipt_path.unlink()
    prefix = root / 'prefix'
    (prefix / 'lib/pkgconfig').mkdir(parents=True, exist_ok=True)
    env = M.isolated_environment(prefix)
    for name in ('CC', 'CXX', 'ASM', 'ASMFLAGS', 'AR', 'RANLIB', 'CMAKE_GENERATOR', 'CMAKE_GENERATOR_PLATFORM', 'CMAKE_GENERATOR_TOOLSET', 'BASH_ENV', 'ENV', 'PERL5LIB', 'PERL5OPT', 'PERLLIB', 'MAKEFLAGS', 'MFLAGS', 'OPENSSL_LOCAL_CONFIG_DIR', 'PERL', 'HASHBANGPERL', 'CROSS_SYSROOT', 'CROSS_COMPILE', 'ARFLAGS', 'RC', 'WINDRES', 'RCFLAGS', 'LDLIBS', 'BUILDFILE', 'CPPDEFINES', 'CPPINCLUDES', 'MAKEFILES', 'GNUMAKEFLAGS', 'AS', 'ASFLAGS', 'CPP', 'LD', 'MT', 'MTFLAGS', 'RM', 'OBJCOPY'):
        env.pop(name, None)
    for name in list(env):
        if name.startswith('__CNF_'): env.pop(name)
    save_prefix_state(root)
    work = generate_openssl(repo, root, args, env, plan, ndk_tools)
    output = root / 'build'
    configure = M.cmake_arguments(args, ndk_tools, prefix, repo / 'tools/native-network', output) + ['-DOPENSSL_SOURCE=' + work.as_posix(), '-DCURL_SOURCE=' + (root / 'sources/curl-8.1.1').as_posix()]
    M.run(configure + ['-DNETWORK_WITH_CURL=OFF'], env)
    M.run([args.cmake, '--build', output, '--parallel', str(args.jobs)], env)
    M.run([args.cmake, '--install', output], env)
    save_prefix_state(root)
    M.run(configure + ['-DNETWORK_WITH_CURL=ON'], env)
    M.run([args.cmake, '--build', output, '--parallel', str(args.jobs)], env)
    M.run([args.cmake, '--install', output], env)
    for name, content in pkg_config_files().items(): (prefix / 'lib/pkgconfig' / name).write_bytes(content)
    runtime = B.require_file(repo / 'build/android-fonts/prefix/lib/libc++_shared.so', 'Verified fonts runtime')
    if B.fingerprint(runtime) != B.fingerprint(ndk_tools['runtime']): raise BuildError('NDK runtime differs from the verified fonts stage.')
    (root / ARTIFACTS[1]).write_bytes(runtime.read_bytes())
    save_prefix_state(root)
    mapping = B.require_file(output / 'probe.map', 'Network linker map').read_text(encoding='utf-8')
    for name in REQUIRED_ARCHIVES:
        if name + '(' not in mapping: raise BuildError('Network probe linker map lacks members from ' + name)
    validation, records = validate_prefix(root, plan)
    verify_sources(root, marker)
    if any(B.fingerprint(repo / name) != value for name, value in inputs.items()) or prior_inputs(repo) != prior:
        raise BuildError('Network inputs/prior fonts changed during compilation; no receipt written.')
    receipt = {'schema_version': 1, 'kind': KIND, 'profile': profile,
               'inputs': {'project_files': inputs, 'archives': pins, 'prerequisites': prior}, 'tools': versions,
               'artifacts': {name: records[name] for name in ARTIFACTS}, 'prefix_files': records,
               'validation': validation, 'device_executed': False}
    receipt_path.write_bytes(B.json_bytes(receipt))
    print(f'Validated {len(records)} installed files and {sum(plan["object_counts"].values())} Android objects.', flush=True)
    print('ANDROID NETWORK BUILD PASS', flush=True)
    print(f'Prefix: {prefix}\nReceipt: {receipt_path}\nOffline libraries built; Android execution remains untested.', flush=True)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('ndk', 'cmake', 'ninja', 'msys-root'): parser.add_argument('--' + name, type=Path)
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--verify', '--verify-only', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.verify:
            verify_receipt(REPO); return 0
        if not 1 <= args.jobs <= 64 or any(getattr(args, name) is None for name in ('ndk', 'cmake', 'ninja')):
            raise BuildError('Build requires --ndk, --cmake, --ninja and --jobs between 1 and 64.')
        for name in ('ndk', 'cmake', 'ninja', 'msys_root'):
            if getattr(args, name) is not None: setattr(args, name, getattr(args, name).resolve())
        build(REPO, args)
    except (BuildError, OSError, ValueError, UnicodeError, tarfile.TarError, struct.error) as error:
        print('BLOCKED: ' + str(error), file=sys.stderr, flush=True); return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
