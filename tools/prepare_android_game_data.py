#!/usr/bin/env python3
"""Create and verify a deterministic, entirely offline game-data OBB.

The generated JSONL is itself authenticated by the existing data-tree pin. It
is embedded in the APK so Android can verify each extracted file independently.
Original data and all earlier build stages are read-only.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_game_data as V

STAGE = Path('build/android-game-data')
MANIFEST = 'gamedata-files.jsonl'
PACKAGE = 'org.wesnoth.offline'
VERSION_CODE = 1192600
OBB = f'main.{VERSION_CODE}.{PACKAGE}.obb'
TREE_SHA256 = 'd805fd055b263a5b09cd19576dc9df83c64e47bbcf7821cbb6550b686a74e53c'
FILES = 26748
BYTES = 726207542
OWNER = '.wesnoth-game-data-stage.json'
OWNER_VALUE = {'schema': 1, 'kind': 'wesnoth-offline-game-data', 'package': PACKAGE}
PROJECT_INPUTS = ('tools/prepare_android_game_data.py', 'tools/verify_game_data.py',
                  'tools/gamedata-1.19.26.reference.json')


class BuildError(RuntimeError):
    pass


def safe_path(path, directory=False):
    path = Path(os.path.abspath(path))
    for parent in reversed(path.parents):
        if not stat.S_ISDIR(V.checked_stat(parent).st_mode):
            raise BuildError(f'Not a directory: {parent}')
    info = V.checked_stat(path)
    if directory != stat.S_ISDIR(info.st_mode):
        raise BuildError(f'Unexpected path type: {path}')
    return info


def sha_file(path):
    before = V.fingerprint(safe_path(path))
    digest = hashlib.sha256()
    with path.open('rb') as source:
        if V.fingerprint(os.fstat(source.fileno())) != before:
            raise BuildError(f'File changed while opening: {path}')
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
        if V.fingerprint(os.fstat(source.fileno())) != before:
            raise BuildError(f'File changed while reading: {path}')
    if V.fingerprint(safe_path(path)) != before:
        raise BuildError(f'File changed during verification: {path}')
    return {'path': path.name, 'bytes': before[3], 'sha256': digest.hexdigest()}


def inputs(repo):
    return {name: sha_file(repo / name)['sha256'] for name in PROJECT_INPUTS}


def reference(repo):
    path = repo / PROJECT_INPUTS[2]
    safe_path(path)
    value = json.loads(path.read_text(encoding='utf-8'))
    if any(value.get(k) != v for k, v in {
        'schema': 1, 'algorithm': V.ALGORITHM, 'core_version': '1.19.26',
        'files': FILES, 'bytes': BYTES, 'tree_sha256': TREE_SHA256,
    }.items()):
        raise BuildError('Existing game-data reference does not match the selected 1.19.26 pin.')
    return value


def read_json(path):
    safe_path(path)
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as dest:
        json.dump(value, dest, sort_keys=True, indent=2)
        dest.write('\n')


def check_owner(root):
    safe_path(root, directory=True)
    if read_json(root / OWNER) != OWNER_VALUE:
        raise BuildError(f'Unrecognized game-data stage; preserved: {root}')
    allowed = {OWNER, MANIFEST, OBB, 'receipt.json', '.operation.lock'}
    unexpected = set(p.name for p in root.iterdir()) - allowed
    if unexpected:
        raise BuildError(f'Unexpected stage files preserved: {sorted(unexpected)}')
    for path in root.iterdir():
        safe_path(path)


@contextmanager
def operation(root):
    lock = root / '.operation.lock'
    content = str(os.getpid()) + '\n'
    try:
        with lock.open('x', encoding='ascii') as handle:
            handle.write(content)
    except FileExistsError as exc:
        raise BuildError(f'Game-data operation already active or interrupted; preserve and report: {lock}') from exc
    identity = V.fingerprint(safe_path(lock))
    try:
        yield
    finally:
        if V.fingerprint(safe_path(lock)) != identity or lock.read_text(encoding='ascii') != content:
            raise BuildError('Game-data operation lock changed; replacement preserved for inspection.')
        lock.unlink()


def parse_manifest(path, expected):
    summary = sha_file(path)
    if summary['sha256'] != expected['tree_sha256']:
        raise BuildError('Generated data manifest does not match the trusted game-data tree SHA256.')
    records = []
    previous = ''
    seen = set()
    for line in path.read_bytes().splitlines(keepends=True):
        record = json.loads(line)
        if not isinstance(record, list) or len(record) != 3:
            raise BuildError('Invalid data manifest record.')
        name, size, digest = record
        if (not isinstance(name, str) or not name or name <= previous
                or type(size) is not int or size < 0
                or not isinstance(digest, str) or len(digest) != 64
                or any(c not in '0123456789abcdef' for c in digest)
                or line != V.canonical_record(name, size, digest)):
            raise BuildError('Noncanonical data manifest record.')
        for part in name.split('/'):
            if not part:
                raise BuildError('Empty data path component.')
            V.check_name(part)
        if name.casefold() in seen:
            raise BuildError('Case-colliding data manifest paths.')
        seen.add(name.casefold())
        previous = name
        records.append((name, size, digest))
    if len(records) != expected['files'] or sum(r[1] for r in records) != expected['bytes']:
        raise BuildError('Data manifest count or total bytes mismatch.')
    return records, summary


def verify_bundle(root, expected, progress=None):
    records, manifest = parse_manifest(root / MANIFEST, expected)
    path = root / OBB
    original = V.fingerprint(safe_path(path))
    with zipfile.ZipFile(path, 'r') as archive:
        entries = archive.infolist()
        if len(entries) != len(records) or archive.comment:
            raise BuildError('Unexpected OBB entries or archive comment.')
        for index, (entry, (name, size, digest)) in enumerate(zip(entries, records), 1):
            if (entry.filename != name or entry.file_size != size
                    or entry.compress_type != zipfile.ZIP_STORED or entry.compress_size != size
                    or entry.flag_bits & 1 or entry.create_system != 3
                    or entry.external_attr != (stat.S_IFREG | 0o644) << 16
                    or entry.date_time != (1980, 1, 1, 0, 0, 0) or entry.comment or entry.extra):
                raise BuildError(f'Unexpected OBB member metadata: {name}')
            actual = hashlib.sha256()
            length = 0
            with archive.open(entry) as source:
                while chunk := source.read(1024 * 1024):
                    actual.update(chunk)
                    length += len(chunk)
            if length != size or actual.hexdigest() != digest:
                raise BuildError(f'OBB member differs from trusted game data: {name}')
            if progress and (index % 4000 == 0 or index == len(records)):
                progress(f'Verified OBB files: {index:,}/{len(records):,}')
    obb = sha_file(path)
    if original != V.fingerprint(safe_path(path)):
        raise BuildError('OBB changed during verification.')
    return manifest, obb


def receipt_value(repo, manifest, obb):
    return {'schema': 1, 'package': PACKAGE, 'version_code': VERSION_CODE,
            'core_version': '1.19.26', 'files': FILES, 'bytes': BYTES,
            'tree_sha256': TREE_SHA256, 'inputs': inputs(repo),
            'manifest': manifest, 'obb': obb,
            'validation': 'exact pinned file names and bytes; deterministic stored ZIP; no runtime claim'}


def verify_receipt(repo, progress=None):
    repo = Path(repo)
    expected = reference(repo)
    root = repo / STAGE
    check_owner(root)
    if (root / '.operation.lock').exists():
        raise BuildError('Game-data preparation is active or interrupted; preserve its operation lock.')
    manifest, obb = verify_bundle(root, expected, progress)
    expected_receipt = receipt_value(repo, manifest, obb)
    if read_json(root / 'receipt.json') != expected_receipt:
        raise BuildError('Game-data receipt does not match its inputs or verified outputs; preserved for inspection.')
    return expected_receipt


def build_bundle(source, root, expected, progress=None):
    """Owned empty output directory; parameterized reference supports isolated tests."""
    source = Path(os.path.abspath(source))
    before = V.inventory(source)
    if len(before) != expected['files'] or sum(v[3] for v in before.values()) != expected['bytes']:
        raise BuildError('Source file count/total bytes differs from the pinned game-data copy.')
    tree = hashlib.sha256()
    with (root / MANIFEST).open('xb') as manifest, zipfile.ZipFile(root / OBB, 'x', compression=zipfile.ZIP_STORED) as archive:
        for index, name in enumerate(sorted(before), 1):
            path = source / name
            if V.fingerprint(V.checked_stat(path)) != before[name]:
                raise BuildError(f'Source changed before packing: {name}')
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | 0o644) << 16
            entry.compress_type = zipfile.ZIP_STORED
            entry.file_size = before[name][3]
            digest = hashlib.sha256()
            size = 0
            with path.open('rb') as src, archive.open(entry, 'w') as dest:
                if V.fingerprint(os.fstat(src.fileno())) != before[name]:
                    raise BuildError(f'Source changed while opening: {name}')
                while chunk := src.read(1024 * 1024):
                    dest.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                if V.fingerprint(os.fstat(src.fileno())) != before[name] or size != before[name][3]:
                    raise BuildError(f'Source changed while packing: {name}')
            record = V.canonical_record(name, size, digest.hexdigest())
            manifest.write(record)
            tree.update(record)
            if progress and (index % 2000 == 0 or index == len(before)):
                progress(f'Packed files: {index:,}/{len(before):,}')
    if tree.hexdigest() != expected['tree_sha256']:
        raise BuildError('Source names/bytes differ from trusted game-data tree; partial outputs preserved.')
    if V.inventory(source) != before:
        raise BuildError('Source changed during OBB preparation; outputs preserved for inspection.')
    return verify_bundle(root, expected, progress)


def prepare(repo, source, progress=print):
    repo = Path(repo)
    expected = reference(repo)
    root = repo / STAGE
    if root.exists() or root.is_symlink():
        check_owner(root)
        if (root / '.operation.lock').exists():
            raise BuildError('Another data preparation is active or interrupted; preserve the stage and send the log.')
        if not (root / 'receipt.json').is_file():
            raise BuildError(f'Incomplete data stage preserved: {root}. Send the log before changing it.')
        progress('Reusing completed OBB after full verification; original source is not read again.')
        return verify_receipt(repo, progress)
    safe_path(root.parent, directory=True)
    root.mkdir()
    write_json(root / OWNER, OWNER_VALUE)
    with operation(root):
        manifest, obb = build_bundle(source, root, expected, progress)
        receipt = receipt_value(repo, manifest, obb)
        write_json(root / 'receipt.json', receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    if args.verify_only and args.data_root:
        parser.error('--verify-only does not read --data-root')
    try:
        result = verify_receipt(repo, print) if args.verify_only else prepare(repo, args.data_root or repo.parent / 'gamedata-1.19.26')
        print(f"OBB: {repo / STAGE / OBB}")
        print(f"Files={result['files']:,}; data bytes={result['bytes']:,}; OBB bytes={result['obb']['bytes']:,}")
        print('ANDROID GAME DATA VERIFIED' if args.verify_only else 'ANDROID GAME DATA PREPARED')
        print('Offline packaging only; Android import and gameplay still require device testing.')
        return 0
    except (BuildError, V.VerificationError, OSError, ValueError, zipfile.BadZipFile, KeyError, TypeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
