"""Preservation and artifact-binding checks for the separate Alliance game stages."""
import hashlib
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
        self.assertIn('versionCode 1192601', (ROOT / 'tools/native-account-game/app.build.gradle').read_text())
        data = (ROOT / 'tools/native-account-game/OfflineDataInstaller.java').read_text()
        self.assertIn('main.1192600.org.wesnoth.offline.obb', data)
        self.assertNotIn('usesCleartextTraffic="true"', manifest)


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
