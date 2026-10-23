"""Actual C++ companion entry with real JSON/profile/certificate validation.

Shared launch/results and video mode are scripted boundaries. This does not
run Android, execute the engine, connect a socket, or use a real account.
"""
from pathlib import Path
import hashlib
import json
import os
import shutil
import ssl
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
START = 'ALLIANCE COMPANION STARTING: authenticated computer-controlled fixed seat'
FINISH = 'ALLIANCE COMPANION FINISHED: replay verified; status='
STOP = 'ALLIANCE COMPANION STOPPED: battle did not finish with a verified acknowledged result.'
ACCOUNT = '11111111-1111-4111-8111-111111111111'
RUN = '22222222-2222-4222-8222-222222222222'
TOKEN = 't' * 43


class CompanionEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='companion-entry-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.temp = Path(cls.temporary.name)
        cls.audit = Path(os.environ.get('ALLIANCE_COMPANION_ENTRY_AUDIT', cls.temp))
        cls.audit.mkdir(parents=True, exist_ok=True)
        stub = cls.audit / 'stub'; stub.mkdir(exist_ok=True)
        (stub / 'video.hpp').write_text('#pragma once\nnamespace video { bool headless(); }\n')
        cls.binary = cls.audit / 'companion_entry_probe'
        paths = ['tests/companion_entry_probe.cpp', 'alliance_game/companion.cpp',
                 'alliance_native/account_client.cpp', 'alliance_native/account_protocol.cpp', 'alliance_native/transport.cpp']
        command = [shutil.which(os.environ.get('CXX', 'c++')), '-std=c++17', '-O0', '-g',
                   '-Wall', '-Wextra', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                   '-DBOOST_ALL_NO_LIB', '-DBOOST_ERROR_CODE_HEADER_ONLY', '-I'+str(stub), '-I'+str(ROOT),
                   '-I'+str(ROOT / 'engine/src'), *[str(ROOT / name) for name in paths],
                   '-lssl', '-lcrypto', '-lz', '-pthread', '-o', str(cls.binary)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=240)
        (cls.audit / 'compile.log').write_text(result.stdout + result.stderr)
        if result.returncode: raise RuntimeError(result.stdout + result.stderr)
        cls.command, cls.sources, cls.observations = command, paths, []
        cert, key = cls.temp / 'cert.pem', cls.temp / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:prime256v1',
                        '-nodes', '-days', '1', '-subj', '/CN=127.0.0.1', '-addext', 'subjectAltName=IP:127.0.0.1',
                        '-addext', 'basicConstraints=critical,CA:FALSE', '-addext', 'keyUsage=critical,digitalSignature',
                        '-addext', 'extendedKeyUsage=serverAuth', '-keyout', str(key), '-out', str(cert)],
                       capture_output=True, check=True, timeout=20)
        pem = cert.read_text()
        cls.profile_value = {'format': 1, 'protocol': 'wesnoth-alliance-tls', 'server_address': '127.0.0.1', 'port': 15001,
                             'minimum_tls': '1.2', 'certificate_pem': pem,
                             'certificate_sha256': hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()}
        cls.profile = cls.temp / 'alliance-public-profile.json'; cls.profile.write_text(json.dumps(cls.profile_value))

    @classmethod
    def tearDownClass(cls):
        (cls.audit / 'validation.json').write_text(json.dumps({'command': cls.command, 'observations': cls.observations,
            'limitations': __doc__, 'sources': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in cls.sources}}, indent=2)+'\n')

    def run_case(self, mode='acknowledged', value=None, raw=None, expected=1, profile=True):
        if value is None: value = {'run_id': RUN, 'account_id': ACCOUNT, 'token': TOKEN, 'intent': 'result_retry' if mode=='retry' else 'play'}
        environment = os.environ.copy(); environment.pop('WESNOTH_ALLIANCE_PROFILE', None)
        if profile: environment['WESNOTH_ALLIANCE_COMPANION_PROFILE'] = str(self.profile)
        else: environment.pop('WESNOTH_ALLIANCE_COMPANION_PROFILE', None)
        result = subprocess.run([str(self.binary), mode], input=raw if raw is not None else json.dumps(value)+'\n',
                                env=environment, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, expected, result.stdout+result.stderr)
        lines = (result.stdout+result.stderr).splitlines()
        self.assertTrue(all(line in (START, FINISH+'acknowledged', FINISH+'pending', STOP) for line in lines), lines)
        self.assertNotIn(TOKEN, result.stdout+result.stderr)
        self.assertNotIn(str(self.profile), result.stdout+result.stderr)
        self.assertNotIn('PRIVATE_', result.stdout+result.stderr)
        self.observations.append({'mode': mode, 'exit': result.returncode, 'markers': lines})
        return lines

    def test_acknowledged_exact_account_run_and_replay_exit_zero(self):
        self.assertEqual(self.run_case(expected=0), [START, FINISH+'acknowledged'])

    def test_explicit_result_retry_is_separate_admission(self):
        self.assertEqual(self.run_case('retry', expected=0), [START, FINISH+'acknowledged'])

    def test_pending_preserved_result_returns_two(self):
        self.assertEqual(self.run_case('pending', expected=2), [START, FINISH+'pending'])

    def test_failed_launch_or_record_or_replay_never_claims_success(self):
        for mode in ('queue_failure', 'run_failure', 'missing_record', 'wrong_run', 'replay_failure'):
            with self.subTest(mode=mode): self.assertEqual(self.run_case(mode)[-1], STOP)

    def test_requires_explicit_profile_and_headless_mode(self):
        self.assertEqual(self.run_case(profile=False), [STOP])
        self.assertEqual(self.run_case('nonheadless'), [STOP])

    def test_requires_exact_four_fields_and_known_intent(self):
        valid = {'run_id': RUN, 'account_id': ACCOUNT, 'token': TOKEN, 'intent': 'play'}
        cases = [dict(valid, extra='private'), dict(valid, intent='download'), dict(valid, intent=0), dict(valid, token='t'*44)]
        cases += [{key: value for key, value in valid.items() if key != missing} for missing in valid]
        for value in cases:
            with self.subTest(value=list(value)): self.assertEqual(self.run_case(value=value), [STOP])

    def test_rejects_duplicate_deep_malformed_and_oversized_stdin(self):
        valid = json.dumps({'run_id': RUN, 'account_id': ACCOUNT, 'token': TOKEN, 'intent': 'play'})
        for raw in (valid[:-1]+',"intent":"play"}\n', '{"a":'+ '['*16+'0'+']'*16+'}\n', '['+valid+']\n', 'invalid\n', ' '*4097+'\n', valid+'x\n'):
            with self.subTest(raw_length=len(raw)): self.assertEqual(self.run_case(raw=raw), [STOP])

    def test_real_reader_rejects_changed_profile_pin_without_private_output(self):
        original = self.profile.read_bytes()
        try:
            self.profile.write_text(json.dumps(dict(self.profile_value, certificate_sha256='0'*64)))
            self.assertEqual(self.run_case(), [STOP])
        finally: self.profile.write_bytes(original)


if __name__ == '__main__': unittest.main()
