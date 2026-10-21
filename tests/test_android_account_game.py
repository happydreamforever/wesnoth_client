"""Preservation and artifact-binding checks for the separate Alliance game stages."""
import hashlib
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result

A = module('account_game_tests', ROOT / 'tools/build_android_account_game.py')
P = module('account_profile_tests', ROOT / 'tools/validate_android_account_profile.py')

class AccountBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.engine = self.repo / 'build/android-engine'; (self.engine / 'build').mkdir(parents=True)
        self.receipt = {'inputs': {'pinned': 'engine'}}
        for name in ('CMakeCache.txt', 'build.ninja', 'CMakeFiles/rules.ninja', 'config.h'):
            path = self.engine / 'build' / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'fixed-config\n')
        self.config = {'kind': A.E.KIND + '-configured', 'schema_version': 1,
            'binding': {'inputs': self.receipt['inputs']},
            'files': {name: A.fingerprint(self.engine / 'build' / name) for name in
                      ('CMakeCache.txt', 'build.ninja', 'CMakeFiles/rules.ninja', 'config.h')}}
        (self.engine / 'configured.json').write_text(json.dumps(self.config))
        self.object = self.engine / 'build/CMakeFiles/main.dir/drive_/workspace/engine/src/wesnoth.cpp.o'
        self.object.parent.mkdir(parents=True)
        header = bytearray(64); header[:7] = b'\x7fELF\x02\x01\x01'; struct.pack_into('<HHI', header, 16, 1, 62, 1)
        self.object.write_bytes(header + b'object-content')
    def collect(self):
        with patch.object(A, 'source_names', return_value=['wesnoth.cpp']):
            return A.base_objects(self.repo, self.receipt)
    def test_object_bytes_and_configuration_are_bound(self):
        result = self.collect()
        self.assertEqual(result['objects']['wesnoth.cpp']['sha256'], hashlib.sha256(self.object.read_bytes()).hexdigest())
        self.assertEqual(result['configuration'], self.config['files'])
    def test_changed_configuration_is_rejected(self):
        (self.engine / 'build/build.ninja').write_bytes(b'changed')
        with self.assertRaises(A.BuildError): self.collect()
    def test_wrong_configuration_inputs_are_rejected(self):
        self.receipt['inputs'] = {'different': 'source'}
        with self.assertRaises(A.BuildError): self.collect()
    def test_missing_object_is_rejected(self):
        self.object.unlink()
        with self.assertRaises(A.BuildError): self.collect()
    def test_wrong_architecture_is_rejected(self):
        data = bytearray(self.object.read_bytes()); struct.pack_into('<H', data, 18, 183); self.object.write_bytes(data)
        with self.assertRaises(A.BuildError): self.collect()
    def test_duplicate_object_is_rejected(self):
        target = self.engine / 'build/CMakeFiles/main.dir/another/engine/src/wesnoth.cpp.o'
        target.parent.mkdir(parents=True); target.write_bytes(self.object.read_bytes())
        with self.assertRaises(A.BuildError): self.collect()
    def test_unlisted_object_is_rejected(self):
        self.object.with_name('surprise.cpp.o').write_bytes(self.object.read_bytes())
        with self.assertRaises(A.BuildError): self.collect()
    def test_object_symlink_is_rejected(self):
        target = self.object.with_name('alias.cpp.o'); target.symlink_to(self.object)
        with self.assertRaises((A.BuildError, OSError)): self.collect()
    def test_owned_stage_will_not_replace_inputs(self):
        root = self.repo / 'stage'; A.prepare_owned(root, 'kind', 'owner.json', {'in': 1}, {'source': b'original'})
        with self.assertRaises(A.BuildError): A.prepare_owned(root, 'kind', 'owner.json', {'in': 2}, {'source': b'new'})
        self.assertEqual((root / 'source').read_bytes(), b'original')
    def test_owned_stage_rejects_tampering(self):
        root = self.repo / 'stage'; A.prepare_owned(root, 'kind', 'owner.json', {}, {'source': b'original'})
        (root / 'source').write_bytes(b'changed')
        with self.assertRaises(A.BuildError): A.verify_owned(root, 'kind', 'owner.json', {})
    def test_cmake_paths_with_spaces_work_and_injection_is_rejected(self):
        self.assertEqual(A.cmake_literal(Path('/tmp/with spaces/object.o')), '"/tmp/with spaces/object.o"')
        for value in ('/tmp/a;b', '/tmp/${var}', '/tmp/a\nb', '/tmp/a"b'):
            with self.subTest(value=value), self.assertRaises(A.BuildError): A.cmake_literal(value)
    def test_exact_title_overlay_preserves_original(self):
        original = ROOT / 'engine/src/gui/dialogs/title_screen.cpp'; before = original.read_bytes()
        result = A.title_overlay(ROOT)
        self.assertIn(b'alliance_game::show_account_dialog();', result)
        self.assertEqual(original.read_bytes(), before)
        self.assertNotIn(b'if(manage_addons())', result)
    def test_templates_keep_package_data_and_no_plaintext(self):
        manifest = (ROOT / 'tools/native-account-game/AndroidManifest.xml').read_text()
        self.assertIn('.AccountSetupActivity', manifest); self.assertIn('android:allowBackup="false"', manifest)
        self.assertIn('versionCode 1192605', (ROOT / 'tools/native-account-game/app.build.gradle').read_text())
        data = (ROOT / 'tools/native-account-game/OfflineDataInstaller.java').read_text()
        self.assertIn('main.1192600.org.wesnoth.offline.obb', data)
        self.assertNotIn('usesCleartextTraffic="true"', manifest)


