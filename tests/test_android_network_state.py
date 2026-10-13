"""Read-only Network state collection, raw Ninja evidence and report ownership."""
from contextlib import redirect_stdout, redirect_stderr
import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('android_network_state', REPO / 'tools/inspect_android_network_state.py')
D = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(D)
N = D.N
VALID = b'# ninja log v7\n-1\t25\t-123\tobject with spaces.o\taBcD123\n'


def write(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def fixture(repo):
    for name in N.PROJECT_INPUTS:
        write(repo, name, (REPO / name).read_bytes())
    root = repo / 'build/android-network'
    work = root / 'work/openssl-3.4.1'
    plan_path = repo / 'tools/native-network/build-plan.json'
    plan = N.B.read_json(plan_path)
    headers = {}
    for name in plan['public_header_paths']['openssl']:
        data = ('fixture header ' + name).encode()
        write(root, 'prefix/include/' + name, data)
        write(work, 'include/' + name, data)
        headers[name] = N.pin(data)
    # Match the valid intermediate OpenSSL installation: 141 headers,
    # two archives and its license. No final receipt or curl is required.
    write(root, 'prefix/lib/libcrypto.a', b'partial fixture crypto archive')
    write(root, 'prefix/lib/libssl.a', b'partial fixture ssl archive')
    write(root, 'prefix/share/licenses/wesnoth-network/openssl-LICENSE.txt', b'fixture license')
    configure = write(work, 'Configure', b'fixture Configure')
    plan['configure_patch']['after'] = N.B.fingerprint(configure)
    plan['openssl_headers'] = N.inventory_pin(headers)
    plan_path.write_bytes(N.B.json_bytes(plan))
    write(work, 'portable-library-graph.json', N.B.json_bytes(plan['openssl_graph']))
    profile, _, plan, pins = N.selected_inputs(repo)
    write(root, N.MARKER, N.B.json_bytes({'schema_version': 1, 'kind': N.KIND,
        'profile': profile, 'archives': pins, 'source_files': {}}))
    write(root, '.generated-state.json', N.B.json_bytes({'inputs': N.generation_binding(repo, plan),
        'files': N.M.inventory(root, 'work')}))
    N.save_prefix_state(root)
    write(root, 'build/.ninja_log.restat', VALID)
    write(root, 'build/.ninja_deps', b'\0binary dependencies\xff')
    write(root, 'build/build.ninja', b'# existing native build graph\n')
    write(root, 'sources/preserved-original', b'owned source untouched')
    write(repo, 'build/android-boost/prefix/prior-stage-sentinel', b'prior completed stage')
    return root


def snapshot(repo):
    return {str(path.relative_to(repo)): path.read_bytes() for path in repo.rglob('*')
            if path.is_file() and 'diagnostics' not in path.relative_to(repo).parts}


class NinjaLogTests(unittest.TestCase):
    def test_v7_signed_fields_crlf_duplicates_and_unordered_outputs(self):
        data = (b'# ninja log v7\r\n-2147483648\t2147483647\t-9223372036854775808\tz.o\t1\r\n'
                b'1\t2\t9223372036854775807\ta.o\tffffffffffffffff\r\n'
                b'3\t4\t8\tz.o\t2\r\n')
        result = D.parse_ninja_log(data)
        self.assertTrue(result['syntax_valid'])
        self.assertEqual((result['row_count'], result['unique_output_count'], result['duplicate_row_count']), (3, 2, 1))
        self.assertEqual(result['duplicate_output_count'], 1)
        self.assertEqual(result['rows'][0]['mtime'], -9223372036854775808)
        self.assertNotIn('restorable', result)
        self.assertIn('do not prove completeness', result['interpretation'])

    def test_malformed_rows_and_unterminated_final_record(self):
        bad = [b'1\t2\t3\tonly-four', b'1\t2\t3\t\t0', b'2147483648\t2\t3\tx\t0',
               b'1\t2\t9223372036854775808\tx\t0', b'1\t2\t3\tx\txyz',
               b'1\t2\t3\tx\t12345678901234567', b'1\t2\t3\tx\t0\textra',
               b'1\t2\t3\tx\x00y\t0', b'1.0\t2\t3\tx\t0', b'9' * 5000 + b'\t2\t3\tx\t0']
        for row in bad:
            with self.subTest(row=row[:30]):
                result = D.parse_ninja_log(b'# ninja log v7\n' + row + b'\n')
                self.assertFalse(result['syntax_valid'])
                self.assertEqual(result['malformed_row_count'], 1)
        result = D.parse_ninja_log(VALID.rstrip(b'\n'))
        self.assertFalse(result['syntax_valid'])
        self.assertEqual(result['valid_row_count'], 1)
        self.assertFalse(D.parse_ninja_log(VALID.replace(b'v7', b'v6'))['syntax_valid'])
        self.assertFalse(D.parse_ninja_log(b'\xff')['syntax_valid'])
        # Leading zeros must not cause Python's conversion-size limit either.
        self.assertTrue(D.parse_ninja_log(b'# ninja log v7\n' + b'0' * 5000 + b'1\t2\t3\tx\t0\n')['syntax_valid'])


class StateCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='network diagnostic fixtures ')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo'
        self.root = fixture(self.repo)

    def invoke(self, name='report.json'):
        report = self.repo / 'diagnostics' / name
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(D, 'REPO', self.repo), redirect_stdout(stdout), redirect_stderr(stderr):
            result = D.main(['--report', str(report)])
        return result, report, stdout.getvalue(), stderr.getvalue()

    def test_partial_prefix_and_generation_verified_without_process_or_mutation(self):
        before = snapshot(self.repo)
        with patch.object(N.subprocess, 'run', side_effect=AssertionError('No child processes')), \
             patch.object(N.subprocess, 'Popen', side_effect=AssertionError('No child processes')), \
             patch.object(N.M, 'run', side_effect=AssertionError('No native tools')), \
             patch.object(N, 'build', side_effect=AssertionError('No build')), \
             patch.object(N, 'verify_receipt', side_effect=AssertionError('No final receipt required')):
            result, path, stdout, stderr = self.invoke()
        self.assertEqual((result, stderr), (0, ''))
        report = json.loads(path.read_bytes())
        self.assertEqual(report['checks']['generation']['status'], 'passed')
        self.assertEqual(report['checks']['prefix']['file_count'], 144)
        self.assertEqual(report['checks']['prefix']['status'], 'passed')
        self.assertEqual(report['files']['.ninja_log']['status'], 'missing')
        self.assertEqual(report['files']['.ninja_log.recompact']['status'], 'missing')
        self.assertTrue(report['collection']['complete'])
        self.assertIn('ANDROID NETWORK STATE COLLECTED', stdout)
        self.assertIn('Report saved: ' + str(path), stdout)
        self.assertEqual(snapshot(self.repo), before)
        self.assertFalse((self.root / 'receipt.json').exists())
        self.assertFalse(list(self.repo.rglob('__pycache__')))

    def test_exact_raw_roundtrip_and_metadata_only_binary_files(self):
        for payload in (VALID.replace(b'\n', b'\r\n'), VALID + b'\xff\xfe', b'# ninja log v7\n' + b'9' * 5000 + b'\t2\t3\tx\t0\n'):
            with self.subTest(payload=payload[:30]):
                write(self.root, 'build/.ninja_log', payload)
                report = D.collect(self.repo)
                entry = report['files']['.ninja_log']
                raw = entry['raw']
                decoded = raw['text'].encode('utf-8') if raw['encoding'] == 'utf-8' else base64.b64decode(raw['data'])
                self.assertEqual(decoded, payload)
                self.assertEqual(entry['bytes'], len(payload))
                self.assertEqual(entry['sha256'], hashlib.sha256(payload).hexdigest())
                self.assertTrue(report['collection']['complete'])
                self.assertNotIn('raw', report['files']['.ninja_deps'])
                self.assertNotIn('raw', report['files']['build.ninja'])

    def test_failed_validator_still_saves_all_metadata(self):
        (self.root / 'prefix/lib/libssl.a').write_bytes(b'changed')
        (self.root / 'work/openssl-3.4.1/Configure').write_bytes(b'changed')
        result, path, _, _ = self.invoke()
        self.assertEqual(result, 0)
        report = json.loads(path.read_bytes())
        self.assertEqual(report['checks']['prefix']['status'], 'failed')
        self.assertEqual(report['checks']['generation']['status'], 'failed')
        self.assertTrue(report['collection']['complete'])
        self.assertEqual(len(report['files']), 5)
        self.assertEqual(report['files']['.ninja_log.restat']['raw']['text'].encode(), VALID)

    def test_missing_stage_is_not_verified_and_missing_state_is_unverified(self):
        (self.root / N.PREFIX_STATE).unlink()
        (self.root / '.generated-state.json').unlink()
        report = D.collect(self.repo)
        self.assertEqual(report['checks']['prefix']['status'], 'unverified')
        self.assertEqual(report['checks']['generation']['status'], 'unverified')
        self.root.rename(self.root.with_name('preserved-network'))
        report = D.collect(self.repo)
        self.assertEqual(report['checks']['stage_ownership']['status'], 'absent')
        self.assertEqual(report['checks']['generation']['status'], 'unverified')
        self.assertTrue(all(entry['status'] == 'missing' for entry in report['files'].values()))
        self.assertTrue(report['collection']['complete'])

    def test_owned_stage_with_absent_work_and_prefix_does_not_pass(self):
        (self.root / 'work').rename(self.root / 'preserved-work')
        (self.root / 'prefix').rename(self.root / 'preserved-prefix')
        (self.root / N.PREFIX_STATE).unlink()
        (self.root / '.generated-state.json').unlink()
        report = D.collect(self.repo)
        self.assertEqual(report['checks']['generation']['status'], 'absent')
        self.assertEqual(report['checks']['prefix']['status'], 'absent')

    def test_malformed_marker_and_plan_are_recorded_without_losing_logs(self):
        marker = self.root / N.MARKER
        original = marker.read_bytes()
        for data in (b'null', b'[]', b'{"schema_version": 42}', b'{malformed'):
            with self.subTest(marker=data):
                marker.write_bytes(data)
                report = D.collect(self.repo)
                self.assertEqual(report['checks']['stage_ownership']['status'], 'failed')
                self.assertTrue(report['collection']['complete'])
        marker.write_bytes(original)
        plan = self.repo / 'tools/native-network/build-plan.json'
        plan.write_bytes(b'[]')
        report = D.collect(self.repo)
        self.assertEqual(report['checks']['project_inputs']['status'], 'failed')
        self.assertTrue(report['collection']['complete'])
        self.assertEqual(report['files']['.ninja_log.restat']['raw']['text'].encode(), VALID)

    def test_metadata_read_error_still_writes_incomplete_report(self):
        original = D.os.open
        target = self.root / 'build/.ninja_log.restat'
        def denied(path, *args, **kwargs):
            if Path(path) == target:
                raise PermissionError('fixture sharing violation')
            return original(path, *args, **kwargs)
        with patch.object(D.os, 'open', side_effect=denied):
            result, path, stdout, _ = self.invoke()
        self.assertEqual(result, 2)
        report = json.loads(path.read_bytes())
        self.assertEqual(report['files']['.ninja_log.restat']['status'], 'read_error')
        self.assertFalse(report['collection']['complete'])
        self.assertIn('Report saved:', stdout)
        self.assertNotIn('ANDROID NETWORK STATE COLLECTED', stdout)

    def test_size_bounds_capture_metadata_but_do_not_claim_complete_raw_evidence(self):
        with patch.object(D, 'MAX_RAW_BYTES', 10):
            report = D.collect(self.repo)
        entry = report['files']['.ninja_log.restat']
        self.assertEqual(entry['raw']['status'], 'omitted_size_limit')
        self.assertEqual(entry['sha256'], hashlib.sha256(VALID).hexdigest())
        self.assertFalse(report['collection']['complete'])
        with patch.object(D, 'MAX_METADATA_BYTES', 10):
            entry = D.inspect_file(self.root / 'build/build.ninja')
        self.assertEqual(entry['status'], 'omitted_size_limit')
        self.assertNotIn('sha256', entry)

    def test_symlink_metadata_and_nested_inventory_are_rejected(self):
        external = write(Path(self.temp.name), 'outside', b'external data')
        target = self.root / 'build/.ninja_log'
        try:
            target.symlink_to(external)
        except OSError as error:
            self.skipTest(str(error))
        result, path, _, _ = self.invoke()
        report = json.loads(path.read_bytes())
        self.assertEqual(result, 2)
        self.assertEqual(report['files']['.ninja_log']['status'], 'rejected')
        target.unlink()
        (self.root / 'prefix/nested-link').symlink_to(external)
        report = D.collect(self.repo)
        self.assertEqual(report['checks']['prefix']['status'], 'failed')
        self.assertFalse(report['collection']['complete'])
        self.assertEqual(external.read_bytes(), b'external data')

    def test_symlink_report_directory_and_windows_reparse_ancestor_rejected(self):
        outside = Path(self.temp.name) / 'outside-reports'; outside.mkdir()
        diagnostics = self.repo / 'diagnostics'
        try:
            diagnostics.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(str(error))
        result, _, _, stderr = self.invoke()
        self.assertEqual(result, 2)
        self.assertIn('reparse point rejected', stderr)
        self.assertEqual(list(outside.iterdir()), [])
        diagnostics.unlink()
        original = Path.lstat
        ancestor = self.root / 'build'
        def reparse(path):
            info = original(path)
            if path == ancestor:
                return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
            return info
        with patch.object(Path, 'lstat', reparse):
            result, path, _, _ = self.invoke()
        report = json.loads(path.read_bytes())
        self.assertEqual(result, 2)
        self.assertTrue(all(entry['status'] == 'rejected' for entry in report['files'].values()))

    def test_existing_or_external_report_is_not_overwritten(self):
        path = write(self.repo, 'diagnostics/report.json', b'preserved report')
        before = snapshot(self.repo)
        result, _, _, stderr = self.invoke()
        self.assertEqual(result, 2)
        self.assertIn('already exists', stderr)
        self.assertEqual(path.read_bytes(), b'preserved report')
        self.assertEqual(snapshot(self.repo), before)
        outside = Path(self.temp.name) / 'outside.json'
        with self.assertRaises(D.InspectionError):
            D.report_path(self.repo, outside)
        self.assertFalse(outside.exists())
        # Exclusive creation closes the gap after the initial destination check.
        with self.assertRaises(D.InspectionError):
            D.save_report(path, {'replacement': True})
        self.assertEqual(path.read_bytes(), b'preserved report')


if __name__ == '__main__':
    unittest.main()
