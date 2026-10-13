#!/usr/bin/env python3
"""Package the completed Wesnoth engine and verified offline data manifest.

No native compilation or downloads occur. Debug information is removed only
from new stage-owned copies; the completed engine/dependency stages stay intact.
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
import tarfile
import uuid
import zipfile

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]


def helper(name):
    spec = importlib.util.spec_from_file_location('game_' + name, REPO / 'tools' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


E = helper('build_android_engine')
S = helper('build_android_sdl_check')
D = helper('prepare_android_game_data')
B, N, A = E.B, E.N, E.A
BuildError = B.BuildError
KIND = 'wesnoth-android-game'
MARKER = '.wesnoth-android-game.json'
LOCK = '.wesnoth-game-operation.json'
PACKAGE = 'org.wesnoth.offline'
ACTIVITY = PACKAGE + '.OfflineInitActivity'
GAME_ACTIVITY = PACKAGE + '.OfflineGameActivity'
APK = 'app/build/outputs/apk/debug/app-debug.apk'
JNI_ROOT = 'app/src/main/jniLibs/x86_64/'
LIBRARIES = ('libmain.so', 'libSDL3.so', 'libSDL3_image.so', 'libSDL3_mixer.so', 'libc++_shared.so')
ARTIFACTS = (APK,) + tuple(JNI_ROOT + name for name in LIBRARIES)
MANIFEST = 'gamedata-files.jsonl'
MANIFEST_HASH = 'd805fd055b263a5b09cd19576dc9df83c64e47bbcf7821cbb6550b686a74e53c'
ARCHIVE = {'name': 'SDL3-3.4.2.tar.gz', 'bytes': 15575883,
           'sha256': 'ef39a2e3f9a8a78296c40da701967dd1b0d0d6e267e483863ce70f8a03b4050c'}
TEMPLATES = {
    'settings.gradle': 'settings.gradle', 'build.gradle': 'build.gradle',
    'app.build.gradle': 'app/build.gradle', 'gradle.properties': 'gradle.properties',
    'AndroidManifest.xml': 'app/src/main/AndroidManifest.xml',
    **{name + '.java': 'app/src/main/java/org/wesnoth/offline/' + name + '.java'
       for name in ('OfflineInitActivity', 'OfflineGameActivity', 'OfflineDataInstaller')},
}
PROJECT_INPUTS = tuple(dict.fromkeys((*E.PROJECT_INPUTS, *S.PROJECT_INPUTS,
    'tools/prepare_android_game_data.py', 'tools/verify_game_data.py',
    'tools/gamedata-1.19.26.reference.json', 'tools/build_android_game.py',
    'tools/native-game/build-plan.json', *(str('tools/native-game/' + name) for name in TEMPLATES))))


def checked(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except (E.BuildError, S.BuildError, N.BuildError, A.BuildError, D.BuildError, D.V.VerificationError) as error:
        raise BuildError(str(error)) from error


def safe(path):
    return checked(N.ninja_path, path)


def regular(path, label):
    path = safe(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or not info.st_size:
        raise BuildError(label + ' must be a nonempty regular file: ' + str(path))
    return path


def fingerprint(path):
    path = regular(path, 'Game input')
    before = path.stat()
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while data := stream.read(1024 * 1024):
            digest.update(data)
        after = os.fstat(stream.fileno())
    current = safe(path).stat()
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
    if identity(before) != identity(after) or identity(before) != identity(current):
        raise BuildError('Game input changed while being read: ' + str(path))
    return {'bytes': before.st_size, 'sha256': digest.hexdigest()}


def write_new(path, data):
    safe(path.parent).mkdir(parents=True, exist_ok=True)
    # Project/receipt/index files are bounded; reuse the established exclusive
    # creation and Windows sharing-retry primitive for these owned small files.
    checked(N.ninja_write_new, path, data)


def expected_plan():
    return {'schema_version': 1, 'kind': KIND + '-plan', 'package_name': PACKAGE,
            'version_name': '1.19.26', 'version_code': 1192600, 'activity': ACTIVITY,
            'game_activity': GAME_ACTIVITY, 'abi': 'x86_64', 'min_sdk': 23, 'target_sdk': 35,
            'sdl_version': '3.4.2', 'strip_mode': '--strip-debug', 'native_libraries': list(LIBRARIES),
            'manifest_asset': MANIFEST, 'manifest_sha256': MANIFEST_HASH,
            'data_files': 26748, 'data_bytes': 726207542,
            'obb_name': 'main.1192600.org.wesnoth.offline.obb', 'sdl_archive': ARCHIVE}


def selected_inputs(repo):
    profile, _, wrapper = checked(S.selected_inputs, repo)
    if B.read_json(regular(repo / 'tools/native-game/build-plan.json', 'Game plan')) != expected_plan():
        raise BuildError('The game packaging plan differs from the selected offline profile.')
    files = {name: fingerprint(repo / name) for name in PROJECT_INPUTS}
    manifest = B.read_json(repo / 'tools/android-native-archives.json')
    pins = [{key: entry.get(key) for key in ARCHIVE} for entry in manifest.get('archives', [])
            if entry.get('name') == ARCHIVE['name']]
    if pins != [ARCHIVE]:
        raise BuildError('SDL Java glue archive differs from the recorded 3.4.2 pin.')
    return profile, {'project_files': files, 'sdl_archive': ARCHIVE,
                     'wrapper_sha256': S.WRAPPER_HASH}, wrapper


def prior_inputs(repo):
    print('Checking completed game engine and offline data prerequisites...', flush=True)
    checked(E.verify_receipt, repo)
    checked(D.verify_receipt, repo)
    manifest = fingerprint(repo / 'build/android-game-data' / MANIFEST)
    if manifest['sha256'] != MANIFEST_HASH:
        raise BuildError('Offline data index differs from its independent trusted pin.')
    return {'engine_receipt': fingerprint(repo / 'build/android-engine/receipt.json'),
            'data_receipt': fingerprint(repo / 'build/android-game-data/receipt.json'),
            'manifest': manifest}


def generated_files(repo, profile, wrapper, source_files):
    files = {}
    for template, output in TEMPLATES.items():
        content = regular(repo / 'tools/native-game' / template, 'Game template').read_text(encoding='utf-8')
        for key, value in profile.items():
            content = content.replace('@' + key.upper() + '@', str(value))
        if re.search(r'@[A-Z_]+@', content):
            raise BuildError('Unresolved game template setting: ' + template)
        files[output] = content.encode('utf-8')
    prefix = 'sources/' + S.SDL_ROOT + '/android-project/app/src/main/java/org/libsdl/app/'
    java = {name.removeprefix(prefix): data for name, data in source_files.items()
            if name.startswith(prefix) and name.endswith('.java')}
    if set(java) != S.JAVA_NAMES:
        raise BuildError('Pinned SDL archive must provide exactly eleven Java glue files.')
    files.update({'app/src/main/java/org/libsdl/app/' + name: data for name, data in java.items()})
    files['app/src/main/assets/' + MANIFEST] = regular(repo / 'build/android-game-data' / MANIFEST, 'Data index').read_bytes()
    if B.sha256(files['app/src/main/assets/' + MANIFEST]) != MANIFEST_HASH:
        raise BuildError('Game data index changed before project preparation.')
    files['gradle/wrapper/gradle-wrapper.jar'] = wrapper
    files['gradle/wrapper/gradle-wrapper.properties'] = (
        'distributionBase=GRADLE_USER_HOME\ndistributionPath=wrapper/dists\n'
        f"distributionUrl=https\\://services.gradle.org/distributions/gradle-{profile['gradle_version']}-bin.zip\n"
        f"distributionSha256Sum={profile['gradle_sha256']}\n"
        'networkTimeout=10000\nvalidateDistributionUrl=true\n'
        'zipStoreBase=GRADLE_USER_HOME\nzipStorePath=wrapper/dists\n').encode('ascii')
    return files


def verify_owned(root, marker, allow_missing=False):
    safe(root)
    if (not isinstance(marker, dict) or marker.get('kind') != KIND or marker.get('schema_version') != 1
            or not isinstance(marker.get('files'), dict)):
        raise BuildError('Existing game stage ownership marker is invalid.')
    for name, expected in marker['files'].items():
        if (not isinstance(name, str) or name.startswith('/') or '\\' in name or ':' in name
                or any(part in ('', '.', '..') for part in name.split('/'))):
            raise BuildError('Unsafe owned game project path.')
        path = safe(root / name)
        if not path.exists() and allow_missing:
            continue
        if fingerprint(path) != expected:
            raise BuildError('Owned game project input changed: ' + name)
    for path in root.rglob('*'):
        safe(path)
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative.startswith(('app/src/', 'gradle/')):
            if relative not in marker['files'] and relative not in ARTIFACTS[1:]:
                raise BuildError('Unexpected game source/payload file: ' + relative)


def prepare(root, files, profile, inputs):
    marker = {'schema_version': 1, 'kind': KIND, 'profile': profile, 'inputs': inputs,
              'files': {name: {'bytes': len(data), 'sha256': B.sha256(data)} for name, data in sorted(files.items())}}
    safe(root)
    if root.exists():
        if B.read_json(regular(root / MARKER, 'Game ownership marker')) != marker:
            raise BuildError('Existing game stage has different inputs. Preserve it and send the log.')
        verify_owned(root, marker, allow_missing=True)
    else:
        root.mkdir()
        write_new(root / MARKER, B.json_bytes(marker))
    for name, data in files.items():
        path = safe(root / name)
        if not path.exists():
            write_new(path, data)
    verify_owned(root, marker)
    return marker


@contextmanager
def operation(repo):
    directory = safe(repo / 'build')
    directory.mkdir(exist_ok=True)
    path = directory / LOCK
    content = B.json_bytes({'kind': KIND, 'pid': os.getpid(), 'token': uuid.uuid4().hex})
    try:
        write_new(path, content)
    except FileExistsError as error:
        raise BuildError('A game build operation lock exists. Preserve it and send the log.') from error
    try:
        yield
    finally:
        checked(N.ninja_unlink, path, content)


def loadable_signature(data):
    """Compare runtime bytes while allowing only stripped section bookkeeping."""
    if len(data) < 64 or data[:7] != b'\x7fELF\x02\x01\x01':
        raise BuildError('Native payload lacks an ELF64 header.')
    phoff = struct.unpack_from('<Q', data, 32)[0]
    phsize, count = struct.unpack_from('<HH', data, 54)
    if phsize != 56 or not 1 <= count <= 256 or phoff + count * phsize > len(data):
        raise BuildError('Native payload has an invalid program header table.')
    signature = []
    for index in range(count):
        record = struct.unpack_from('<IIQQQQQQ', data, phoff + index * phsize)
        tag, flags, offset, virtual, physical, length, memory, alignment = record
        if tag == 1:
            if offset + length > len(data):
                raise BuildError('Native loadable segment extends outside its file.')
            content = memoryview(data)[offset:offset + length]
            # Avoid copying the large unstripped debug tail. Only the first
            # loadable segment can include these section-only header fields.
            if offset < 64 and offset + length > 40:
                content = bytearray(content)
                for start, end in ((40, 48), (58, 64)):
                    first, last = max(start, offset), min(end, offset + length)
                    if first < last:
                        content[first - offset:last - offset] = bytes(last - first)
            signature.append({'program_header': list(record),
                              'sha256': B.sha256(content)})
    if not signature:
        raise BuildError('Native payload has no loadable segments.')
    return signature


def library_validation(path, source, name):
    data = regular(path, 'Packaged native library').read_bytes()
    original = regular(source, 'Completed engine library').read_bytes()
    signature = loadable_signature(data)
    if signature != loadable_signature(original):
        raise BuildError('Stripping changed runtime bytes of the completed engine: ' + name)
    elf = checked(A.inspect_elf, data, runtime=name == 'libc++_shared.so',
                  required_exports=('SDL_main',) if name == 'libmain.so' else ())
    return {'elf': elf, 'loadable_segments': signature, 'source': fingerprint(source)}


def prepare_libraries(repo, root, strip):
    validation = {}
    for name in LIBRARIES:
        source = regular(repo / 'build/android-engine/prefix/lib' / name, 'Completed native engine library')
        destination = safe(root / JNI_ROOT / name)
        record_path = root / 'native-records' / (name + '.json')
        if record_path.exists():
            record = B.read_json(regular(record_path, 'Native packaging record'))
            detail = library_validation(destination, source, name)
            if record != {'artifact': fingerprint(destination), 'validation': detail}:
                raise BuildError('Previously packaged native library changed: ' + name)
        else:
            if destination.exists():
                raise BuildError('An unfinished/unrecorded native copy exists. Preserve it and send the log: ' + name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            # LLVM reads the immutable engine library and writes a new owned
            # destination. No original is passed as a mutation target.
            checked(S.run, [strip, '--strip-debug', '-o', destination, source])
            detail = library_validation(destination, source, name)
            write_new(record_path, B.json_bytes({'artifact': fingerprint(destination), 'validation': detail}))
        validation[name] = detail
    return validation


def inspect_apk(apk, root):
    regular(apk, 'Game APK')
    with zipfile.ZipFile(apk) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise BuildError('Game APK contains duplicate ZIP entries.')
        if any(name.startswith('/') or '\\' in name or any(part in ('', '.', '..')
               for part in name.rstrip('/').split('/')) for name in names):
            raise BuildError('Game APK contains an unsafe ZIP entry.')
        for name in ('AndroidManifest.xml', 'classes.dex'):
            if name not in names or not archive.getinfo(name).file_size:
                raise BuildError('Game APK lacks required Android content: ' + name)
        libraries = {name for name in names if name.startswith('lib/') and not name.endswith('/')}
        if libraries != {'lib/x86_64/' + name for name in LIBRARIES}:
            raise BuildError('Game APK must contain exactly the five selected x86_64 native libraries.')
        for name in LIBRARIES:
            with archive.open('lib/x86_64/' + name) as stream:
                digest, size = hashlib.sha256(), 0
                while data := stream.read(1024 * 1024):
                    digest.update(data); size += len(data)
            if {'bytes': size, 'sha256': digest.hexdigest()} != fingerprint(root / JNI_ROOT / name):
                raise BuildError('APK native payload differs from its verified owned copy: ' + name)
        assets = {name for name in names if name.startswith('assets/') and not name.endswith('/')}
        if assets != {'assets/' + MANIFEST} or B.sha256(archive.read('assets/' + MANIFEST)) != MANIFEST_HASH:
            raise BuildError('APK offline data manifest differs from its independent trusted pin.')
    return {'package_name': PACKAGE, 'activity': ACTIVITY, 'game_activity': GAME_ACTIVITY,
            'version_code': 1192600, 'version_name': '1.19.26', 'abi': 'x86_64',
            'min_sdk': 23, 'target_sdk': 35, 'native_payload_matches': True,
            'manifest_sha256': MANIFEST_HASH, 'signature_verified_at_build': True}


def android_tools(sdk, java):
    java = regular(java, 'Java21 executable')
    version = checked(S.run, [java, '-version'], capture=True)
    if not re.search(r'version "21(?:[.\"]|$)', version):
        raise BuildError('The game build requires the existing Java21 runtime.')
    regular(sdk / 'platforms/android-35/android.jar', 'Android SDK35 platform')
    suffix = '.exe' if sys.platform == 'win32' else ''
    aapt2 = regular(sdk / 'build-tools/34.0.0' / ('aapt2' + suffix), 'SDK AAPT2')
    signer = regular(sdk / 'build-tools/34.0.0/lib/apksigner.jar', 'SDK APK signer')
    return aapt2, signer, version.splitlines()[0]


def strip_tool(ndk, tag):
    """Allow only the NDK's known Linux multicall alias, never input links.

    LLVM dispatches strip/objcopy behavior using argv[0], so execute the alias
    after validating it and fingerprint its regular target separately.
    """
    suffix = '.exe' if tag == 'windows-x86_64' else ''
    command = ndk / 'toolchains/llvm/prebuilt' / tag / 'bin' / ('llvm-strip' + suffix)
    safe(command.parent)
    target = command
    if stat.S_ISLNK(command.lstat().st_mode):
        target = command.parent / 'llvm-objcopy'
        if tag != 'linux-x86_64' or os.readlink(command) != 'llvm-objcopy':
            raise BuildError('NDK llvm-strip alias must target the regular llvm-objcopy in the same bin directory.')
    regular(target, 'NDK llvm-strip binary')
    if command != target and command.resolve(strict=True) != target:
        raise BuildError('NDK llvm-strip alias resolves outside its verified binary target.')
    return command, target


def verify_android_signature(apk, aapt2, java, signer):
    badging = checked(S.run, [aapt2, 'dump', 'badging', apk], capture=True)
    patterns = (rf"(?m)^package: name='{re.escape(PACKAGE)}' versionCode='1192600' versionName='1\.19\.26'",
                r"(?m)^sdkVersion:'23'$", r"(?m)^targetSdkVersion:'35'$",
                rf"(?m)^launchable-activity: name='{re.escape(ACTIVITY)}'",
                r"(?m)^native-code: 'x86_64'\s*$")
    if not all(re.search(pattern, badging) for pattern in patterns):
        raise BuildError('Game APK package/activity/version/API/ABI differs from its selected profile.')
    tree = checked(S.run, [aapt2, 'dump', 'xmltree', '--file', 'AndroidManifest.xml', apk], capture=True)
    if GAME_ACTIVITY not in tree and '.OfflineGameActivity' not in tree:
        raise BuildError('Packaged Android manifest lacks the SDL game activity.')
    checked(S.run, [java, '-jar', signer, 'verify', '--verbose', apk])


def artifact_validation(repo, root):
    native = {}
    for name in LIBRARIES:
        path = root / JNI_ROOT / name
        native[name] = library_validation(path, repo / 'build/android-engine/prefix/lib' / name, name)
        record = B.read_json(regular(root / 'native-records' / (name + '.json'), 'Native packaging record'))
        if record != {'artifact': fingerprint(path), 'validation': native[name]}:
            raise BuildError('Game native receipt differs: ' + name)
    return {'libraries': native, 'apk': inspect_apk(root / APK, root)}


def verify_receipt(repo, sdk=None, java=None):
    if safe(repo / 'build' / LOCK).exists():
        raise BuildError('A game build is active or interrupted. Preserve the operation lock.')
    profile, inputs, _ = selected_inputs(repo)
    inputs['prerequisites'] = prior_inputs(repo)
    root = safe(repo / 'build/android-game')
    marker = B.read_json(regular(root / MARKER, 'Game ownership marker'))
    verify_owned(root, marker)
    if marker.get('profile') != profile or marker.get('inputs') != inputs:
        raise BuildError('Game ownership inputs differ from the current prerequisites.')
    receipt = B.read_json(regular(root / 'receipt.json', 'Game APK receipt'))
    validation = artifact_validation(repo, root)
    artifacts = {name: fingerprint(root / name) for name in ARTIFACTS}
    if (not isinstance(receipt, dict) or receipt.get('schema_version') != 1 or receipt.get('kind') != KIND or receipt.get('profile') != profile
            or receipt.get('inputs') != inputs or receipt.get('artifacts') != artifacts
            or receipt.get('validation') != validation or receipt.get('device_executed') is not False
            or receipt.get('project_files') != marker['files']):
        raise BuildError('Game APK receipt, inputs, project or payload differs. Nothing sent to Android.')
    if (sdk is None) != (java is None):
        raise BuildError('Optional Android signature recheck requires both --sdk and --java.')
    if sdk is not None:
        aapt2, signer, _ = android_tools(sdk, java)
        verify_android_signature(root / APK, aapt2, java, signer)
    print('ANDROID GAME ARTIFACTS VERIFIED', flush=True)
    return receipt


def build(repo, args):
    root = safe(repo / 'build/android-game')
    if (root / 'receipt.json').exists():
        receipt = verify_receipt(repo, args.sdk, args.java)
        print('Existing signed APK reused without rebuilding.', flush=True)
        print('ANDROID GAME BUILD PASS', flush=True)
        return receipt
    profile, inputs, wrapper = selected_inputs(repo)
    inputs['prerequisites'] = prior_inputs(repo)
    pin, archive = checked(S.verified_archive, repo, args.archive_directory)
    if pin != ARCHIVE:
        raise BuildError('SDL archive differs from the selected Java glue pin.')
    generated = generated_files(repo, profile, wrapper, checked(S.archive_files, archive))
    ndk = safe(args.ndk)
    properties = regular(ndk / 'source.properties', 'NDK metadata').read_text(encoding='utf-8')
    if not re.search(r'(?m)^Pkg\.Revision\s*=\s*26\.3\.11579264\s*$', properties):
        raise BuildError('Use the selected NDK26.3.11579264 for stripping owned copies.')
    strip, strip_binary = strip_tool(ndk, B.host_tag())
    aapt2, signer, java_version = android_tools(args.sdk, args.java)
    launcher = checked(S.require_cached_gradle, args.gradle_user_home, profile)
    tools = {'java': java_version, 'strip': fingerprint(strip_binary),
             'strip_alias_target': strip_binary.name if strip_binary != strip else None, 'aapt2': fingerprint(aapt2),
             'apksigner': fingerprint(signer), 'gradle_launcher': fingerprint(launcher)}
    env = os.environ.copy()
    for name in tuple(env):
        if name in ('GRADLE_OPTS', 'JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS', 'JDK_JAVA_OPTIONS') or name.startswith('ORG_GRADLE_PROJECT_'):
            env.pop(name, None)
    env.update(JAVA_HOME=str(args.java.parent.parent), ANDROID_HOME=str(args.sdk),
               ANDROID_SDK_ROOT=str(args.sdk), GRADLE_USER_HOME=str(args.gradle_user_home))
    with operation(repo):
        marker = prepare(root, generated, profile, inputs)
        prepare_libraries(repo, root, strip)
        checked(S.run, [args.java, '-classpath', root / 'gradle/wrapper/gradle-wrapper.jar',
            'org.gradle.wrapper.GradleWrapperMain', '--offline', '--no-daemon', '--console=plain',
            '--project-dir', root, '--project-cache-dir', root / 'gradle-cache', ':app:assembleDebug'], env=env)
        validation = artifact_validation(repo, root)
        verify_android_signature(root / APK, aapt2, args.java, signer)
        verify_owned(root, marker)
        current = selected_inputs(repo)[1]
        current['prerequisites'] = prior_inputs(repo)
        if current != inputs:
            raise BuildError('Game packaging inputs changed during the build. No success receipt written.')
        receipt = {'schema_version': 1, 'kind': KIND, 'profile': profile, 'inputs': inputs,
                   'project_files': marker['files'], 'tools': tools, 'validation': validation,
                   'artifacts': {name: fingerprint(root / name) for name in ARTIFACTS}, 'device_executed': False}
        write_new(root / 'receipt.json', B.json_bytes(receipt))
    print('ANDROID GAME BUILD PASS', flush=True)
    print('APK: ' + str(root / APK), flush=True)
    print('Offline game APK packaged; asset import, title screen and gameplay require the Android test.', flush=True)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('ndk', 'sdk', 'java', 'archive-directory'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--gradle-user-home', type=Path, default=Path.home() / '.gradle')
    parser.add_argument('--verify', '--verify-only', action='store_true')
    args = parser.parse_args(argv)
    try:
        for name, value in vars(args).items():
            if isinstance(value, Path):
                setattr(args, name, safe(value))
        if args.verify:
            verify_receipt(REPO, args.sdk, args.java)
        else:
            if any(getattr(args, name) is None for name in ('ndk', 'sdk', 'java')):
                raise BuildError('Game packaging requires --ndk, --sdk and --java from the existing environment.')
            build(REPO, args)
    except (BuildError, OSError, ValueError, UnicodeError, struct.error, tarfile.TarError, zipfile.BadZipFile) as error:
        print('BLOCKED: ' + str(error), file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
