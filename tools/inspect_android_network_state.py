#!/usr/bin/env python3
"""Collect existing Network/Ninja state without running or changing the build.

Only a new report directly under this checkout's diagnostics directory is
written. Raw Ninja text logs are retained so their exact contents can be
reviewed later; valid syntax or record counts do not establish completeness.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys

sys.dont_write_bytecode = True
REPO = Path(os.path.abspath(__file__)).parents[1]
SPEC = importlib.util.spec_from_file_location('wesnoth_network_state_builder', REPO / 'tools/build_android_network.py')
N = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(N)
MAX_RAW_BYTES = 8 * 1024 * 1024
MAX_METADATA_BYTES = 64 * 1024 * 1024
LOG_NAMES = ('.ninja_log', '.ninja_log.restat', '.ninja_log.recompact')
FILE_NAMES = (*LOG_NAMES, '.ninja_deps', 'build.ninja')


class InspectionError(Exception):
    pass


def checked_path(path):
    """Reject links/reparse points in every existing path component.

    Return False for an absent path, without following any missing ancestor.
    The same guard applies to the inspected stage and the report destination.
    """
    path = Path(os.path.abspath(path))
    for component in (*reversed(path.parents), path):
        try:
            info = component.lstat()
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise InspectionError('Symbolic link or Windows reparse point rejected: ' + str(component))
        if component != path and not stat.S_ISDIR(info.st_mode):
            raise InspectionError('Non-directory ancestor rejected: ' + str(component))
    return True


def identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def checked_tree(directory):
    """Guard every inventory entry before the existing validators read it."""
    checked_path(directory)
    pending = [directory]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                    raise InspectionError('Symbolic link or Windows reparse point rejected: ' + entry.path)
                if stat.S_ISDIR(info.st_mode):
                    pending.append(Path(entry.path))
                elif not stat.S_ISREG(info.st_mode):
                    raise InspectionError('Non-regular inventory entry rejected: ' + entry.path)


def parse_ninja_log(data):
    """Describe v7 syntax; never infer that a log is intact or restorable."""
    result = {'format': 'ninja-log-v7', 'final_newline': data.endswith(b'\n'),
              'header_valid': False, 'row_count': 0, 'valid_row_count': 0,
              'malformed_row_count': 0, 'unique_output_count': 0,
              'duplicate_output_count': 0, 'duplicate_row_count': 0,
              'rows': [], 'malformed_rows': [],
              'interpretation': 'Syntax and counts alone do not prove completeness or safe recovery.'}
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        result.update(syntax_valid=False, error='Log bytes are not valid UTF-8.')
        return result
    lines = text.split('\n')
    if result['final_newline']:
        lines.pop()
    result['header_valid'] = bool(lines) and lines[0].removesuffix('\r') == '# ninja log v7'
    seen = {}
    for line_number, line in enumerate(lines[1:], 2):
        fields = line.removesuffix('\r').split('\t')
        result['row_count'] += 1
        reason = None
        if len(fields) != 5:
            reason = 'Expected exactly five tab-separated fields.'
        else:
            for index, bits in ((0, 32), (1, 32), (2, 64)):
                if not re.fullmatch(r'[+-]?[0-9]+', fields[index]):
                    reason = 'Invalid signed integer field.'
                    break
                # Untrusted logs must remain collectable even beyond Python's
                # integer-string conversion limit. Ninja fields are bounded.
                if len(fields[index].lstrip('+-').lstrip('0')) > (10 if bits == 32 else 19):
                    reason = 'Signed integer field is outside its range.'
                    break
                value = int(fields[index].lstrip('+-').lstrip('0') or '0')
                if fields[index].startswith('-'):
                    value = -value
                if not -(2 ** (bits - 1)) <= value < 2 ** (bits - 1):
                    reason = 'Signed integer field is outside its range.'
                    break
            if reason is None and (not fields[3] or '\0' in fields[3] or '\r' in fields[3]):
                reason = 'Output path is empty or contains a control character.'
            if reason is None and not re.fullmatch(r'[0-9a-fA-F]{1,16}', fields[4]):
                reason = 'Command hash must contain one through sixteen hexadecimal digits.'
        if reason:
            result['malformed_rows'].append({'line': line_number, 'reason': reason})
            result['malformed_row_count'] += 1
            continue
        def integer(field):
            value = int(field.lstrip('+-').lstrip('0') or '0')
            return -value if field.startswith('-') else value
        row = {'line': line_number, 'start': integer(fields[0]), 'end': integer(fields[1]),
               'mtime': integer(fields[2]), 'output': fields[3], 'command_hash': fields[4]}
        result['rows'].append(row)
        result['valid_row_count'] += 1
        seen[fields[3]] = seen.get(fields[3], 0) + 1
    result['unique_output_count'] = len(seen)
    result['duplicate_output_count'] = sum(count > 1 for count in seen.values())
    result['duplicate_row_count'] = sum(count - 1 for count in seen.values())
    result['syntax_valid'] = result['header_valid'] and result['final_newline'] and not result['malformed_row_count']
    return result


def inspect_file(path, include_raw=False):
    result = {'status': 'unknown'}
    try:
        if not checked_path(path):
            return {'status': 'missing'}
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            return {'status': 'rejected', 'error': 'Expected a regular file.'}
        if before.st_size > MAX_METADATA_BYTES:
            return {'status': 'omitted_size_limit', 'bytes': before.st_size, 'limit_bytes': MAX_METADATA_BYTES}
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if identity(opened) != identity(before):
                return {'status': 'changed', 'error': 'File changed before reading.'}
            digest = hashlib.sha256()
            total = 0
            raw = bytearray() if include_raw and before.st_size <= MAX_RAW_BYTES else None
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                digest.update(chunk)
                total += len(chunk)
                if total > MAX_METADATA_BYTES:
                    return {'status': 'omitted_size_limit', 'limit_bytes': MAX_METADATA_BYTES}
                if raw is not None:
                    if total <= MAX_RAW_BYTES:
                        raw.extend(chunk)
                    else:
                        raw = None
            after_handle = os.fstat(stream.fileno())
        checked_path(path)
        after_path = path.lstat()
        result.update(status='present', bytes=total, sha256=digest.hexdigest())
        if identity(before) != identity(after_handle) or identity(before) != identity(after_path) or total != before.st_size:
            result.update(status='changed', error='File changed while being read; snapshot is not stable.')
        if include_raw:
            if raw is None:
                result['raw'] = {'status': 'omitted_size_limit', 'limit_bytes': MAX_RAW_BYTES}
            else:
                data = bytes(raw)
                try:
                    result['raw'] = {'status': 'captured', 'encoding': 'utf-8', 'text': data.decode('utf-8')}
                except UnicodeDecodeError:
                    result['raw'] = {'status': 'captured', 'encoding': 'base64', 'data': base64.b64encode(data).decode('ascii')}
                result['parse'] = parse_ninja_log(data)
    except InspectionError as error:
        result.update(status='rejected', error=str(error))
    except OSError as error:
        result.update(status='read_error', error=str(error))
    return result


def verification_error(error):
    return {'status': 'failed', 'error': str(error),
            'inspection_incomplete': isinstance(error, (InspectionError, OSError))}


def collect(repo):
    """Read fixed metadata and existing validators; no external process calls."""
    root = repo / 'build/android-network'
    report = {'schema_version': 1, 'kind': 'wesnoth-android-network-state',
              'collected_at_utc': datetime.now(timezone.utc).isoformat(),
              'scope': 'Read-only stage inspection. Only the new diagnostic report is written; no build or recovery is performed.',
              'checks': {}, 'files': {}}
    checks = report['checks']
    for name in FILE_NAMES:
        report['files'][name] = inspect_file(root / 'build' / name, include_raw=name in LOG_NAMES)
    try:
        checked_path(repo)
        for name in N.PROJECT_INPUTS:
            checked_path(repo / name)
        profile, _, plan, pins = N.selected_inputs(repo)
        checks['project_inputs'] = {'status': 'passed', 'profile': profile}
    except (InspectionError, N.BuildError, OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        checks['project_inputs'] = verification_error(error)
        plan = None
    owned = False
    try:
        if not checked_path(root):
            checks['stage_ownership'] = {'status': 'absent'}
        elif not root.is_dir():
            checks['stage_ownership'] = {'status': 'failed', 'error': 'Network stage is not a directory.'}
        elif not checked_path(root / N.MARKER):
            checks['stage_ownership'] = {'status': 'unverified', 'error': 'Owned-stage marker is missing.'}
        elif plan is None:
            checks['stage_ownership'] = {'status': 'unverified', 'error': 'Project inputs could not be verified.'}
        else:
            marker = N.B.read_json(N.B.require_file(root / N.MARKER, 'Owned-stage marker'))
            if (not isinstance(marker, dict) or marker.get('schema_version') != 1 or marker.get('kind') != N.KIND
                    or marker.get('profile') != profile or marker.get('archives') != pins
                    or not isinstance(marker.get('source_files'), dict)):
                raise ValueError('Owned-stage marker does not match the selected Network profile and archives.')
            checks['stage_ownership'] = {'status': 'passed'}
            owned = True
    except (InspectionError, N.BuildError, OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        checks['stage_ownership'] = verification_error(error)
    for kind in ('generation', 'prefix'):
        if not owned:
            checks[kind] = {'status': 'unverified', 'error': 'A matching regular owned stage is required.'}
            continue
        try:
            directory = root / ('work/openssl-3.4.1' if kind == 'generation' else 'prefix')
            state = root / ('.generated-state.json' if kind == 'generation' else N.PREFIX_STATE)
            present = checked_path(directory)
            state_present = checked_path(state)
            if not present and not state_present:
                checks[kind] = {'status': 'absent'}
            elif not present or not state_present:
                checks[kind] = {'status': 'unverified', 'error': 'Directory or its owned state record is missing.'}
            elif not directory.is_dir() or not state.is_file():
                checks[kind] = {'status': 'failed', 'error': 'Expected a directory and regular state file.'}
            elif kind == 'generation':
                checked_tree(root / 'work')
                checks[kind] = {'status': 'passed' if N.verify_generated(repo, root, plan) else 'absent'}
            else:
                checked_tree(directory)
                files = N.verify_prefix(root)
                checks[kind] = {'status': 'passed', 'file_count': len(files),
                                'note': 'Owned prefix inventory matches its saved state; this is not final artifact or build validation.'}
        except (InspectionError, N.BuildError, OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            checks[kind] = verification_error(error)
    complete = all(entry['status'] in ('present', 'missing')
                   and entry.get('raw', {}).get('status') != 'omitted_size_limit'
                   for entry in report['files'].values())
    complete = complete and not any(check.get('inspection_incomplete') for check in checks.values())
    report['collection'] = {'complete': complete, 'files_inspected': len(FILE_NAMES),
                            'meaning': 'Collection completeness is separate from verification results and does not mean the build succeeded.'}
    return report


def report_path(repo, requested):
    path = Path(os.path.abspath(requested))
    diagnostics = repo / 'diagnostics'
    if path.parent != diagnostics or path.suffix.lower() != '.json':
        raise InspectionError('Report must be a new .json file directly in this checkout\'s diagnostics directory.')
    if not checked_path(repo) or not repo.is_dir():
        raise InspectionError('Checkout directory is absent or unsafe.')
    if not checked_path(diagnostics):
        diagnostics.mkdir()
    if not checked_path(diagnostics) or not diagnostics.is_dir():
        raise InspectionError('Diagnostics must be a regular directory.')
    if checked_path(path):
        raise InspectionError('Report already exists; choose a new report filename.')
    return path


def save_report(path, report):
    checked_path(path.parent)
    if checked_path(path):
        raise InspectionError('Report already exists; nothing overwritten.')
    data = (json.dumps(report, sort_keys=True, indent=2, ensure_ascii=True) + '\n').encode('utf-8')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        destination = report_path(REPO, args.report)
        report = collect(REPO)
        save_report(destination, report)
        print('Report saved: ' + str(destination), flush=True)
        print('Files inspected: ' + str(report['collection']['files_inspected']), flush=True)
        for name in ('stage_ownership', 'generation', 'prefix'):
            print(name + ': ' + report['checks'][name]['status'], flush=True)
        if report['collection']['complete']:
            print('ANDROID NETWORK STATE COLLECTED', flush=True)
            print('Diagnostic collection only; no build, test or metadata recovery was performed.', flush=True)
            return 0
        print('INCOMPLETE: one or more metadata files could not be safely captured. Send the saved report.', flush=True)
    except (InspectionError, OSError, ValueError) as error:
        print('ERROR: ' + str(error), file=sys.stderr, flush=True)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
