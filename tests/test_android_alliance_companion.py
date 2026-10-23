"""Companion orchestration: actual loopback TLS and owned shell subprocesses.

ADB/Android and the actual engine are substituted. No Android execution,
account credentials, production database, readiness or reward is changed.
"""
import argparse
import contextlib
import copy
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('companion', ROOT / 'tools/run_android_alliance_companion.py')
C = importlib.util.module_from_spec(spec); spec.loader.exec_module(C)
ACCOUNT = '11111111-1111-4111-8111-111111111111'
RUN = '22222222-2222-4222-8222-222222222222'
TOKEN = 't' * 43
GENERATION = '33333333-3333-4333-8333-333333333333'


def account():
    return {'id': ACCOUNT, 'username': 'UniRecruit', 'database_generation': GENERATION, 'heroes': [
        {'hero_id': 'konrad', 'name': 'Konrad', 'hero_level': 2, 'experience': 7, 'total_experience': 27, 'revision': 1, 'forms': ['konrad_fighter']}] }


def selection():
    return {'hero_id': 'konrad', 'form_id': 'konrad_fighter', 'hero_level': 2, 'hero_revision': 1, 'unit_type': 'Alliance Konrad Fighter', 'unit_level': 1}


def battle():
    players = [{'id': ACCOUNT if i == 1 else f'{i:08d}-1111-4111-8111-111111111111', 'side': i, 'ready': True, 'selection': selection()} for i in range(1, 5)]
    return {'battle': {'id': RUN, 'state': 'draft', 'validity': 'valid', 'map': {'size': 2},
        'teams': [{'approved': True, 'captain_id': ACCOUNT, 'players': players[:2]}, {'approved': True, 'captain_id': players[2]['id'], 'players': players[2:]}]}}


class FixtureServer:
    def __init__(self, cert, key, handler=None, preamble=b'\0\0\0\0', version=2, requests=1):
        self.listener = socket.socket(); self.listener.bind(('127.0.0.1', 0)); self.listener.listen(1)
        self.listener.settimeout(10)
        self.port = self.listener.getsockname()[1]
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); self.context.load_cert_chain(cert, key)
        self.handler = handler or (lambda request: {'status': 'logged_out'})
        self.preamble = preamble; self.application = None; self.error = None
        self.version = version; self.requests = requests; self.applications = []

    def __enter__(self):
        def serve():
            try:
                for _ in range(self.requests):
                    with self.listener.accept()[0] as raw:
                        raw.settimeout(5)
                        if raw.recv(4) != b'\0\0\0\1': raise AssertionError('preamble differs')
                        raw.sendall(self.preamble)
                        if self.preamble != b'\0\0\0\0': return
                        with self.context.wrap_socket(raw, server_side=True) as stream:
                            size = struct.unpack('!I', C.exact_read(stream, 4, C.time.monotonic() + 5))[0]
                            self.application = json.loads(gzip.decompress(C.exact_read(stream, size, C.time.monotonic() + 5)))
                            request = self.application
                            self.applications.append(request)
                            reply = {'v': self.version, 'id': request['id'], 'ok': True, 'status': 200, 'data': self.handler(request)}
                            encoded = gzip.compress(json.dumps(reply).encode(), mtime=0)
                            stream.sendall(struct.pack('!I', len(encoded)) + encoded)
            except Exception as error:
                self.error = error
            finally:
                self.listener.close()
        self.thread = threading.Thread(target=serve, daemon=True); self.thread.start(); return self

    def __exit__(self, *args):
        self.thread.join(12)
        if self.thread.is_alive(): raise AssertionError('TLS fixture did not stop')


class CompanionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='companion-tests-')
        cls.root = Path(cls.temp.name)
        cls.cert, cls.key = cls.root / 'cert.pem', cls.root / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-subj', '/CN=Companion fixture', '-addext', 'subjectAltName=IP:127.0.0.1',
                        '-keyout', str(cls.key), '-out', str(cls.cert)], check=True, capture_output=True)
        pem = cls.cert.read_text()
        cls.profile = {'format': 1, 'protocol': 'wesnoth-alliance-tls', 'minimum_tls': '1.2',
                       'server_address': '127.0.0.1', 'port': 15001, 'certificate_pem': pem,
                       'certificate_sha256': hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()}

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def test_public_profile_exact_numeric_identity_and_pin(self):
        self.assertEqual(C.public_profile(json.dumps(self.profile).encode()), self.profile)
        for changes in ({'port': 27018}, {'server_address': 'localhost'}, {'server_address': '0.0.0.0'},
                        {'certificate_sha256': '0' * 64}, {'format': True}, {'minimum_tls': '1.0'}, {'secret': 'private'}):
            with self.subTest(changes=changes), self.assertRaises(C.CompanionError):
                C.public_profile(json.dumps(dict(self.profile, **changes)).encode())

    def test_duplicate_and_oversized_json_refused(self):
        for value in (b'{"a":1,"a":2}', b'{}' + b' ' * 32768, b'[]', b'\xff'):
            with self.assertRaises(C.CompanionError): C.document(value)

    def test_actual_tls_login_bound_response_and_no_plaintext_password(self):
        with FixtureServer(self.cert, self.key, lambda req: {'token': TOKEN, 'account': account()}) as server:
            profile = dict(self.profile, port=server.port)
            self.assertEqual(C.login(profile, 'UniRecruit', 'private-fixture-password'), (TOKEN, account()))
        self.assertIsNone(server.error)
        self.assertEqual(server.application['body']['password'], 'private-fixture-password')
        self.assertEqual(server.application['op'], 'login')
        self.assertEqual(server.application['v'], 2)

    def test_release_matches_current_builder(self):
        import re
        builder = (ROOT / 'tools/build_android_account_game.py').read_text()
        self.assertEqual(C.VERSION, int(re.search(r'^VERSION_CODE = (\d+)$', builder, re.M)[1]))
        self.assertEqual(C.WIRE_VERSION, 2)

    def test_actual_tls_v1_reply_is_refused(self):
        with FixtureServer(self.cert, self.key, version=1) as server:
            with self.assertRaisesRegex(C.CompanionError, 'invalid_account_response'):
                C.account_request(dict(self.profile, port=server.port), 'logout', {}, TOKEN)
        self.assertEqual(server.application['v'], 2)

    def test_actual_tls_large_owned_hero_profile_and_plain_bound(self):
        large = account()
        large['heroes'] = [dict(large['heroes'][0], hero_id='hero_' + str(i), name='N' * 128,
            forms=['form_' + str(n) for n in range(16)]) for i in range(117)]
        self.assertGreater(len(json.dumps(large)), 32768)
        with FixtureServer(self.cert, self.key, lambda req: {'token': TOKEN, 'account': large}) as server:
            self.assertEqual(C.login(dict(self.profile, port=server.port), 'UniRecruit', 'hidden'), (TOKEN, large))
        with FixtureServer(self.cert, self.key, lambda req: {'oversized': 'x' * C.PLAIN_LIMIT}) as server:
            with self.assertRaisesRegex(C.CompanionError, 'invalid_compression'):
                C.account_request(dict(self.profile, port=server.port), 'logout', {}, TOKEN)

    def test_owned_profile_strict_generation_and_integral_uncapped_levels(self):
        empty = account(); empty['heroes'] = []
        self.assertEqual(C.account_identity(empty, 'unirecruit'), empty)
        high = account(); high['heroes'][0]['hero_level'] = 100000
        self.assertEqual(C.account_identity(high, 'UniRecruit'), high)
        cases = [dict(account(), leader={}), dict(account(), database_generation='invalid'),
                 {'id': ACCOUNT, 'username': 'UniRecruit', 'leader': {}}]
        for field, value in [('hero_level', True), ('hero_level', 1.5), ('hero_level', 0),
                             ('revision', -1), ('total_experience', 1 << 63),
                             ('forms', ['same', 'same']), ('forms', ['bad\nform'])]:
            changed = account(); changed['heroes'][0][field] = value; cases.append(changed)
        changed = account(); changed['heroes'] *= 2; cases.append(changed)
        for changed in cases:
            with self.subTest(value=changed), self.assertRaises(C.CompanionError):
                C.account_identity(changed, 'UniRecruit')
        with mock.patch.object(C, 'account_request', side_effect=[{'token': TOKEN, 'account': cases[-1]}, {'status': 'logged_out'}]) as requests:
            with self.assertRaises(C.CompanionError): C.login(self.profile, 'UniRecruit', 'hidden')
        self.assertEqual(requests.call_args.args, (self.profile, 'logout', {}, TOKEN))

    def test_selected_hero_must_be_owned_current_form_and_integral_snapshot(self):
        for field, value in [('hero_id', 'another'), ('form_id', 'unowned_form'), ('hero_level', 3),
                             ('hero_revision', 2), ('hero_level', True), ('unit_level', 1.5),
                             ('unit_level', 21), ('hero_revision', -1)]:
            changed = battle(); changed['battle']['teams'][0]['players'][0]['selection'][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(C.CompanionError):
                C.selected_side(changed, RUN, account())
        for selected in (None, {}, dict(selection(), extra=True)):
            changed = battle(); changed['battle']['teams'][1]['players'][0]['selection'] = selected
            with self.assertRaises(C.CompanionError): C.selected_side(changed, RUN, account())
        empty = account(); empty['heroes'] = []
        with self.assertRaisesRegex(C.CompanionError, 'battle_hero_selection_changed'):
            C.selected_side(battle(), RUN, empty)

    def test_wrong_pin_rejected_before_application_data(self):
        with FixtureServer(self.cert, self.key) as server:
            with self.assertRaises(C.CompanionError):
                C.account_request(dict(self.profile, port=server.port, certificate_sha256='0' * 64), 'login', {'password': 'never-sent'})
        self.assertIsNone(server.application)

    def test_wrong_ip_rejected_before_application_data(self):
        with FixtureServer(self.cert, self.key) as server:
            original = socket.create_connection
            with mock.patch.object(C.socket, 'create_connection', side_effect=lambda *a, **k: original(('127.0.0.1', server.port), timeout=5)):
                with self.assertRaises(ssl.SSLCertVerificationError):
                    C.account_request(dict(self.profile, port=server.port, server_address='127.0.0.2'), 'login', {'password': 'never-sent'})
        self.assertIsNone(server.application)

    def test_plaintext_downgrade_rejected(self):
        with FixtureServer(self.cert, self.key, preamble=b'NOPE') as server:
            with self.assertRaisesRegex(C.CompanionError, 'account_tls_required'):
                C.account_request(dict(self.profile, port=server.port), 'login', {'password': 'never-sent'})
        self.assertIsNone(server.application)

    def test_actual_tls_logout_only_selected_token(self):
        with FixtureServer(self.cert, self.key) as server:
            self.assertEqual(C.account_request(dict(self.profile, port=server.port), 'logout', {}, TOKEN), {'status': 'logged_out'})
        self.assertEqual(server.application['token'], TOKEN)
        self.assertEqual(server.application['body'], {})

    def test_wrong_login_actor_revokes_issued_session(self):
        with mock.patch.object(C, 'account_request', side_effect=[{'token': TOKEN, 'account': dict(account(), username='Another')}, {'status': 'logged_out'}]) as request:
            with self.assertRaisesRegex(C.CompanionError, 'invalid_login_identity'): C.login(self.profile, 'UniRecruit', 'hidden')
        self.assertEqual(request.call_args.args, (self.profile, 'logout', {}, TOKEN))

    def test_selection_requires_all_ready_both_captains_and_exact_actor(self):
        self.assertEqual(C.selected_side(battle(), RUN, account()), 1)
        cases = []
        for key, value in (('state', 'completed'), ('state', 'cancelled'), ('validity', 'roster_changed'), ('id', ACCOUNT)):
            changed = battle(); changed['battle'][key] = value; cases.append(changed)
        for team in (0, 1):
            changed = battle(); changed['battle']['teams'][team]['approved'] = False; cases.append(changed)
            for player in (0, 1):
                changed = battle(); changed['battle']['teams'][team]['players'][player]['ready'] = False; cases.append(changed)
        changed = battle(); changed['battle']['teams'][1]['players'][0]['side'] = 1; cases.append(changed)
        for changed in cases:
            with self.assertRaises(C.CompanionError): C.selected_side(changed, RUN, account())
        with self.assertRaises(C.CompanionError): C.selected_side(battle(), RUN, dict(account(), id=RUN))

    def test_regular_rejects_redirected_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'file'; path.write_text('data')
            link = Path(tmp) / 'link'; link.symlink_to(path)
            with self.assertRaises(C.CompanionError): C.regular(link)

    def test_policy_requires_exact_supported_pair_and_cannot_retry(self):
        for action in C.CAPTAIN_ACTIONS:
            for turn in (1, 100):
                args = argparse.Namespace(captain_action=action, after_turn=turn, retry_result=False)
                self.assertEqual(C.captain_policy(args), {'captain_action': action, 'after_turn': turn})
        self.assertEqual(C.captain_policy(argparse.Namespace(retry_result=False)), {})
        for action, turn, retry in ((None, 1, False), ('surrender', None, False), ('arbitrary_lua', 1, False),
                                    ('surrender', 0, False), ('surrender', 101, False), ('surrender', True, False),
                                    ('surrender', '2', False), ('surrender', 2, True)):
            with self.subTest(action=action, turn=turn), self.assertRaisesRegex(C.CompanionError, 'invalid_captain_policy'):
                C.captain_policy(argparse.Namespace(captain_action=action, after_turn=turn, retry_result=retry))

    def test_policy_selected_account_must_be_current_captain(self):
        self.assertEqual(C.selected_side(battle(), RUN, account(), True), 1)
        changed = battle(); changed['battle']['teams'][0]['captain_id'] = RUN
        self.assertEqual(C.selected_side(changed, RUN, account()), 1)
        with self.assertRaisesRegex(C.CompanionError, 'account_not_selected_captain'):
            C.selected_side(changed, RUN, account(), True)
        changed['battle']['teams'][0]['captain_id'] = ACCOUNT
        changed['battle']['teams'][0]['players'][0]['id'] = RUN
        with self.assertRaisesRegex(C.CompanionError, 'account_not_selected'):
            C.selected_side(changed, RUN, account(), True)

    def test_capture_is_bounded_and_does_not_emit_untrusted_output(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(C.CompanionError):
                C.capture([os.sys.executable, '-c', 'print("PRIVATE"*10000)'], limit=32)
        self.assertEqual(output.getvalue(), '')

    def make_payload(self, root, code=None):
        stage = root / 'stage'; stage.mkdir()
        binary = stage / 'wesnoth-engine-cli'
        binary.write_text('#!' + os.sys.executable + '\n' + (code or '''import json,sys
value=json.loads(sys.stdin.readline())
assert len(value['token'])==43
assert '--language' in sys.argv and 'en_US' in sys.argv
assert '--userdata-dir' in sys.argv and '--usercache-dir' in sys.argv
print('PRIVATE '+value['token'])
print(''' + repr(C.START) + ''',flush=True)
print(''' + repr(C.FINISHED + 'acknowledged') + ''',flush=True)
'''))
        for name in C.LIBRARIES: (stage / name).write_bytes(b'fixture-elf')
        hashes = {p.name: C.digest(p) for p in stage.iterdir()}
        private = root / 'app'; (private / 'files').mkdir(parents=True)
        (private / 'files/alliance-public-profile.json').write_text('public-profile-fixture')
        return stage, private, hashes

    def shell_device(self):
        # Execute the exact shipped remote script in a real local shell. The
        # only substituted boundary is ADB's run-as shell-command wrapper.
        class Local:
            base = [os.sys.executable, '-c', "import os,shlex,sys; a=shlex.split(sys.argv[-1]); os.execvp('sh',['sh','-c',a[-1]])"]
        return Local()

    def test_real_owned_shell_stdin_markers_cleanup_and_stable_reconnect_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); stage, private, hashes = self.make_payload(root)
            messages = []
            paths = []
            for launch in ('a' * 32, 'b' * 32):
                session, script = C.launch_script(str(private), str(stage), ACCOUNT, RUN, launch, '/read-only/gamedata', hashes, hashlib.sha256(b'public-profile-fixture').hexdigest())
                paths.append(session)
                self.assertNotIn(TOKEN, script)
                result = C.run_native(self.shell_device(), script, {'run_id': RUN, 'account_id': ACCOUNT, 'token': TOKEN, 'intent': 'play'}, session, emit=lambda s, **kw: messages.append(s))
                self.assertEqual(result, 'acknowledged')
                owner = private / 'files/alliance-companions' / ACCOUNT
                self.assertFalse((owner / 'active').exists())
                self.assertFalse((Path(session) / 'launcher.pid').exists())
                self.assertEqual((owner / RUN / 'owner').read_text().strip(), 'alliance-companion-v1 ' + ACCOUNT + ' ' + RUN)
                self.assertEqual(list((private / 'files/alliance-companions').glob('slot-*')), [])
            self.assertNotEqual(paths[0], paths[1])
            self.assertEqual(Path(paths[0]).parent, Path(paths[1]).parent)
            self.assertEqual(messages, [C.START, C.FINISHED + 'acknowledged'] * 2)
            self.assertFalse(any(TOKEN in p.read_text(errors='ignore') for p in private.rglob('*') if p.is_file()))

    def test_real_shell_refuses_changed_payload_before_engine_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); stage, private, hashes = self.make_payload(root)
            (stage / 'libmain.so').write_bytes(b'changed')
            session, script = C.launch_script(str(private), str(stage), ACCOUNT, RUN, 'a' * 32, '/data', hashes, hashlib.sha256(b'public-profile-fixture').hexdigest())
            with self.assertRaises(C.CompanionError): C.run_native(self.shell_device(), script, {'token': TOKEN}, session)
            self.assertFalse((private / 'files/alliance-companions' / ACCOUNT / 'active').exists())

    def test_real_shell_selects_compiled_dummy_sdl_drivers_for_headless_child(self):
        # --nogui still initializes SDL video. A CLI launched with run-as has
        # no Android SDLActivity, so inherited Android drivers must not win.
        code = "import os,json,sys\njson.loads(sys.stdin.readline())\n" \
            "assert os.environ['SDL_VIDEO_DRIVER']=='dummy'\n" \
            "assert os.environ['SDL_AUDIO_DRIVER']=='dummy'\n" \
            "assert os.environ['SDL_VIDEODRIVER']=='dummy'\n" \
            "assert os.environ['SDL_AUDIODRIVER']=='dummy'\n" \
            "print(" + repr(C.START) + ",flush=True)\n" \
            "print(" + repr(C.FINISHED + 'acknowledged') + ",flush=True)\n"
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ,
                {'SDL_VIDEO_DRIVER': 'android', 'SDL_AUDIO_DRIVER': 'openslES',
                 'SDL_VIDEODRIVER': 'android', 'SDL_AUDIODRIVER': 'openslES'}):
            stage, private, hashes = self.make_payload(Path(tmp), code)
            session, script = C.launch_script(str(private), str(stage), ACCOUNT, RUN, 'a' * 32,
                '/data', hashes, hashlib.sha256(b'public-profile-fixture').hexdigest())
            self.assertEqual(C.run_native(self.shell_device(), script, {'token': TOKEN}, session,
                emit=lambda *a, **k: None), 'acknowledged')
            self.assertEqual(os.environ['SDL_VIDEODRIVER'], 'android')
            self.assertEqual(os.environ['SDL_AUDIODRIVER'], 'openslES')
            self.assertEqual(os.environ['SDL_VIDEO_DRIVER'], 'android')
            self.assertEqual(os.environ['SDL_AUDIO_DRIVER'], 'openslES')

    def test_real_shell_account_lock_prevents_duplicate_without_removing_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); stage, private, hashes = self.make_payload(root)
            lock = private / 'files/alliance-companions' / ACCOUNT / 'active'; lock.mkdir(parents=True)
            session, script = C.launch_script(str(private), str(stage), ACCOUNT, RUN, 'a' * 32, '/data', hashes, hashlib.sha256(b'public-profile-fixture').hexdigest())
            with self.assertRaises(C.CompanionError): C.run_native(self.shell_device(), script, {'token': TOKEN}, session)
            self.assertTrue(lock.is_dir())

    def test_real_shell_seven_slot_cap_preserves_existing_slots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); stage, private, hashes = self.make_payload(root)
            base = private / 'files/alliance-companions'; base.mkdir()
            for i in range(1, 8): (base / ('slot-' + str(i))).mkdir()
            session, script = C.launch_script(str(private), str(stage), ACCOUNT, RUN, 'a' * 32, '/data', hashes, hashlib.sha256(b'public-profile-fixture').hexdigest())
            with self.assertRaises(C.CompanionError): C.run_native(self.shell_device(), script, {'token': TOKEN}, session)
            self.assertEqual(len(list(base.glob('slot-*'))), 7)
            self.assertFalse((base / ACCOUNT / 'active').exists())

    def test_marker_exit_must_agree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); stage, private, hashes = self.make_payload(root, "print('" + C.FINISHED + "acknowledged')\n")
            session, script = C.launch_script(str(private), str(stage), ACCOUNT, RUN, 'a' * 32, '/data', hashes, hashlib.sha256(b'public-profile-fixture').hexdigest())
            with self.assertRaises(C.CompanionError): C.run_native(self.shell_device(), script, {'token': TOKEN}, session)

    def test_real_shell_start_marker_must_match_selected_control_policy(self):
        for requested, emitted, accepted in ((True, C.START, False), (False, C.CAPTAIN_START, False), (True, C.CAPTAIN_START, True)):
            with self.subTest(requested=requested, emitted=emitted), tempfile.TemporaryDirectory() as tmp:
                code = "import json,sys\nvalue=json.loads(sys.stdin.readline())\nprint(" + repr(emitted) + ",flush=True)\nprint(" + repr(C.FINISHED + 'acknowledged') + ",flush=True)\n"
                stage, private, hashes = self.make_payload(Path(tmp), code)
                session, script = C.launch_script(str(private), str(stage), ACCOUNT, RUN, 'a' * 32, '/data', hashes, hashlib.sha256(b'public-profile-fixture').hexdigest())
                admission = {'token': TOKEN}
                if requested: admission.update(captain_action='surrender', after_turn=3)
                if accepted:
                    self.assertEqual(C.run_native(self.shell_device(), script, admission, session, emit=lambda *a, **k: None), 'acknowledged')
                else:
                    with self.assertRaises(C.CompanionError): C.run_native(self.shell_device(), script, admission, session, emit=lambda *a, **k: None)

    def test_real_shell_public_profile_copy_is_bound_and_original_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); stage, private, hashes = self.make_payload(root)
            original = private / 'files/alliance-public-profile.json'
            original_bytes = original.read_bytes()
            session, script = C.launch_script(str(private), str(stage), ACCOUNT, RUN, 'a' * 32, '/data', hashes, '0' * 64)
            with self.assertRaises(C.CompanionError): C.run_native(self.shell_device(), script, {'token': TOKEN}, session)
            self.assertEqual(original.read_bytes(), original_bytes)
            self.assertFalse((private / 'files/alliance-companions' / ACCOUNT / 'active').exists())

    def test_real_owned_shell_signal_stops_child_and_releases_only_owned_locks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); stage, private, hashes = self.make_payload(root,
                "import json,sys,time\njson.loads(sys.stdin.readline())\nprint('" + C.START + "',flush=True)\ntime.sleep(60)\n")
            session, script = C.launch_script(str(private), str(stage), ACCOUNT, RUN, 'a' * 32, '/data', hashes, hashlib.sha256(b'public-profile-fixture').hexdigest())
            class Local:
                def private(self, command, **kwargs):
                    return C.capture(['sh', '-c', command], timeout=15)
            with subprocess.Popen(['sh', '-c', script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
                process.stdin.write(b'{}\n'); process.stdin.close()
                self.assertEqual(process.stdout.readline().decode().strip(), C.START)
                C.stop_owned(Local(), session)
                self.assertEqual(process.wait(timeout=10), 130)
            self.assertFalse((private / 'files/alliance-companions' / ACCOUNT / 'active').exists())
            self.assertEqual(list((private / 'files/alliance-companions').glob('slot-*')), [])

    def test_device_checks_exact_installed_apk_before_reading_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            apk = Path(tmp) / 'app.apk'; apk.write_bytes(b'installed-apk')
            device = mock.Mock()
            device.call.side_effect = [b'device\n', b'1\n', b'x86_64\n', b'28\n', b'versionCode=1192614']
            with self.assertRaisesRegex(C.CompanionError, 'installed_version_differs'): C.check_device(device, apk)
            device.private.assert_not_called()
            device.reset_mock()
            device.call.side_effect = [b'device\n', b'1\n', b'x86_64\n', b'28\n', b'versionCode=1192618', b'package:/data/app/example/base.apk\n', b'wrong hash']
            with self.assertRaisesRegex(C.CompanionError, 'installed_apk_differs'): C.check_device(device, apk)
            device.private.assert_not_called()

    def test_device_reads_only_existing_profile_data_after_apk_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            apk = Path(tmp) / 'app.apk'; apk.write_bytes(b'installed-apk')
            device = mock.Mock()
            device.call.side_effect = [b'device\n', b'1\n', b'x86_64\n', b'28\n', b'versionCode=1192618', b'package:/data/app/example/base.apk\n', C.digest(apk).encode() + b' path']
            raw = json.dumps(self.profile).encode()
            device.private.side_effect = [b'/data/user/0/org.wesnoth.offline\n', raw, b'']
            checked = C.check_device(device, apk)
            self.assertEqual(checked[2], self.profile)
            self.assertEqual(checked[4], hashlib.sha256(raw).hexdigest())
            self.assertNotIn('cp ', ' '.join(call.args[0] for call in device.private.call_args_list))

    def test_build_payload_binds_current_receipts_after_full_verifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp); (repo / 'tools').mkdir(); (repo / 'tools/build_android_account_game.py').write_text('verifier')
            engine = repo / 'build/android-engine'; account = repo / 'build/android-account-game'
            cli = engine / 'prefix/bin/wesnoth-engine-cli'; cli.parent.mkdir(parents=True); cli.write_bytes(b'cli')
            records = {}
            for name in C.LIBRARIES:
                relative = 'app/src/main/jniLibs/x86_64/' + name
                path = account / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(name.encode())
                records[relative] = {'bytes': path.stat().st_size, 'sha256': C.digest(path)}
            (account / 'receipt.json').write_text(json.dumps({'artifacts': records}))
            (engine / 'receipt.json').write_text(json.dumps({'artifacts': {'prefix/bin/wesnoth-engine-cli': {'bytes': 3, 'sha256': C.digest(cli)}}}))
            with mock.patch.object(C, 'capture', return_value=b'ANDROID ACCOUNT GAME ARTIFACTS VERIFIED\n'):
                self.assertEqual(len(C.verify_build(repo, repo, cli)), 6)
                cli.write_bytes(b'changed')
                with self.assertRaisesRegex(C.CompanionError, 'payload_receipt_differs'): C.verify_build(repo, repo, cli)

    def execute_fixture(self, retry=False):
        args = argparse.Namespace(adb=self.cert, sdk=self.root, java=self.cert, serial='emulator-5554', username='UniRecruit', battle_id=RUN, retry_result=retry)
        report = {'outcome': 'failed'}
        device = mock.Mock(); device.call.return_value = b'x ignored'
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(C, 'verify_build', return_value={}))
        stack.enter_context(mock.patch.object(C, 'Device', return_value=device))
        stack.enter_context(mock.patch.object(C, 'check_device', return_value=('/data/user/0/' + C.PACKAGE, 'profile', self.profile, '/data', '0' * 64)))
        stack.enter_context(mock.patch('builtins.input', return_value=('RETRY ' if retry else 'CONTROL ') + 'UniRecruit'))
        stack.enter_context(mock.patch.object(C.getpass, 'getpass', return_value='hidden-fixture'))
        stack.enter_context(mock.patch.object(C, 'login', return_value=(TOKEN, account())))
        stack.enter_context(mock.patch.object(C, 'launch_script', return_value=('/owned-session', 'script')))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        return args, report, stack

    def test_actual_hidden_prompt_v2_tls_and_owned_shell_workflow(self):
        # Real getpass terminal, encrypted API, shipped execute/run_native and
        # shell wrapper. Only Android/ADB and the engine binary are fixtures.
        import pty
        import select
        import termios
        import time
        with tempfile.TemporaryDirectory(prefix='companion workflow ') as tmp:
            root = Path(tmp); stage, private, hashes = self.make_payload(root)
            def response(request):
                self.assertEqual(request['v'], 2)
                if request['op'] == 'login': return {'token': TOKEN, 'account': account()}
                if request['op'] == 'battle_detail': return battle()
                if request['op'] == 'logout': return {'status': 'logged_out'}
                raise AssertionError('unexpected operation')
            with FixtureServer(self.cert, self.key, response, requests=4) as server:
                profile = dict(self.profile, port=server.port)
                report_path = root / 'report.json'
                program = root / 'helper.py'
                program.write_text("""import argparse, importlib.util, json, sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('companion', SOURCE)
C=importlib.util.module_from_spec(spec);spec.loader.exec_module(C)
class Device:
    base=[sys.executable,'-c',"import os,shlex,sys; a=shlex.split(sys.argv[-1]); os.execvp('sh',['sh','-c',a[-1]])"]
    def __init__(self,*args): pass
    def call(self,*args,**kw): return b''
C.Device=Device
C.verify_build=lambda *a: {}
C.check_device=lambda *a: (PRIVATE,'profile',PROFILE,'/data',PROFILE_HASH)
original=C.launch_script
C.launch_script=lambda pr,st,ac,run,launch,data,hashes,ph: original(pr,STAGE,ac,run,launch,data,HASHES,ph)
args=argparse.Namespace(adb=Path('unused'),sdk=Path('unused'),java=Path('unused'),serial='emulator-5554',username='UniRecruit',battle_id=RUN,retry_result=False)
report={}
C.execute(args,report)
Path(REPORT).write_text(json.dumps(report))
""".replace('SOURCE', repr(str(ROOT / 'tools/run_android_alliance_companion.py')))
                    .replace('PRIVATE', repr(str(private))).replace('PROFILE_HASH', repr(hashlib.sha256(b'public-profile-fixture').hexdigest()))
                    .replace('PROFILE', repr(profile)).replace('STAGE', repr(str(stage)))
                    .replace('HASHES', repr(hashes)).replace('RUN', repr(RUN)).replace('REPORT', repr(str(report_path))))
                master, slave = pty.openpty()
                try:
                    with subprocess.Popen([os.sys.executable, '-I', '-B', str(program)], stdin=slave, stdout=slave, stderr=slave, start_new_session=True) as process:
                        os.close(slave); slave = None
                        output = bytearray(); consent_sent = password_sent = False
                        deadline = time.monotonic() + 25
                        while process.poll() is None and time.monotonic() < deadline:
                            if select.select([master], [], [], 0.1)[0]:
                                try: output.extend(os.read(master, 4096))
                                except OSError: break
                            if not consent_sent and b'Type CONTROL UniRecruit' in output:
                                os.write(master, b'CONTROL UniRecruit\n'); consent_sent = True
                            if not password_sent and b'Player password (hidden; one login attempt):' in output:
                                self.assertFalse(termios.tcgetattr(master)[3] & termios.ECHO)
                                os.write(master, b'private-fixture-password\n'); password_sent = True
                        if process.poll() is None: process.kill()
                        self.assertEqual(process.wait(timeout=5), 0, output.decode(errors='replace'))
                    while select.select([master], [], [], 0)[0]:
                        try: output.extend(os.read(master, 4096))
                        except OSError: break
                finally:
                    os.close(master)
                    if slave is not None: os.close(slave)
                self.assertTrue(consent_sent and password_sent)
                self.assertNotIn(b'private-fixture-password', output)
                self.assertNotIn(TOKEN.encode(), output)
                report = json.loads(report_path.read_text())
                self.assertEqual(report['outcome'], 'completed')
                self.assertEqual(report['session_logout'], 'confirmed')
                self.assertEqual(report['database_generation'], GENERATION)
                self.assertNotIn(TOKEN, report_path.read_text())
                self.assertEqual([req['op'] for req in server.applications], ['login', 'battle_detail', 'battle_detail', 'logout'])
                self.assertIsNone(server.error)

    def test_finally_logout_after_native_failure(self):
        args, report, stack = self.execute_fixture()
        with stack, mock.patch.object(C, 'account_request', side_effect=[battle(), battle(), {'status': 'logged_out'}]) as requests, mock.patch.object(C, 'run_native', side_effect=C.CompanionError('companion_stopped')):
            with self.assertRaises(C.CompanionError): C.execute(args, report)
        self.assertEqual(requests.call_args.args, (self.profile, 'logout', {}, TOKEN))
        self.assertEqual(report['session_logout'], 'confirmed')
        self.assertNotIn(TOKEN, json.dumps(report))

    def test_retry_only_passes_intent_skips_readiness_mutations_and_revokes_session(self):
        args, report, stack = self.execute_fixture(retry=True)
        with stack, mock.patch.object(C, 'account_request', return_value={'status': 'logged_out'}) as requests, mock.patch.object(C, 'run_native', return_value='acknowledged') as native:
            C.execute(args, report)
        self.assertEqual(native.call_args.args[2]['intent'], 'result_retry')
        self.assertEqual([call.args[1] for call in requests.call_args_list], ['logout'])
        self.assertEqual(report['outcome'], 'completed')

    def test_back_at_consent_does_not_login_or_start_process(self):
        args, report, stack = self.execute_fixture()
        with stack, mock.patch('builtins.input', return_value=''), mock.patch.object(C, 'login') as login, mock.patch.object(C, 'run_native') as native:
            with self.assertRaisesRegex(C.CompanionError, 'cancelled_before_login'): C.execute(args, report)
        login.assert_not_called(); native.assert_not_called()

    def test_failed_refresh_revokes_token_without_starting_engine(self):
        args, report, stack = self.execute_fixture()
        with stack, mock.patch.object(C, 'account_request', side_effect=[battle(), C.CompanionError('account_rejected'), {'status': 'logged_out'}]) as requests, mock.patch.object(C, 'run_native') as native:
            with self.assertRaises(C.CompanionError): C.execute(args, report)
        native.assert_not_called()
        self.assertEqual([call.args[1] for call in requests.call_args_list], ['battle_detail', 'battle_detail', 'logout'])

    def test_successful_engine_with_unconfirmed_logout_is_not_fully_complete(self):
        args, report, stack = self.execute_fixture(retry=True)
        with stack, mock.patch.object(C, 'account_request', side_effect=C.CompanionError('account_timeout')), mock.patch.object(C, 'run_native', return_value='acknowledged'):
            C.execute(args, report)
        self.assertEqual(report['session_logout'], 'unconfirmed')

    def test_captain_policy_exact_consent_stdin_and_two_permission_reads(self):
        for action in C.CAPTAIN_ACTIONS:
            with self.subTest(action=action):
                args, report, stack = self.execute_fixture()
                args.captain_action, args.after_turn = action, 3
                consent = 'CONTROL UniRecruit ' + action + ' TURN 3'
                with stack, mock.patch('builtins.input', return_value=consent) as prompt, mock.patch.object(C, 'account_request', side_effect=[battle(), battle(), {'status': 'logged_out'}]) as requests, mock.patch.object(C, 'run_native', return_value='acknowledged') as native:
                    C.execute(args, report)
                self.assertIn(consent, prompt.call_args.args[0])
                self.assertEqual(native.call_args.args[2], {'run_id': RUN, 'account_id': ACCOUNT, 'token': TOKEN, 'intent': 'play', 'captain_action': action, 'after_turn': 3})
                self.assertEqual([call.args[1] for call in requests.call_args_list], ['battle_detail', 'battle_detail', 'logout'])
                self.assertEqual(report['captain_policy'], {'captain_action': action, 'after_turn': 3})
                self.assertNotIn(TOKEN, json.dumps(report))
                self.assertNotIn('plugin', native.call_args.args[1])

    def test_captain_policy_generic_consent_is_not_sufficient(self):
        args, report, stack = self.execute_fixture()
        args.captain_action, args.after_turn = 'surrender', 1
        with stack, mock.patch.object(C, 'login') as login, mock.patch.object(C, 'run_native') as native:
            with self.assertRaisesRegex(C.CompanionError, 'cancelled_before_login'): C.execute(args, report)
        login.assert_not_called(); native.assert_not_called()

    def test_changed_captain_before_launch_logs_out_without_engine_start(self):
        args, report, stack = self.execute_fixture()
        args.captain_action, args.after_turn = 'surrender', 2
        changed = battle(); changed['battle']['teams'][0]['captain_id'] = RUN
        with stack, mock.patch('builtins.input', return_value='CONTROL UniRecruit surrender TURN 2'), mock.patch.object(C, 'account_request', side_effect=[battle(), changed, {'status': 'logged_out'}]) as requests, mock.patch.object(C, 'run_native') as native:
            with self.assertRaisesRegex(C.CompanionError, 'account_not_selected_captain'): C.execute(args, report)
        native.assert_not_called()
        self.assertEqual(requests.call_args.args, (self.profile, 'logout', {}, TOKEN))

    def test_invalid_policy_fails_before_payload_or_password_access(self):
        args, report, stack = self.execute_fixture(retry=True)
        args.captain_action, args.after_turn = 'surrender', 2
        with stack, mock.patch.object(C, 'verify_build') as verify, mock.patch.object(C.getpass, 'getpass') as password:
            with self.assertRaisesRegex(C.CompanionError, 'invalid_captain_policy'): C.execute(args, report)
        verify.assert_not_called(); password.assert_not_called()

    def test_powershell_wrapper_preserves_arguments_and_child_exit(self):
        pwsh = ROOT / 'build/host-tools/powershell-7.4.6/pwsh'
        with tempfile.TemporaryDirectory(prefix='companion PS & ') as tmp:
            base = Path(tmp); tools = base / 'tools'; tools.mkdir()
            helper = tools / 'Run-AndroidAllianceCompanion.ps1'; shutil.copyfile(ROOT / 'tools/Run-AndroidAllianceCompanion.ps1', helper)
            (tools / 'run_android_alliance_companion.py').write_text('fixture')
            python = base / 'Python & tool/python.exe'; python.parent.mkdir()
            python.write_text('#!/usr/bin/python3\nimport json,os,sys\nopen(os.environ["CALLS"],"w").write(json.dumps(sys.argv[1:]))\nsys.exit(2)\n'); python.chmod(0o755)
            adb = base / 'SDK & tool/adb.exe'; adb.parent.mkdir(); adb.write_text('fixture')
            java = base / 'Java & tool/bin/java.exe'; java.parent.mkdir(parents=True); java.write_text('fixture')
            quote = lambda value: "'" + str(value).replace("'", "''") + "'"
            command = "$env:OS='Windows_NT'; & " + quote(helper) + ' -Username UniRecruit -BattleId ' + quote(RUN) + ' -PythonPath ' + quote(python) + ' -AdbPath ' + quote(adb) + ' -SdkRoot ' + quote(adb.parent) + ' -JavaHome ' + quote(java.parent.parent) + ' -RetryResult; exit $LASTEXITCODE'
            env = dict(os.environ, CALLS=str(base / 'calls.json'))
            result = subprocess.run([str(pwsh), '-NoProfile', '-Command', command], capture_output=True, env=env, timeout=20)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            args = json.loads((base / 'calls.json').read_text())
            self.assertEqual(args[:3], ['-I', '-B', '-u'])
            self.assertEqual(args[args.index('--battle-id') + 1], RUN)
            self.assertEqual(args[-1], '--retry-result')
            self.assertNotIn(TOKEN, json.dumps(args))
            for action, wire in (('Surrender', 'surrender'), ('OfferDraw', 'offer_draw'), ('WithdrawDraw', 'withdraw_draw')):
                policy_command = command.replace('-RetryResult;', '-CaptainAction ' + action + ' -AfterTurn 3;')
                result = subprocess.run([str(pwsh), '-NoProfile', '-Command', policy_command], capture_output=True, env=env, timeout=20)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                args = json.loads((base / 'calls.json').read_text())
                self.assertEqual(args[-4:], ['--captain-action', wire, '--after-turn', '3'])
            for invalid in ('-CaptainAction Surrender', '-AfterTurn 3', '-CaptainAction Surrender -AfterTurn 3 -RetryResult', '-CaptainAction Surrender -AfterTurn 101'):
                (base / 'calls.json').unlink()
                result = subprocess.run([str(pwsh), '-NoProfile', '-Command', command.replace('-RetryResult;', invalid + ';')], capture_output=True, env=env, timeout=20)
                self.assertNotEqual(result.returncode, 2)
                self.assertFalse((base / 'calls.json').exists())
                (base / 'calls.json').write_text('[]')

    def test_powershell_wrapper_has_no_credential_parameters_or_transcript(self):
        source = (ROOT / 'tools/Run-AndroidAllianceCompanion.ps1').read_text()
        self.assertNotIn('Start-Transcript', source)
        self.assertNotIn('$Password', source)
        self.assertNotIn('$Token', source)
        self.assertIn('--retry-result', source)
        pwsh = ROOT / 'build/host-tools/powershell-7.4.6/pwsh'
        self.assertTrue(pwsh.is_file())
        command = "$e=$null; $t=$null; [System.Management.Automation.Language.Parser]::ParseFile($args[0],[ref]$t,[ref]$e)|Out-Null; if($e.Count){exit 1}"
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / 'parse.ps1'; script.write_text(command)
            result = subprocess.run([str(pwsh), '-NoProfile', '-File', str(script), str(ROOT / 'tools/Run-AndroidAllianceCompanion.ps1')], capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__': unittest.main()
