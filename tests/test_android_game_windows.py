"""Real PowerShell process-boundary fixtures; Android/Windows tools are simulated."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
PWSH = None
BUILD = 'Build-WindowsAndroidGame.ps1'
TEST = 'Test-AndroidGame.ps1'
OBB = 'main.1192600.org.wesnoth.offline.obb'
FAKE_TOOL = r'''#!/usr/bin/python3
import hashlib,json,os,pathlib,signal,sys,time
c=json.loads(pathlib.Path(os.environ['GAME_FIXTURE']).read_text()); a=sys.argv[1:]; n=pathlib.Path(sys.argv[0]).name
with open(c['calls'],'a') as f:
 f.write(json.dumps({'tool':n,'args':a,'pid':os.getpid(),'cwd':os.getcwd(),'bash_env':os.environ.get('BASH_ENV'),
 'pythonpath':os.environ.get('PYTHONPATH'),'nobytecode':os.environ.get('PYTHONDONTWRITEBYTECODE')})+'\n')
if n=='taskkill.exe':
 os.killpg(int(a[1]),signal.SIGKILL); raise SystemExit(0)
if n=='python.exe':
 assert sys.stdin.read()==''
 if '--verify-only' in a:
  print(c.get('verify_output','ANDROID GAME ARTIFACTS VERIFIED')); raise SystemExit(c.get('verify_exit',0))
 print('BUILD STDOUT',flush=True); print('BUILD STDERR',file=sys.stderr,flush=True)
 if c.get('hang'):
  os.setsid(); time.sleep(120)
 if not c.get('receipt',True): pathlib.Path(c['receipt_path']).unlink(missing_ok=True)
 print(c.get('build_output','ANDROID GAME BUILD PASS')); raise SystemExit(c.get('build_exit',0))
assert n=='adb.exe' and a[:4]==['-H','localhost','-P','5037'],a
if a[4]=='connect': print('connected'); raise SystemExit(c.get('connect_exit',0))
assert a[4:6]==['-s','127.0.0.1:5555'],a
a=a[6:]; remote='/sdcard/Android/obb/org.wesnoth.offline/'+c['obb_name']; state=pathlib.Path(c['run_state'])
if a==['get-state']: print(c.get('state','device'))
elif a[:2]==['shell','getprop']:
 print(c.get('properties',{}).get(a[2],{'sys.boot_completed':'1','ro.build.version.sdk':'28','ro.product.cpu.abi':'x86_64'}[a[2]]))
elif a[:2]==['install','-r']:
 assert a[2]==c['apk']; print(c.get('install_output','Success')); raise SystemExit(c.get('install_exit',0))
elif a[:3]==['shell','am','force-stop']:
 assert a[3]=='org.wesnoth.offline'
elif a[:3]==['shell','mkdir','-p']:
 assert a[3]=='/sdcard/Android/obb/org.wesnoth.offline'
elif a[0]=='shell' and len(a)==2 and a[1].startswith('if [ -f '):
 old=c.get('existing','MISSING'); print(old if old=='MISSING' else old+'  '+remote)
elif a[0]=='push':
 assert a[1]==c['obb']; assert a[2].startswith(remote+'.incoming-'); print('OBB pushed'); raise SystemExit(c.get('push_exit',0))
elif a[:2]==['shell','sha256sum']:
 print(c.get('uploaded_sha',c['obb_sha'])+'  '+a[2]); raise SystemExit(c.get('sha_exit',0))
elif a[0]=='shell' and len(a)==2 and a[1].startswith('[ ! -e '):
 assert ' && mv '+remote in a[1],a; raise SystemExit(c.get('publish_exit',0))
elif a[:3]==['shell','am','start']:
 assert a[3:8]==['-W','-n','org.wesnoth.offline/.OfflineInitActivity','--es','wesnoth_run_id'],a
 state.write_text(a[8]); print(c.get('launch_output','Status: ok')); raise SystemExit(c.get('launch_exit',0))
elif a==['shell','pidof','org.wesnoth.offline']:
 print(c.get('pid','142')); raise SystemExit(c.get('pid_exit',0))
elif a[:3]==['shell','test','-d']:
 assert a[3]=='/sdcard/Android/data/org.wesnoth.offline/files/userdata/logs',a
 raise SystemExit(c.get('native_logs_exist',1))
elif a[0]=='pull':
 assert a[1]=='/sdcard/Android/data/org.wesnoth.offline/files/userdata/logs',a
 pathlib.Path(a[2]).mkdir(); (pathlib.Path(a[2])/'wesnoth.log').write_text('native game log fixture')
 print('native game logs copied'); raise SystemExit(c.get('native_pull_exit',0))
elif a[0]=='logcat':
 assert '-c' not in a,a
 if '--pid=142' not in a:
  assert '*:S' in a and 'AndroidRuntime:E' in a,a
 token=state.read_text() if state.exists() else 'absent'
 if c.get('stale'): token='previous_run'
 mode=c.get('logs','ready')
 if mode=='ready':
  print('I/WesnothOffline: WESNOTH_GAME_DATA_READY run_id='+token+' files=26748 bytes=726207542')
  print('I/WesnothOffline: WESNOTH_GAME_LAUNCH run_id='+token+' version=1.19.26')
  print('I/WesnothOffline: WESNOTH_GAME_ACTIVITY_READY run_id='+token)
 elif mode=='failed': print('E/WesnothOffline: WESNOTH_GAME FAIL run_id='+token+' message=injected')
 else: print('I/WesnothOffline: waiting')
 raise SystemExit(c.get('log_exit',0))
else: raise AssertionError(a)
'''


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


class GameWindowsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='game windows fixture ')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / 'client with spaces'
        (self.repo / 'tools').mkdir(parents=True)
        for name in (BUILD, TEST):
            shutil.copy2(REPO / 'tools' / name, self.repo / 'tools' / name)
        self.builder = self.repo / 'tools/build_android_game.py'
        self.builder.write_text('fixture builder')
        self.apk = self.repo / 'build/android-game/app/build/outputs/apk/debug/app-debug.apk'
        self.apk.parent.mkdir(parents=True)
        self.apk.write_bytes(b'fixture apk')
        self.receipt = self.repo / 'build/android-game/receipt.json'
        self.receipt.write_text('{}')
        self.obb = self.repo / 'build/android-game-data' / OBB
        self.obb.parent.mkdir(parents=True)
        self.obb.write_bytes(b'fixture obb, verified independently before adb')
        self.sdk = self.base / 'SDK with literal $ & spaces'
        (self.sdk / 'ndk/26.3.11579264').mkdir(parents=True)
        self.java_home = self.base / 'JDK with spaces'
        self.java = self.java_home / 'bin/java.exe'
        self.gradle = self.base / '.gradle'
        self.gradle.mkdir()
        self.python = self.base / 'Python/python.exe'
        self.adb = self.sdk / 'platform-tools/adb.exe'
        self.taskkill = self.base / 'Windows/System32/taskkill.exe'
        for path in (self.java, self.python, self.adb, self.taskkill):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(FAKE_TOOL)
            path.chmod(0o755)
        self.keep = self.repo / 'build/android-engine/prefix/lib/libmain.so'
        self.keep.parent.mkdir(parents=True)
        self.keep.write_bytes(b'prior engine preserved')
        self.check = self.repo / 'build/android-sdl-apk/receipt.json'
        self.check.parent.mkdir(parents=True)
        self.check.write_bytes(b'previous diagnostic app preserved')
        self.secret = self.repo / '.env'
        self.secret.write_bytes(b'PRIVATE_SENTINEL_MUST_NOT_PRINT')
        self.config = {'calls': str(self.base / 'calls.jsonl'), 'receipt_path': str(self.receipt),
                       'apk': str(self.apk), 'obb': str(self.obb), 'obb_name': OBB,
                       'obb_sha': hashlib.sha256(self.obb.read_bytes()).hexdigest(),
                       'run_state': str(self.base / 'run.txt')}
        self.cfg_path = self.base / 'fixture.json'

    def run_wrapper(self, build=False, extra=()):
        self.cfg_path.write_text(json.dumps(self.config))
        Path(self.config['calls']).unlink(missing_ok=True)
        script = self.repo / 'tools' / (BUILD if build else TEST)
        args = ['-PythonPath', self.python, '-SdkRoot', self.sdk, '-JavaHome', self.java_home]
        args += ['-GradleUserHome', self.gradle] if build else ['-AdbPath', self.adb]
        args += list(extra)
        launcher = self.base / 'launcher.ps1'
        before, after = self.base / 'before.json', self.base / 'after.json'
        capture = '@{ PATH=$env:PATH; MSYSTEM=$env:MSYSTEM; BASH_ENV=$env:BASH_ENV; PYTHONPATH=$env:PYTHONPATH } | ConvertTo-Json | Set-Content -LiteralPath '
        launcher.write_text(capture + quote(before) + '\n& ' + quote(script) + ' ' +
                            ' '.join(str(args[i]) + ' ' + quote(args[i+1]) for i in range(0, len(args), 2)) +
                            '\n$result=$LASTEXITCODE\n' + capture + quote(after) + '\nexit $result\n')
        environment = dict(os.environ, OS='Windows_NT', LOCALAPPDATA=str(self.base),
                           SystemRoot=str(self.base / 'Windows'), GAME_FIXTURE=str(self.cfg_path),
                           MSYSTEM='preserve parent', BASH_ENV='preserve hook', PYTHONPATH='preserve pythonpath')
        result = subprocess.run([PWSH, '-NoLogo', '-NoProfile', '-File', str(launcher)],
                                env=environment, capture_output=True, text=True, timeout=25)
        self.output = result.stdout + result.stderr
        path = Path(self.config['calls'])
        self.calls = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        self.assertEqual(self.keep.read_bytes(), b'prior engine preserved')
        self.assertEqual(self.check.read_bytes(), b'previous diagnostic app preserved')
        self.assertNotIn('PRIVATE_SENTINEL_MUST_NOT_PRINT', self.output)
        self.assertEqual(json.loads(before.read_text()), json.loads(after.read_text()))
        return result.returncode

    def adb_calls(self):
        return [call['args'][6:] for call in self.calls if call['tool'] == 'adb.exe' and call['args'][4] == '-s']

    def shorten_wait(self):
        path = self.repo / 'tools' / TEST
        path.write_text(path.read_text().replace('$importTimeoutMilliseconds = 600000', '$importTimeoutMilliseconds = 1'))

    def test_build_offline_arguments_marker_and_receipt(self):
        self.assertEqual(self.run_wrapper(build=True), 0, self.output)
        self.assertEqual(len(self.calls), 1)
        call = self.calls[0]
        self.assertEqual(call['args'], ['-I', '-B', '-u', str(self.builder), '--ndk', str(self.sdk / 'ndk/26.3.11579264'),
                                       '--sdk', str(self.sdk), '--java', str(self.java), '--gradle-user-home', str(self.gradle)])
        self.assertEqual(call['cwd'], str(self.repo))
        self.assertIsNone(call['bash_env'])
        self.assertIsNone(call['pythonpath'])
        self.assertEqual(call['nobytecode'], '1')
        self.assertIn('BUILD STDOUT', self.output)
        self.assertIn('BUILD STDERR', self.output)
        self.assertIn('ANDROID GAME BUILD COMPLETE:', self.output)

    def test_optional_archive_directory_is_forwarded_literally(self):
        archive = self.base / 'SDL source $ & archive'
        self.assertEqual(self.run_wrapper(build=True, extra=['-ArchiveDirectory', archive]), 0, self.output)
        self.assertEqual(self.calls[0]['args'][-2:], ['--archive-directory', str(archive)])

    def test_build_failures_cannot_print_completion(self):
        for settings in ({'build_exit': 2}, {'build_output': 'no marker'}, {'receipt': False}):
            with self.subTest(settings=settings):
                self.config.update(settings)
                self.assertEqual(self.run_wrapper(build=True), 2, self.output)
                self.assertNotIn('ANDROID GAME BUILD COMPLETE:', self.output)
                for key in settings:
                    self.config.pop(key)
                self.receipt.write_text('{}')

    def test_missing_java_and_ndk_block_before_build(self):
        original = self.java.read_bytes()
        self.java.unlink()
        self.assertEqual(self.run_wrapper(build=True), 2, self.output)
        self.assertEqual(self.calls, [])
        self.java.write_bytes(original)
        self.java.chmod(0o755)
        (self.sdk / 'ndk/26.3.11579264').rmdir()
        self.assertEqual(self.run_wrapper(build=True), 2, self.output)
        self.assertEqual(self.calls, [])

    def test_build_timeout_stops_only_owned_tree(self):
        path = self.repo / 'tools' / BUILD
        path.write_text(path.read_text().replace('$timeoutMilliseconds = 7200000', '$timeoutMilliseconds = 400'))
        self.config['hang'] = True
        self.assertEqual(self.run_wrapper(build=True), 2, self.output)
        self.assertEqual([c['tool'] for c in self.calls], ['python.exe', 'taskkill.exe'])
        self.assertEqual(self.calls[1]['args'], ['/PID', str(self.calls[0]['pid']), '/T', '/F'])

    def test_verify_signed_apk_and_obb_precedes_all_adb(self):
        for settings in ({'verify_exit': 2}, {'verify_output': 'no marker'}):
            with self.subTest(settings=settings):
                self.config.update(settings)
                self.assertEqual(self.run_wrapper(), 2, self.output)
                self.assertEqual([c['tool'] for c in self.calls], ['python.exe'])
                self.assertEqual(self.calls[0]['args'], ['-I', '-B', '-u', str(self.builder), '--verify-only',
                                                       '--sdk', str(self.sdk), '--java', str(self.java)])
                for key in settings:
                    self.config.pop(key)

    def test_missing_obb_blocks_before_adb_even_if_verifier_is_incorrect(self):
        self.obb.unlink()
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual([c['tool'] for c in self.calls], ['python.exe'])

    def test_game_install_import_and_activity_keep_manual_gameplay_pending(self):
        self.config['native_logs_exist'] = 0
        self.assertEqual(self.run_wrapper(), 0, self.output)
        calls = self.adb_calls()
        self.assertIn(['install', '-r', str(self.apk)], calls)
        pushes = [a for a in calls if a[0] == 'push']
        self.assertEqual(len(pushes), 1)
        self.assertEqual(pushes[0][1], str(self.obb))
        self.assertIn('.incoming-', pushes[0][2])
        self.assertFalse(any('offlinecheck' in arg for args in calls for arg in args))
        self.assertFalse(any(a[:2] == ['shell', 'input'] or a[0] == 'uninstall' or a == ['logcat', '-c'] for a in calls))
        self.assertIn('ANDROID GAME LAUNCH CHECK PASSED', self.output)
        self.assertIn('gameplay still need your visual test', self.output)
        logs = list((self.repo / 'diagnostics').glob('game-logcat-*.txt'))
        self.assertEqual(len(logs), 1)
        self.assertIn('WESNOTH_GAME_DATA_READY', logs[0].read_text())
        native = list((self.repo / 'diagnostics').glob('game-userdata-*/wesnoth.log'))
        self.assertEqual(len(native), 1)
        self.assertEqual(native[0].read_text(), 'native game log fixture')

    def test_identical_remote_obb_skips_transfer_and_changed_one_is_backed_up(self):
        self.config['existing'] = self.config['obb_sha']
        self.assertEqual(self.run_wrapper(), 0, self.output)
        self.assertFalse(any(a[0] == 'push' for a in self.adb_calls()))
        self.config['existing'] = 'f' * 64
        self.assertEqual(self.run_wrapper(), 0, self.output)
        calls = self.adb_calls()
        moves = [a[1] for a in calls if a[0] == 'shell' and len(a) == 2 and ' && mv ' in a[1]]
        self.assertEqual(len(moves), 2)
        self.assertIn('.before-', moves[0])
        self.assertIn('.incoming-', moves[1])
        self.assertEqual(len(list((self.repo / 'diagnostics').glob('game-logcat-*.txt'))), 2)

    def test_device_readiness_precedes_install(self):
        for settings in ({'state': 'offline'}, {'properties': {'ro.product.cpu.abi': 'arm64-v8a'}},
                         {'properties': {'ro.build.version.sdk': '22'}}, {'properties': {'sys.boot_completed': '0'}}):
            with self.subTest(settings=settings):
                self.config.update(settings)
                self.assertEqual(self.run_wrapper(), 2, self.output)
                self.assertFalse(any(a[0] == 'install' for a in self.adb_calls()))
                for key in settings:
                    self.config.pop(key)

    def test_install_requires_exit_zero_and_success_before_obb_or_launch(self):
        for settings in ({'install_exit': 1}, {'install_output': 'Failure [INSTALL_FAILED]'}):
            with self.subTest(settings=settings):
                self.config.update(settings)
                self.assertEqual(self.run_wrapper(), 2, self.output)
                self.assertFalse(any(a[0] == 'push' or a[:3] == ['shell', 'am', 'start'] for a in self.adb_calls()))
                for key in settings:
                    self.config.pop(key)

    def test_failed_push_or_wrong_remote_hash_cannot_publish_or_launch(self):
        for settings in ({'push_exit': 1}, {'uploaded_sha': '0' * 64}, {'sha_exit': 1}, {'existing': 'malformed hash'}):
            with self.subTest(settings=settings):
                self.config.update(settings)
                self.assertEqual(self.run_wrapper(), 2, self.output)
                self.assertFalse(any(a[:3] == ['shell', 'am', 'start'] or (len(a) == 2 and ' && mv ' in a[1])
                                     for a in self.adb_calls()))
                for key in settings:
                    self.config.pop(key)

    def test_fresh_markers_required_and_failure_logs_saved(self):
        self.shorten_wait()
        for settings in ({'stale': True}, {'logs': 'pending'}, {'logs': 'failed'}):
            with self.subTest(settings=settings):
                self.config.update(settings)
                before = len(list((self.repo / 'diagnostics').glob('game-logcat-*.txt'))) if (self.repo / 'diagnostics').exists() else 0
                self.assertEqual(self.run_wrapper(), 2, self.output)
                self.assertNotIn('ANDROID GAME LAUNCH CHECK PASSED', self.output)
                self.assertEqual(len(list((self.repo / 'diagnostics').glob('game-logcat-*.txt'))), before + 1)
                for key in settings:
                    self.config.pop(key)

    def test_failed_activity_launch_collects_filtered_logs_without_global_dump(self):
        self.config['launch_output'] = 'Error: unable to start activity'
        self.assertEqual(self.run_wrapper(), 2, self.output)
        logs = [a for a in self.adb_calls() if a[0] == 'logcat']
        self.assertEqual(len(logs), 1)
        self.assertIn('*:S', logs[0])
        self.assertIn('AndroidRuntime:E', logs[0])
        self.assertIn('Game logcat saved:', self.output)

    def test_stopped_or_multiple_app_processes_cannot_pass(self):
        for settings in ({'pid': '', 'pid_exit': 1}, {'pid': '142 143'}):
            with self.subTest(settings=settings):
                self.config.update(settings)
                self.assertEqual(self.run_wrapper(), 2, self.output)
                self.assertNotIn('ANDROID GAME LAUNCH CHECK PASSED', self.output)
                for key in settings:
                    self.config.pop(key)

    def test_native_log_pull_failure_cannot_print_final_success(self):
        self.config.update(native_logs_exist=0, native_pull_exit=1)
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertNotIn('ANDROID GAME LAUNCH CHECK PASSED', self.output)
        self.assertIn('ERROR saving game logs:', self.output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--pwsh', required=True)
    args, remaining = parser.parse_known_args()
    PWSH = args.pwsh
    unittest.main(argv=[__file__] + remaining)
