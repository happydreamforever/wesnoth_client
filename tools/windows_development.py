"""Explicit workstation checks and immutable-stage verification; never loads .env."""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import re
import shutil
import hashlib
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
STAGES = ('media', 'fonts', 'text-base', 'glib-deps', 'glib', 'text-render',
          'text-layout', 'icu', 'boost', 'network', 'engine', 'game')


def module(name):
    spec = importlib.util.spec_from_file_location('windows_development_' + name,
        ROOT / 'tools' / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def verify_stage(stage):
    if stage not in STAGES:
        raise ValueError('Unknown native stage.')
    if stage == 'media':
        module('android_engine_artifacts').verify_media(ROOT)
    elif stage == 'fonts':
        module('build_android_network').prior_inputs(ROOT)
    else:
        module('build_android_' + stage.replace('-', '_')).verify_receipt(ROOT)
    print('EXISTING DEVELOPMENT STAGE VERIFIED: ' + stage)


def certificate(apk, sdk, java):
    tool = sdk / 'build-tools/34.0.0/lib/apksigner.jar'
    result = subprocess.run([str(java), '-jar', str(tool), 'verify', '--verbose', '--print-certs', str(apk)],
                            capture_output=True, text=True, timeout=60)
    matches = re.findall(r'^Signer #1 certificate SHA-256 digest: ([0-9a-f]{64})$', result.stdout, re.M)
    if result.returncode or len(matches) != 1 or re.search(r'^Signer #[2-9]', result.stdout, re.M):
        raise ValueError('APK signature verification failed. Preserve the APK and original signer.')
    return matches[0]


def import_archives(root, source):
    """Supply legacy fixed-cache builders without changing their receipt-bound inputs."""
    target = root / 'packaging/android/download'
    records = json.loads((root / 'tools/android-native-archives.json').read_text())['archives']
    # Boost/network builders deliberately retain their existing fixed source-cache contract.
    # Select the exact filenames from those stages rather than guessing release filenames.
    boost = module('build_android_boost')
    network = module('build_android_network')
    names = {boost.BOOST_ARCHIVE, boost.B.ARCHIVE_NAME} | set(network.ARCHIVES)
    selected = [record for record in records if record['name'] in names]
    if len(selected) != len(names):
        raise ValueError('Pinned Boost/network archive inventory is incomplete.')
    def verified(path, record):
        if path.is_symlink() or not path.is_file() or path.stat().st_size != record['bytes']:
            raise ValueError('Required pinned source archive is missing or differs. Existing cache retained.')
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != record['sha256']:
            raise ValueError('Pinned source archive checksum differs. Existing cache retained.')
    # Verify every input and occupied destination before publishing any missing file.
    for record in selected:
        verified(source / record['name'], record)
        destination = target / record['name']
        if destination.exists() or destination.is_symlink():
            verified(destination, record)
    if target.is_symlink():
        raise ValueError('Pinned archive destination must be an ordinary directory.')
    target.mkdir(parents=True, exist_ok=True)
    for record in selected:
        destination = target / record['name']
        if not destination.exists():
            with (source / record['name']).open('rb') as incoming, destination.open('xb') as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            verified(destination, record)
    print('PINNED BOOST AND NETWORK SOURCE CACHE VERIFIED')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('check', 'verify-stage', 'verify-signer', 'import-archives'))
    parser.add_argument('--stage', choices=STAGES)
    for name in ('sdk', 'java', 'cmake', 'ninja', 'public-config', 'openssl', 'gradle-home',
                 'android-user-home', 'reference-apk', 'apk', 'archive-directory'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--server-ip')
    args = parser.parse_args()
    try:
        if args.command == 'import-archives':
            if not args.archive_directory:
                raise ValueError('Select the pinned archive cache.')
            import_archives(ROOT, args.archive_directory)
        elif args.command == 'verify-stage':
            verify_stage(args.stage)
        elif args.command == 'verify-signer':
            if not all((args.apk, args.reference_apk, args.sdk, args.java)):
                raise ValueError('Signer verification needs the rebuilt APK, original reference APK, SDK and Java.')
            if certificate(args.apk, args.sdk, args.java) != certificate(args.reference_apk, args.sdk, args.java):
                raise ValueError('APK signer differs from the original Windows reference; no installation is allowed.')
            print('ORIGINAL WINDOWS APK SIGNER VERIFIED')
        else:
            if sys.version_info[:3] != (3, 12, 10) or sys.maxsize <= 2**32:
                raise ValueError('Select the pinned 64-bit Python 3.12.10 executable.')
            if not all((args.sdk, args.java, args.cmake, args.ninja, args.public_config,
                        args.openssl, args.gradle_home, args.android_user_home, args.reference_apk, args.server_ip)):
                raise ValueError('Required development check paths are missing.')
            game = module('build_android_game')
            profile = game.B.read_json(ROOT / 'tools/android-build-profile.json')
            tools = game.B.ndk_paths(args.sdk / 'ndk/26.3.11579264', game.B.host_tag())
            game.B.check_tools(args.cmake, args.ninja, tools)
            game.android_tools(args.sdk, args.java)
            game.S.require_cached_gradle(args.gradle_home, profile)
            key = args.android_user_home / 'debug.keystore'
            if key.is_symlink() or not key.is_file() or key.stat().st_size == 0:
                raise ValueError('Original Windows debug.keystore is missing. Copy it privately from the old computer before building.')
            public = module('android_public_bundle')
            _, identity = public.public_profile(ROOT, args.public_config, args.openssl)
            if identity['address'] != args.server_ip:
                raise ValueError('The client .env server IP differs from the verified public profile. Select the server identity matching the new computer.')
            certificate(args.reference_apk, args.sdk, args.java)
            print('WINDOWS ANDROID DEVELOPMENT DEPENDENCIES VERIFIED')
            print('Pinned tools, offline Gradle, original signer presence, reference APK signature and public server identity verified.')
        return 0
    except Exception as error:
        # Third-party subprocess exceptions may contain unrelated environment data.
        text = str(error) if type(error) is ValueError else 'Development check failed; preserve completed stages and inspect the selected tools, inputs and public profile.'
        print('ERROR: ' + text, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
