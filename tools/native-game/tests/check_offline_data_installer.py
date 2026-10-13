#!/usr/bin/env python3
"""Host JVM tests; run after compiling the importer and OfflineDataInstallerHarness.

The caller selects an empty owned output directory. No Android device, prior build,
source dataset or shared cache is written by this suite.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import warnings
import zipfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(entries):
    return b''.join((json.dumps([name, len(data), sha(data)], separators=(',', ':')) + '\n').encode('ascii') for name, data in entries)


def write_zip(path, entries, mode=stat.S_IFREG | 0o644, compression=zipfile.ZIP_STORED):
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with zipfile.ZipFile(path, 'w', compression=compression) as stream:
            for name, data in entries:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = mode << 16
                info.compress_type = compression
                stream.writestr(info, data)


def snapshot(root):
    result = {}
    for path in sorted(root.rglob('*')):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[name] = ['link', os.readlink(path)]
        elif path.is_file():
            result[name] = ['file', path.stat().st_size, sha(path.read_bytes())]
        elif path.is_dir():
            result[name] = ['directory']
        else:
            result[name] = ['special']
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--java', required=True)
    parser.add_argument('--classes', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=False)
    results = []
    entries = [('data/core.cfg', b'game-data\n'), ('fonts/font.bin', bytes(range(256))), ('zero', b'')]

    def fixture(name, records=entries, data=None):
        work = root / name
        work.mkdir()
        base = work / 'application files with spaces'
        base.mkdir()
        (base / 'userdata').mkdir()
        (base / 'userdata' / 'save.cfg').write_bytes(b'Never replace saved games\n')
        manifest = canonical(records) if data is None else data
        (work / 'manifest.jsonl').write_bytes(manifest)
        write_zip(work / 'game.obb', records)
        return work, base, manifest, len(records), sum(len(value) for _, value in records)

    def run(item, label, ok, mode='install', manifest_hash=None, obb=None, preserve=False):
        work, base, manifest, count, total = item
        before = snapshot(base)
        command = [args.java, '-cp', str(Path(args.classes).resolve()), 'org.wesnoth.offline.OfflineDataInstallerHarness',
                   str(work / 'manifest.jsonl'), manifest_hash or sha(manifest), str(count), str(total), str(base),
                   str(obb or work / 'game.obb'), mode]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60)
        (work / (label + '.log')).write_text(result.stdout)
        assert (result.returncode == 0) == ok, (label, result.returncode, result.stdout)
        assert (base / 'userdata' / 'save.cfg').read_bytes() == b'Never replace saved games\n'
        if ok:
            assert 'HOST IMPORT PASS' in result.stdout, result.stdout
        elif preserve:
            assert before == snapshot(base), (label, 'rejected input modified existing app files')
        results.append({'case': label, 'pass': True, 'exit': result.returncode, 'log': str(work / (label + '.log'))})
        print('PASS ' + label, flush=True)
        return result

    item = fixture('success')
    run(item, 'initial-import', True)
    before = snapshot(item[1])
    run(item, 'verified-reuse-with-missing-obb', True, obb=item[0] / 'missing.obb')
    assert before == snapshot(item[1])
    for name, data in entries:
        assert (item[1] / 'gamedata' / name).read_bytes() == data

    item = fixture('manifest-pin')
    run(item, 'incorrect-manifest-pin', False, manifest_hash='0' * 64, preserve=True)
    for name, records in [
        ('traversal', [('../escape', b'')]), ('absolute', [('/escape', b'')]),
        ('backslash', [('data\\escape', b'')]), ('duplicate', [('x', b''), ('x', b'')]),
        ('unsorted', [('z', b''), ('a', b'')]), ('case-collision', [('X', b''), ('x', b'')]),
        ('file-directory-collision', [('x', b''), ('x/y', b'')]), ('reserved-device', [('NUL.txt', b'')])
    ]:
        run(fixture('manifest-' + name, records), 'manifest-' + name, False, preserve=True)
    item = fixture('manifest-crlf', data=canonical(entries).replace(b'\n', b'\r\n'))
    run(item, 'manifest-crlf-rejected', False, preserve=True)

    for name, changes in [
        ('traversal', [('../outside', b'game-data\n'), *entries[1:]]),
        ('unknown', [('data/wrong.cfg', b'game-data\n'), *entries[1:]]),
        ('duplicate', [entries[0], entries[0], entries[2]]),
        ('directory', [('data/', b'game-data\n'), *entries[1:]]),
        ('wrong-size', [(entries[0][0], b'x'), *entries[1:]]),
        ('missing', entries[:-1]), ('additional', entries + [('unknown', b'')])
    ]:
        item = fixture('zip-' + name)
        write_zip(item[0] / 'game.obb', changes)
        run(item, 'zip-' + name, False, preserve=True)
    for name, mode in [('symlink', stat.S_IFLNK | 0o777), ('special', stat.S_IFIFO | 0o644), ('directory-mode', stat.S_IFDIR | 0o755)]:
        item = fixture('zip-' + name)
        write_zip(item[0] / 'game.obb', entries, mode=mode)
        run(item, 'zip-' + name, False, preserve=True)
    item = fixture('zip-compressed')
    write_zip(item[0] / 'game.obb', entries, compression=zipfile.ZIP_DEFLATED)
    run(item, 'zip-compressed', False, preserve=True)
    for name in ['truncated', 'archive-comment', 'zip64-marker', 'entry-extra', 'entry-comment']:
        item = fixture('zip-' + name)
        path = item[0] / 'game.obb'
        if name == 'truncated':
            path.write_bytes(path.read_bytes()[:-10])
        elif name == 'archive-comment':
            with zipfile.ZipFile(path, 'a') as stream:
                stream.comment = b'unexpected'
        else:
            data = bytearray(path.read_bytes())
            central = data.index(b'PK\x01\x02')
            if name == 'zip64-marker':
                struct.pack_into('<L', data, len(data) - 22 + 16, 0xffffffff)
            else:
                struct.pack_into('<H', data, central + (30 if name == 'entry-extra' else 32), 1)
            path.write_bytes(data)
        run(item, 'zip-' + name, False, preserve=True)

    item = fixture('content-hash')
    write_zip(item[0] / 'game.obb', [(entries[0][0], b'GAME-DATA\n'), *entries[1:]])
    run(item, 'content-hash-rejected', False)
    partials = {str(p): p.read_bytes() for p in (item[1] / '.wesnoth-gamedata-import').glob('.partial-*')}
    assert partials, 'failed file must be preserved for diagnosis'
    write_zip(item[0] / 'game.obb', entries)
    run(item, 'content-hash-corrected-resume', True)
    assert all(Path(path).read_bytes() == value for path, value in partials.items())

    large = [('data/a.cfg', b'complete first\n'), ('data/b.bin', b'z' * (3 * 1024 * 1024))]
    item = fixture('interrupted', large)
    run(item, 'interrupted-copy', False, mode='interrupt')
    completed = item[1] / '.wesnoth-gamedata-import/payload/data/a.cfg'
    assert completed.read_bytes() == large[0][1]
    old_mtime = completed.stat().st_mtime_ns
    partials = {str(p): p.read_bytes() for p in (item[1] / '.wesnoth-gamedata-import').glob('.partial-*')}
    assert partials
    run(item, 'interrupted-copy-resume', True)
    assert (item[1] / 'gamedata/data/a.cfg').stat().st_mtime_ns == old_mtime
    assert all(Path(path).read_bytes() == value for path, value in partials.items())

    item = fixture('receipt-interrupted')
    run(item, 'receipt-publication-interrupted', False, mode='block-receipt')
    assert (item[1] / 'gamedata').is_dir() and not (item[1] / '.wesnoth-gamedata-ready').exists()
    before_target = snapshot(item[1] / 'gamedata')
    partials = {str(p): p.read_bytes() for p in item[1].glob('.partial-*')}
    run(item, 'receipt-publication-recovered', True, obb=item[0] / 'missing.obb')
    assert before_target == snapshot(item[1] / 'gamedata')
    assert all(Path(path).read_bytes() == value for path, value in partials.items())

    for name in ['unmanaged-staging', 'unmanaged-final', 'unexpected-stage-file', 'unexpected-payload-file',
                 'modified-completed-file', 'unexpected-final-file', 'modified-final-file',
                 'symlink-final', 'dangling-symlink-final', 'symlink-payload', 'symlink-payload-leaf']:
        item = fixture(name, large if name in {'unexpected-stage-file', 'unexpected-payload-file', 'modified-completed-file', 'symlink-payload', 'symlink-payload-leaf'} else entries)
        work, base, *_ = item
        if name.startswith('unmanaged'):
            destination = base / ('.wesnoth-gamedata-import' if name.endswith('staging') else 'gamedata')
            destination.mkdir()
            (destination / 'private.txt').write_bytes(b'preserve unknown files')
        elif name in {'unexpected-final-file', 'modified-final-file'}:
            run(item, name + '-setup', True)
            destination = base / ('gamedata/unknown.txt' if name.startswith('unexpected') else 'gamedata/data/core.cfg')
            destination.write_bytes(b'preserve manual edit')
        elif name in {'symlink-final', 'dangling-symlink-final'}:
            outside = work / 'outside'
            if name == 'symlink-final':
                outside.mkdir()
                (outside / 'untouched').write_bytes(b'outside sentinel')
            (base / 'gamedata').symlink_to(outside, target_is_directory=True)
        else:
            run(item, name + '-setup', False, mode='interrupt')
            stage = base / '.wesnoth-gamedata-import'
            payload = stage / 'payload'
            if name == 'unexpected-stage-file':
                (stage / 'unknown.txt').write_bytes(b'preserve stage edit')
            elif name == 'unexpected-payload-file':
                (payload / 'unknown.txt').write_bytes(b'preserve payload edit')
            elif name == 'modified-completed-file':
                (payload / 'data/a.cfg').write_bytes(b'preserve completed edit')
            elif name == 'symlink-payload':
                payload.rename(stage / 'retained-payload')
                payload.symlink_to(stage / 'retained-payload', target_is_directory=True)
            else:
                (payload / 'data/b.bin').symlink_to(work / 'outside-missing')
        before_work = snapshot(work)
        run(item, name + '-rejected', False, preserve=True)
        after_work = snapshot(work)
        assert all(after_work.get(path) == record for path, record in before_work.items()), name

    summary = {'kind': 'wesnoth-offline-java-importer-host-tests', 'cases': len(results), 'all_passed': True,
               'runtime': subprocess.check_output([args.java, '-version'], stderr=subprocess.STDOUT, text=True),
               'results': results}
    (root / 'results.json').write_text(json.dumps(summary, indent=2) + '\n')
    print('ALL %d HOST IMPORT CASES PASSED' % len(results))


if __name__ == '__main__':
    main()