class AccountUpdateTests(unittest.TestCase):
    """Real files/receipts, with ELF and APK decoding isolated from tiny fixtures."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='account update with spaces ')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        (self.repo / 'build').mkdir()
        self.profile = {'fixture': 'pinned profile'}
        self.prior = {'original': {'bytes': 4, 'sha256': hashlib.sha256(b'base').hexdigest()}}
        self.old_inputs = {'project_files': {'account.cpp': {'bytes': 3, 'sha256': hashlib.sha256(b'old').hexdigest()}},
                           'prerequisites': self.prior}
        self.current_inputs = copy.deepcopy(self.old_inputs)
        self.current_inputs['project_files']['account.cpp'] = {'bytes': 3, 'sha256': hashlib.sha256(b'new').hexdigest()}
        self.engine = self.repo / 'build/android-account-engine'
        self.game = self.repo / 'build/android-account-game'
        self.engine_result = {'fixture': 'validated ELF'}
        self.native_result = {'fixture': 'validated payload'}
        marker = A.prepare_owned(self.engine, A.ENGINE_KIND, A.ENGINE_MARKER, self.old_inputs, {'title_screen.cpp': b'old title'})
        self.engine_receipt = {'kind': A.ENGINE_KIND, 'schema_version': 1, 'inputs': self.old_inputs,
                              'files': marker['files'], 'validation': self.engine_result, 'device_executed': False}
        A.write_new(self.engine / 'receipt.json', A.B.json_bytes(self.engine_receipt))
        game_inputs = dict(self.old_inputs, account_engine_receipt=A.fingerprint(self.engine / 'receipt.json'))
        marker = A.prepare_owned(self.game, A.KIND, A.MARKER, game_inputs, {'app/build.gradle': b'versionCode 1192604'})
        self.apk = self.game / 'old.apk'
        self.apk.write_bytes(b'completed apk')
        (self.game / 'empty bookkeeping').write_bytes(b'')
        (self.engine / 'empty directory').mkdir()
        self.game_receipt = {'schema_version': 1, 'kind': A.KIND, 'profile': self.profile, 'inputs': game_inputs,
            'project_files': marker['files'], 'validation': {'libraries': self.native_result,
                'apk': {'version_code': A.PREVIOUS_VERSION_CODE}},
            'artifacts': {'old.apk': A.fingerprint(self.apk)}, 'device_executed': False}
        A.write_new(self.game / 'receipt.json', A.B.json_bytes(self.game_receipt))
        for name, value in (('engine_validation', lambda *args: self.engine_result),
                            ('native_validation', lambda *args: self.native_result),
                            ('inspect_apk', lambda *args: {'version_code': A.VERSION_CODE}),
                            ('selected_inputs', lambda *args: (self.profile, copy.deepcopy(self.current_inputs), {})),
                            ('prerequisites', lambda *args: copy.deepcopy(self.prior))):
            patcher = patch.object(A, name, side_effect=value)
            patcher.start(); self.addCleanup(patcher.stop)
        for name, value in (('APK', 'old.apk'), ('ARTIFACTS', ('old.apk',))):
            patcher = patch.object(A, name, value); patcher.start(); self.addCleanup(patcher.stop)
        self.original = {name: A.stage_snapshot(self.repo / 'build' / name) for name in A.UPDATE_STAGES}
        self.protected = self.repo / 'build/android-engine/original'
        self.protected.parent.mkdir(); self.protected.write_bytes(b'original engine objects/OBB unaffected')
        # All three prior journals and 1192601/1192602/1192603 backups coexist
        # with the completed 1192604 stages. This update must never reuse,
        # parse as its own, replace, or delete any older transaction.
        self.previous_delivery = {
            self.repo / 'build/.wesnoth-account-game-update-1192602.json': b'old immutable update journal\n',
            self.repo / 'build/android-account-engine-before-team-ui-20261020T000000Z-0123456789abcdef0123456789abcdef/receipt.json': b'old 1192601 engine receipt\n',
            self.repo / 'build/android-account-game-before-team-ui-20261020T000000Z-0123456789abcdef0123456789abcdef/receipt.json': b'old 1192601 APK receipt\n',
            self.repo / 'build/.wesnoth-account-game-update-1192603.json': b'previous immutable membership update journal\n',
            self.repo / 'build/android-account-engine-before-membership-ui-20261021T000000Z-fedcba9876543210fedcba9876543210/receipt.json': b'old 1192602 engine receipt\n',
            self.repo / 'build/android-account-game-before-membership-ui-20261021T000000Z-fedcba9876543210fedcba9876543210/receipt.json': b'old 1192602 APK receipt\n',
            self.repo / 'build/.wesnoth-account-game-update-1192604.json': b'previous immutable decisions update journal\n',
            self.repo / 'build/android-account-engine-before-decisions-ui-20261021T010000Z-1123456789abcdef0123456789abcdef/receipt.json': b'old 1192603 engine receipt\n',
            self.repo / 'build/android-account-game-before-decisions-ui-20261021T010000Z-1123456789abcdef0123456789abcdef/receipt.json': b'old 1192603 APK receipt\n',
        }
        for path, data in self.previous_delivery.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def prepare(self):
        try:
            return A.prepare_update(self.repo)
        finally:
            self.assertEqual(self.protected.read_bytes(), b'original engine objects/OBB unaffected')
            for path, data in self.previous_delivery.items():
                self.assertEqual(path.read_bytes(), data)

    def assert_untouched(self):
        for name in A.UPDATE_STAGES:
            self.assertEqual(A.stage_snapshot(self.repo / 'build' / name), self.original[name])
        self.assertFalse((self.repo / 'build' / A.UPDATE_JOURNAL).exists())

    def test_changed_source_binding_preserves_both_old_stages_byte_for_byte(self):
        record = self.prepare()
        self.assertEqual(record['target_version'], 1192605)
        self.assertEqual(A.UPDATE_JOURNAL, '.wesnoth-account-game-update-1192605.json')
        for name, entry in record['stages'].items():
            self.assertFalse((self.repo / 'build' / name).exists())
            self.assertEqual(A.stage_snapshot(self.repo / 'build' / entry['backup']), self.original[name])
        self.assertFalse((self.repo / 'build' / A.LOCK).exists())

    def test_repeated_preparation_keeps_same_backups(self):
        first = self.prepare()
        self.assertEqual(self.prepare(), first)
        self.assertEqual(len(list((self.repo / 'build').glob('*-before-management-ui-*'))), 2)

    def test_current_partial_build_is_left_in_place_for_normal_builder_resume(self):
        record = self.prepare()
        A.prepare_owned(self.engine, A.ENGINE_KIND, A.ENGINE_MARKER, self.current_inputs,
                        {'title_screen.cpp': b'new title'})
        (self.engine / 'partial.o').write_bytes(b'partial new object')
        self.assertEqual(self.prepare(), record)
        self.assertEqual((self.engine / 'partial.o').read_bytes(), b'partial new object')

    def test_current_engine_and_apk_stages_can_be_resumed(self):
        record = self.prepare()
        A.prepare_owned(self.engine, A.ENGINE_KIND, A.ENGINE_MARKER, self.current_inputs,
                        {'title_screen.cpp': b'new title'})
        (self.engine / 'receipt.json').write_bytes(b'completed current engine')
        inputs = dict(self.current_inputs, account_engine_receipt=A.fingerprint(self.engine / 'receipt.json'))
        A.prepare_owned(self.game, A.KIND, A.MARKER, inputs, {'app/build.gradle': b'versionCode 1192605'})
        self.assertEqual(self.prepare(), record)

    def test_missing_or_incomplete_old_stage_is_not_blindly_backed_up(self):
        (self.game / 'receipt.json').unlink()
        with self.assertRaises((A.BuildError, OSError)): self.prepare()
        self.assertTrue(self.engine.exists()); self.assertTrue(self.game.exists())
        self.assertFalse((self.repo / 'build' / A.UPDATE_JOURNAL).exists())

    def test_tampered_old_apk_is_refused_before_any_move(self):
        self.apk.write_bytes(b'changed old apk')
        with self.assertRaises(A.BuildError): self.prepare()
        self.assertTrue(self.engine.exists()); self.assertTrue(self.game.exists())
        self.assertFalse((self.repo / 'build' / A.UPDATE_JOURNAL).exists())

    def test_stage_from_other_release_is_not_accepted_as_current_previous(self):
        for version in (1192600, 1192601, 1192602, 1192603, 1192605):
            with self.subTest(version=version):
                self.game_receipt['validation']['apk']['version_code'] = version
                (self.game / 'receipt.json').write_bytes(A.B.json_bytes(self.game_receipt))
                with self.assertRaises(A.BuildError): self.prepare()
                self.assertTrue(self.engine.exists()); self.assertTrue(self.game.exists())
                self.assertFalse((self.repo / 'build' / A.UPDATE_JOURNAL).exists())

    def test_wrong_version_journal_is_preserved_without_resuming_it(self):
        record = self.prepare()
        for version in (1192604, 1192606):
            with self.subTest(version=version):
                record['target_version'] = version
                journal = self.repo / 'build' / A.UPDATE_JOURNAL
                saved = A.B.json_bytes(record)
                journal.write_bytes(saved)
                with self.assertRaises(A.BuildError): self.prepare()
                self.assertEqual(journal.read_bytes(), saved)
                for name, entry in record['stages'].items():
                    self.assertFalse((self.repo / 'build' / name).exists())
                    self.assertEqual(A.stage_snapshot(self.repo / 'build' / entry['backup']), self.original[name])

    def test_current_journal_cannot_redirect_into_an_older_update_backup(self):
        record = self.prepare()
        for backup in (
                'android-account-engine-before-team-ui-20261020T000000Z-0123456789abcdef0123456789abcdef',
                'android-account-engine-before-membership-ui-20261021T000000Z-fedcba9876543210fedcba9876543210',
                'android-account-engine-before-decisions-ui-20261021T010000Z-1123456789abcdef0123456789abcdef'):
            with self.subTest(backup=backup):
                record['stages']['android-account-engine']['backup'] = backup
                (self.repo / 'build' / A.UPDATE_JOURNAL).write_bytes(A.B.json_bytes(record))
                with self.assertRaises(A.BuildError): self.prepare()

    def test_cross_stage_receipt_mismatch_is_refused(self):
        self.game_receipt['inputs']['account_engine_receipt']['sha256'] = '0' * 64
        (self.game / 'receipt.json').write_text(__import__('json').dumps(self.game_receipt))
        with self.assertRaises(A.BuildError): self.prepare()
        self.assertTrue(self.engine.exists()); self.assertTrue(self.game.exists())
        self.assertFalse((self.repo / 'build' / A.UPDATE_JOURNAL).exists())

    def test_changed_prerequisite_rejected_before_moves(self):
        self.prior['new prerequisite'] = 'unexpected'
        with self.assertRaises(A.BuildError): self.prepare()
        self.assert_untouched()

    def test_mid_validation_mutation_is_rejected(self):
        def mutate(*args):
            (self.engine / 'unexpected concurrent file').write_bytes(b'changed')
            return self.engine_result
        with patch.object(A, 'engine_validation', side_effect=mutate), self.assertRaises(A.BuildError):
            self.prepare()
        self.assertTrue(self.engine.exists()); self.assertTrue(self.game.exists())
        self.assertFalse((self.repo / 'build' / A.UPDATE_JOURNAL).exists())

    def test_interrupted_second_move_resumes_from_the_immutable_journal(self):
        original_rename = Path.rename
        failures = []
        def rename(path, target):
            if path == self.game:
                failures.append(path)
                error = PermissionError('simulated Windows sharing violation'); error.winerror = 32
                raise error
            return original_rename(path, target)
        with patch.object(Path, 'rename', rename), patch.object(A.time, 'sleep'), self.assertRaises(PermissionError):
            self.prepare()
        self.assertEqual(len(failures), len(A.N.NINJA_RETRY_DELAYS) + 1)
        self.assertFalse(self.engine.exists()); self.assertTrue(self.game.exists())
        journal_bytes = (self.repo / 'build' / A.UPDATE_JOURNAL).read_bytes()
        record = self.prepare()
        self.assertEqual((self.repo / 'build' / A.UPDATE_JOURNAL).read_bytes(), journal_bytes)
        for name, entry in record['stages'].items():
            self.assertEqual(A.stage_snapshot(self.repo / 'build' / entry['backup']), self.original[name])

    def test_success_after_one_sharing_failure_preserves_exact_bytes(self):
        original_rename = Path.rename
        count = []
        def rename(path, target):
            count.append(path)
            if len(count) == 1:
                error = PermissionError('sharing'); error.winerror = 5; raise error
            return original_rename(path, target)
        with patch.object(Path, 'rename', rename), patch.object(A.time, 'sleep'):
            record = self.prepare()
        self.assertEqual(len(count), 3)
        for name, entry in record['stages'].items():
            self.assertEqual(A.stage_snapshot(self.repo / 'build' / entry['backup']), self.original[name])

    def test_changed_source_after_sharing_failure_stops_retries(self):
        count = []
        def rename(path, target):
            count.append(path); (path / 'concurrent-file').write_bytes(b'changed')
            error = PermissionError('sharing'); error.winerror = 32; raise error
        with patch.object(Path, 'rename', rename), patch.object(A.time, 'sleep'), self.assertRaises(A.BuildError):
            self.prepare()
        self.assertEqual(len(count), 1)
        self.assertTrue(self.engine.exists()); self.assertTrue(self.game.exists())

    def test_other_stage_backup_mutated_during_second_move_is_detected(self):
        original_rename = Path.rename
        first_backup = []
        def rename(path, target):
            result = original_rename(path, target)
            if path == self.engine:
                first_backup.append(target)
            elif path == self.game:
                (first_backup[0] / 'concurrent mutation').write_bytes(b'changed')
            return result
        with patch.object(Path, 'rename', rename), self.assertRaises(A.BuildError):
            self.prepare()
        self.assertFalse(self.engine.exists()); self.assertFalse(self.game.exists())
        self.assertEqual((first_backup[0] / 'concurrent mutation').read_bytes(), b'changed')

    def test_changed_backup_and_changed_new_stage_are_not_replaced(self):
        record = self.prepare()
        backup = self.repo / 'build' / record['stages']['android-account-engine']['backup']
        (backup / 'unexpected').write_bytes(b'changed backup')
        with self.assertRaises(A.BuildError): self.prepare()
        self.assertEqual((backup / 'unexpected').read_bytes(), b'changed backup')

    def test_new_source_change_rejects_existing_transaction(self):
        self.prepare()
        self.current_inputs['project_files']['account.cpp']['sha256'] = '0' * 64
        with self.assertRaises(A.BuildError): self.prepare()
        self.assertEqual(len(list((self.repo / 'build').glob('*-before-management-ui-*'))), 2)

    def test_symlink_within_stage_and_traversal_journal_are_rejected(self):
        (self.engine / 'alias').symlink_to(self.apk)
        with self.assertRaises(A.BuildError): self.prepare()
        self.assertFalse((self.repo / 'build' / A.UPDATE_JOURNAL).exists())
        (self.engine / 'alias').unlink()
        record = self.prepare()
        record['stages']['android-account-engine']['backup'] = '../original'
        (self.repo / 'build' / A.UPDATE_JOURNAL).write_bytes(A.B.json_bytes(record))
        with self.assertRaises(A.BuildError): self.prepare()

    def test_existing_operation_lock_prevents_moves(self):
        (self.repo / 'build' / A.LOCK).write_bytes(b'active build')
        with self.assertRaises(A.BuildError): self.prepare()
        self.assert_untouched()
        self.assertEqual((self.repo / 'build' / A.LOCK).read_bytes(), b'active build')


class PublicProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); cls.root = Path(cls.temp.name)
        if not shutil.which('openssl'): raise unittest.SkipTest('OpenSSL required for real X509 fixture')
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:prime256v1',
                        '-nodes', '-keyout', str(cls.root / 'key.pem'), '-out', str(cls.root / 'cert.pem'),
                        '-days', '2', '-subj', '/CN=AccountTest', '-addext', 'subjectAltName=IP:100.99.99.14'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        import ssl
        pem = (cls.root / 'cert.pem').read_text()
        cls.profile = {'format': 1, 'protocol': 'wesnoth-alliance-tls', 'server_address': '100.99.99.14',
                       'port': 15001, 'minimum_tls': '1.2', 'certificate_pem': pem,
                       'certificate_sha256': hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()}
    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()
    def setUp(self): self.path = self.root / 'public-profile.json'; self.path.write_text(json.dumps(self.profile))
    def test_real_certificate_and_numeric_ip(self):
        self.assertEqual(P.validate(self.path), self.profile)
    def test_exact_validated_bytes_can_be_snapshotted(self):
        obj, data = P.validated_bytes(self.path)
        self.path.write_text('private replacement is never copied')
        self.assertEqual(json.loads(data), self.profile); self.assertEqual(obj, self.profile)
    def test_private_fields_are_rejected(self):
        value = dict(self.profile, private_key='SECRET'); self.path.write_text(json.dumps(value))
        with self.assertRaises(ValueError): P.validate(self.path)
    def test_duplicate_fields_are_rejected(self):
        self.path.write_text(self.path.read_text().replace('{', '{"format":1,', 1))
        with self.assertRaises(ValueError): P.validate(self.path)
    def test_pin_mismatch_and_hostname_are_rejected(self):
        for key, value in [('certificate_sha256', '0' * 64), ('server_address', 'example.test'), ('port', True), ('format', True)]:
            with self.subTest(key=key):
                obj = dict(self.profile); obj[key] = value; self.path.write_text(json.dumps(obj))
                with self.assertRaises(ValueError): P.validate(self.path)
    def test_size_bound_and_links(self):
        self.path.write_bytes(b' ' * 32769)
        with self.assertRaises(ValueError): P.validate(self.path)
        target = self.root / 'alias.json'
        if target.exists(): target.unlink()
        target.symlink_to(self.path)
        with self.assertRaises(ValueError): P.validate(target)


if __name__ == '__main__': unittest.main()
