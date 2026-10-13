"""Exact Network log recovery and transactional CMake metadata preservation."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath
import tempfile
import unittest
from unittest.mock import patch
from test_android_bootstrap import ar_library, elf_object
from test_android_network_state import D, fixture, snapshot, write

N = D.N


def history(names):
    return b'# ninja log v7\n' + b''.join(('1\t2\t3\t' + name + '\tabc123\n').encode() for name in names)


class ConfigureGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='network configure fixture ')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo'
        self.root = self.repo / 'build/android-network'
        self.output = self.root / 'build'; self.output.mkdir(parents=True)
        self.plan = {'openssl_graph': {'libcrypto': {'sources': ['crypto/a.c']}, 'libssl': {'sources': ['ssl/b.c']}}}
        self.log = history(sorted(N.openssl_ninja_outputs(self.root, self.plan)))
        self.deps = b'# ninjadeps\n\x04\0\0\0fixture binary dependency history'
        write(self.output, '.ninja_log', self.log); write(self.output, '.ninja_deps', self.deps)
        write(self.output, 'existing.o', elf_object()); write(self.output, 'existing.o.d', b'included-header.h\n')
        write(self.root, 'work/sentinel', b'completed generation')
        write(self.repo, 'build/android-boost/prefix/sentinel', b'completed earlier stage')
        self.command = ['fixture-cmake', '-S', str(self.repo / 'tools/native-network'), '-B', str(self.output)]

    def invoke(self, callback):
        with patch.object(N.M, 'run', side_effect=callback), patch.object(N.time, 'sleep'), redirect_stdout(io.StringIO()):
            return N.guarded_configure(self.repo, self.root, self.plan, self.command, {})

    def during_configure(self, args, env):
        self.assertFalse((self.output / '.ninja_log').exists())
        self.assertFalse((self.output / '.ninja_deps').exists())
        self.assertEqual(args[-1], '-DCMAKE_SUPPRESS_REGENERATION=ON')
        transaction = json.loads((self.root / N.NINJA_TRANSACTION).read_bytes())
        self.assertEqual(transaction['files'], {'.ninja_log': N.pin(self.log), '.ninja_deps': N.pin(self.deps)})
        backup = self.root / transaction['backup']
        self.assertEqual((backup / '.ninja_log').read_bytes(), self.log)
        self.assertEqual((backup / '.ninja_deps').read_bytes(), self.deps)
        return backup

    def assert_restored(self):
        self.assertEqual((self.output / '.ninja_log').read_bytes(), self.log)
        self.assertEqual((self.output / '.ninja_deps').read_bytes(), self.deps)
        self.assertFalse((self.root / N.NINJA_TRANSACTION).exists())

    def test_success_withholds_both_then_restores_bytes_and_preserves_other_stages(self):
        before = snapshot(self.repo)
        self.invoke(self.during_configure)
        self.assert_restored()
        after = snapshot(self.repo)
        self.assertEqual({name: after[name] for name in before}, before)
        self.assertEqual(len(list((self.root / 'ninja-state-backups').iterdir())), 1)

    def test_external_source_object_paths_follow_cmake_drive_and_space_mapping(self):
        plan = {'openssl_graph': {'libcrypto': {'sources': ['crypto/a.c']}, 'libssl': {'sources': ['ssl/b.c']}}}
        for root, mapped in (
            (PurePosixPath('/workspace/build/android-network'), 'workspace/build/android-network'),
            (PurePosixPath('/workspace with spaces/build/android-network'), 'workspace_with_spaces/build/android-network'),
            (PureWindowsPath('D:/Test/wesnoth-setup/wesnoth_client/build/android-network'), 'D_/Test/wesnoth-setup/wesnoth_client/build/android-network'),
            (PureWindowsPath('D:/Test with spaces/client/build/android-network'), 'D_/Test_with_spaces/client/build/android-network'),
        ):
            with self.subTest(root=str(root)):
                outputs = N.openssl_ninja_outputs(root, plan)
                self.assertEqual(outputs, {'libcrypto.a', 'libssl.a',
                    'CMakeFiles/crypto.dir/' + mapped + '/work/openssl-3.4.1/crypto/a.c.o',
                    'CMakeFiles/ssl.dir/' + mapped + '/work/openssl-3.4.1/ssl/b.c.o'})

    def test_cmake_failure_and_interrupt_restore_before_propagating(self):
        for failure in (N.BuildError('fixture configure failure'), KeyboardInterrupt()):
            with self.subTest(kind=type(failure).__name__):
                def run(args, env):
                    self.during_configure(args, env)
                    raise failure
                with self.assertRaises(type(failure)):
                    self.invoke(run)
                self.assert_restored()

    def test_failed_second_unlink_restores_first_without_overwriting_survivor(self):
        original = Path.unlink
        def unlink(path, *args, **kwargs):
            if path == self.output / '.ninja_deps': raise PermissionError('fixture sharing violation')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'unlink', unlink), patch.object(N.M, 'run') as run:
            with self.assertRaises(PermissionError): self.invoke(run)
            run.assert_not_called()
        self.assert_restored()

    def test_unexpected_destination_and_partial_restore_keep_backups_and_pending(self):
        def unexpected(args, env):
            self.during_configure(args, env)
            (self.output / '.ninja_log').write_bytes(b'unexpected writer')
        with self.assertRaisesRegex(N.BuildError, 'restoration is incomplete'):
            self.invoke(unexpected)
        self.assertEqual((self.output / '.ninja_log').read_bytes(), b'unexpected writer')
        self.assertEqual((self.output / '.ninja_deps').read_bytes(), self.deps)
        pending = (self.root / N.NINJA_TRANSACTION).read_bytes()
        receipt = write(self.root, 'receipt.json', b'previous receipt must survive')
        with patch.object(N, 'build_stage', side_effect=AssertionError('must not build')):
            with self.assertRaisesRegex(N.BuildError, 'unfinished Ninja configure'):
                N.build(self.repo, object())
        self.assertEqual(receipt.read_bytes(), b'previous receipt must survive')
        self.assertEqual((self.root / N.NINJA_TRANSACTION).read_bytes(), pending)

    def test_partial_exclusive_restore_is_never_overwritten_on_retry(self):
        original = N.ninja_write_new
        def fail(path, data):
            if path == self.output / '.ninja_log':
                path.write_bytes(data[:5])
                raise OSError('fixture interrupted write')
            return original(path, data)
        with patch.object(N, 'ninja_write_new', side_effect=fail):
            with self.assertRaisesRegex(N.BuildError, 'restoration is incomplete'):
                self.invoke(self.during_configure)
        self.assertEqual((self.output / '.ninja_log').read_bytes(), self.log[:5])
        before = snapshot(self.repo)
        with self.assertRaisesRegex(N.BuildError, 'unfinished Ninja configure'):
            self.invoke(lambda *_: self.fail('must not configure'))
        self.assertEqual(snapshot(self.repo), before)

    def test_missing_log_or_manifest_output_stops_before_any_backup_or_configure(self):
        for data in (None, history(['build.ninja']), history(['../outside']), b'# ninja log v7\n1\t2\t3\tx\t0'):
            with self.subTest(data=data):
                path = self.output / '.ninja_log'
                if data is None: path.unlink()
                else: path.write_bytes(data)
                before = snapshot(self.repo)
                with self.assertRaises(N.BuildError): self.invoke(lambda *_: self.fail('must not configure'))
                self.assertEqual(snapshot(self.repo), before)

    def test_fresh_stage_without_metadata_preserves_absence(self):
        (self.output / '.ninja_log').unlink(); (self.output / '.ninja_deps').unlink()
        self.invoke(lambda args, env: self.assertEqual(args[-1], '-DCMAKE_SUPPRESS_REGENERATION=ON'))
        self.assertFalse((self.output / '.ninja_log').exists())
        self.assertFalse((self.output / '.ninja_deps').exists())
        self.assertFalse((self.root / N.NINJA_TRANSACTION).exists())

    def test_stage_operation_lock_rejects_concurrent_or_abandoned_operation(self):
        with N.network_operation(self.repo):
            before = snapshot(self.repo)
            with self.assertRaisesRegex(N.BuildError, 'Another or interrupted Network'):
                with N.network_operation(self.repo): self.fail('second operation must not run')
            self.assertEqual(snapshot(self.repo), before)
        self.assertFalse((self.repo / 'build/.wesnoth-network-operation.json').exists())

    def test_symlink_metadata_is_rejected(self):
        outside = write(Path(self.temp.name), 'outside', b'preserve outside')
        path = self.output / '.ninja_log'; path.unlink()
        try: path.symlink_to(outside)
        except OSError as error: self.skipTest(str(error))
        with self.assertRaisesRegex(N.BuildError, 'link or reparse'):
            self.invoke(lambda *_: self.fail('must not configure'))
        self.assertEqual(outside.read_bytes(), b'preserve outside')

    def test_sharing_retry_is_bounded_and_rechecks_bytes(self):
        target = self.output / '.ninja_log'; original = Path.unlink; calls = []
        def unlink(path, *args, **kwargs):
            if path == target:
                calls.append(path)
                if len(calls) < 3: raise PermissionError('fixture sharing violation')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'unlink', unlink), patch.object(N.time, 'sleep') as sleep, redirect_stdout(io.StringIO()):
            N.ninja_unlink(target, self.log)
        self.assertEqual(len(calls), 3); self.assertEqual(sleep.call_count, 2)
        target.write_bytes(self.log)
        def changed(path, *args, **kwargs):
            path.write_bytes(b'concurrent writer')
            raise PermissionError('fixture sharing violation')
        with patch.object(Path, 'unlink', changed), patch.object(N.time, 'sleep'), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(N.BuildError, 'changed; nothing removed'):
                N.ninja_unlink(target, self.log)
        self.assertEqual(target.read_bytes(), b'concurrent writer')


class StateRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='network exact recovery fixture ')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo'; self.root = fixture(self.repo)
        (self.root / 'sources/preserved-original').unlink()
        plan_path = self.repo / 'tools/native-network/build-plan.json'; plan = N.B.read_json(plan_path)
        plan['source_inventories'] = {source: N.inventory_pin({}) for source in N.ARCHIVES.values()}
        plan['licenses']['openssl-LICENSE.txt'] = N.pin(b'fixture license')
        plan_path.write_bytes(N.B.json_bytes(plan))
        self.plan = N.selected_inputs(self.repo)[2]
        self.names = sorted(N.openssl_ninja_outputs(self.root, self.plan))
        self.output = self.root / 'build'
        for name in self.names:
            data = elf_object() if name.endswith('.o') else ar_library(objects=plan['object_counts'][name])
            write(self.output, name, data)
            if name.endswith('.a'): write(self.root, 'prefix/lib/' + name, data)
        N.save_prefix_state(self.root)
        self.log = history(self.names)
        (self.output / '.ninja_log.restat').write_bytes(self.log)
        self.report_path = write(self.repo, 'diagnostics/network-state-fixture.json', N.B.json_bytes(D.collect(self.repo)))

    def recover(self):
        with redirect_stdout(io.StringIO()) as output, patch.object(N.M, 'run', side_effect=AssertionError('No native process')), \
             patch.object(N.subprocess, 'run', side_effect=AssertionError('No child process')):
            N.recover_ninja_state(self.repo, self.report_path)
        return output.getvalue()

    def test_exact_recovery_preserves_all_sources_objects_metadata_and_prior_stages(self):
        before = snapshot(self.repo)
        output = self.recover()
        self.assertIn('ANDROID NETWORK NINJA STATE RECOVERED', output)
        self.assertEqual((self.output / '.ninja_log').read_bytes(), self.log)
        after = snapshot(self.repo)
        self.assertEqual({name: after[name] for name in before}, before)
        backups = list((self.root / 'ninja-state-backups').iterdir()); self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / '.ninja_log.restat').read_bytes(), self.log)
        self.assertEqual((backups[0] / 'report.json').read_bytes(), self.report_path.read_bytes())
        before = snapshot(self.repo)
        self.assertIn('already restored; nothing changed', self.recover())
        self.assertEqual(snapshot(self.repo), before)

    def test_changed_metadata_rejects_before_recovery_or_backup(self):
        for name in ('.ninja_log.restat', '.ninja_deps', 'build.ninja'):
            with self.subTest(name=name):
                path = self.output / name; original = path.read_bytes(); path.write_bytes(original + b'changed')
                before = snapshot(self.repo)
                with self.assertRaisesRegex(N.BuildError, 'differs from the report'): self.recover()
                self.assertEqual(snapshot(self.repo), before)
                path.write_bytes(original)

    def test_missing_wrong_machine_or_changed_archive_outputs_are_rejected(self):
        object_path = self.output / self.names[0]; original = object_path.read_bytes()
        object_path.unlink()
        with self.assertRaisesRegex(N.BuildError, 'missing or empty'): self.recover()
        object_path.write_bytes(elf_object(machine=3))
        with self.assertRaisesRegex(N.BuildError, 'not x86_64'): self.recover()
        object_path.write_bytes(original)
        archive = self.output / 'libcrypto.a'; archive.write_bytes(ar_library(objects=941))
        with self.assertRaisesRegex(N.BuildError, 'differs from its installed prefix'): self.recover()
        self.assertFalse((self.output / '.ninja_log').exists())
        self.assertFalse((self.root / 'ninja-state-backups').exists())

    def test_exact_set_is_required_even_when_report_matches_forged_log_bytes(self):
        for names in (self.names[:-1], self.names[:-1] + [self.names[0]], self.names[:-1] + ['unrelated.o']):
            with self.subTest(last=names[-1]):
                (self.output / '.ninja_log.restat').write_bytes(history(names))
                self.report_path.write_bytes(N.B.json_bytes(D.collect(self.repo)))
                with self.assertRaisesRegex(N.BuildError, 'exactly the pinned'): self.recover()
                self.assertFalse((self.output / '.ninja_log').exists())

    def test_existing_different_log_never_overwritten_and_report_raw_required(self):
        path = self.output / '.ninja_log'; path.write_bytes(b'user data')
        with self.assertRaisesRegex(N.BuildError, 'different active Ninja log'): self.recover()
        self.assertEqual(path.read_bytes(), b'user data'); path.unlink()
        report = N.B.read_json(self.report_path); report['files']['.ninja_log.restat']['raw']['text'] += 'changed'
        self.report_path.write_bytes(N.B.json_bytes(report))
        with self.assertRaisesRegex(N.BuildError, 'exact restat bytes'): self.recover()
        self.assertFalse(path.exists())

    def test_malformed_report_and_invalid_generation_are_rejected(self):
        original = self.report_path.read_bytes(); self.report_path.write_bytes(b'null')
        with self.assertRaisesRegex(N.BuildError, 'complete report'): self.recover()
        self.report_path.write_bytes(original)
        (self.root / 'work/openssl-3.4.1/Configure').write_bytes(b'changed generation')
        with self.assertRaisesRegex(N.BuildError, 'generation area'): self.recover()
        self.assertFalse((self.output / '.ninja_log').exists())


if __name__ == '__main__': unittest.main()
