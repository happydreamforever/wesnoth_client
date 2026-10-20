"""Executable PowerShell device-helper fixtures with simulated verifier/ADB.

This exercises Windows handoff decisions under Linux PowerShell; it is not
Windows or LDPlayer execution. Real receipt/ELF checks belong to builder tests.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PWSH = None
HELPER = 'Test-AndroidAlliance.ps1'
MARKERS = [
    'WESNOTH_ANDROID_ALLIANCE abi=x86_64 min_api=23 boost=1_88 openssl=0x30400010',
    *['PASS ' + name for name in ('before-connect-guards', 'wrong-ip', 'wrong-pin',
      'hostname-refused', 'broadcast-refused', 'expired', 'TLS1.2', 'TLS1.3',
      'server-push', 'untrusted', 'downgrade', 'plaintext', 'preamble-eof',
      'preamble-reset', 'preamble-stall', 'tls-stall', 'oversized', 'empty-frame',
      'partial-header', 'partial-body', 'receive-stall', 'cancel')],
    'ALLIANCE ANDROID TRANSPORT CHECK PASSED',
]
FAKE = r'''#!/usr/bin/python3
import hashlib, json, os, pathlib, sys, time
cfg = json.loads(pathlib.Path(os.environ['ALLIANCE_DEVICE_FIXTURE']).read_text())
args = sys.argv[1:]
name = pathlib.Path(sys.argv[0]).name
with open(cfg['calls'], 'a') as stream:
    stream.write(json.dumps({'tool': name, 'args': args, 'cwd': os.getcwd(),
       'env': {key: os.environ.get(key) for key in ('PATH', 'MSYSTEM', 'BASH_ENV', 'ENV',
       'PYTHONHOME', 'PYTHONPATH', 'PYTHONDONTWRITEBYTECODE', 'PYTHONNOUSERSITE')}}) + '\n')
assert sys.stdin.read() == ''
if name == 'python.exe':
    if cfg.get('verify_hang'): time.sleep(20)
    print(cfg.get('verify_output', 'ANDROID ALLIANCE ARTIFACTS VERIFIED'))
    print(cfg.get('verify_stderr', 'VISIBLE VERIFY STDERR'), file=sys.stderr)
    raise SystemExit(cfg.get('verify_exit', 0))
assert name == 'adb.exe'
assert args[:4] == ['-H', 'localhost', '-P', '5037'], args
if args[4] == 'connect':
    print('connected to ' + args[5])
    raise SystemExit(cfg.get('connect_exit', 0))
assert args[4:6] == ['-s', cfg['serial']], args
cmd = args[6:]
if cmd == ['get-state']:
    print(cfg.get('state', 'device'))
    raise SystemExit(cfg.get('state_exit', 0))
elif cmd[:2] == ['shell', 'getprop']:
    values = {'sys.boot_completed':'1', 'ro.build.version.sdk':'28', 'ro.product.cpu.abi':'x86_64'}
    print(cfg.get('properties', {}).get(cmd[2], values[cmd[2]]))
elif cmd[:3] == ['shell', 'mkdir', '-p']:
    raise SystemExit(cfg.get('mkdir_exit', 0))
elif cmd[:1] == ['push']:
    assert pathlib.Path(cmd[1]).is_file(), cmd
    print('fixture pushed ' + pathlib.Path(cmd[1]).name)
    raise SystemExit(cfg.get('push_exits', {}).get(pathlib.Path(cmd[1]).name, 0))
elif cmd[:2] == ['shell', 'sha256sum']:
    base = pathlib.PurePosixPath(cmd[2]).name
    print(cfg.get('hash_output', {}).get(base, cfg['hashes'][base] + '  ' + cmd[2]))
    raise SystemExit(cfg.get('hash_exit', 0))
elif cmd[:3] == ['shell', 'chmod', '700']:
    raise SystemExit(cfg.get('chmod_exit', 0))
elif len(cmd) == 2 and cmd[0] == 'shell' and cmd[1].endswith('/android-alliance-check'):
    parts = cmd[1].split()
    assert len(parts) == 5 and parts[0] == 'cd' and parts[2] == '&&', cmd
    remote = str(pathlib.PurePosixPath(parts[4]).parent)
    assert parts[1] == remote and parts[3] == 'LD_LIBRARY_PATH=' + remote, cmd
    if cfg.get('runtime_hang'): time.sleep(20)
    print(cfg['runtime_output'])
    print(cfg.get('runtime_stderr', 'VISIBLE RUNTIME STDERR'), file=sys.stderr)
    raise SystemExit(cfg.get('runtime_exit', 0))
else: raise AssertionError(cmd)
'''


def ps(value):
    return "'" + str(value).replace("'", "''") + "'"


class DeviceHelper(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='alliance device ')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "client & literal $ ' with spaces"
        (self.repo / 'tools').mkdir(parents=True)
        self.helper = self.repo / 'tools' / HELPER
        shutil.copyfile(ROOT / 'tools' / HELPER, self.helper)
        self.builder = self.repo / 'tools/build_android_alliance.py'
        self.builder.write_text('synthetic verifier')
        self.python = self.base / 'Native Python/python.exe'
        self.adb = self.base / 'Android SDK/platform-tools/adb.exe'
        for path in (self.python, self.adb):
            path.parent.mkdir(parents=True)
            path.write_text(FAKE)
            path.chmod(0o755)
        stage = self.repo / 'build/android-alliance'
        self.artifact = stage / 'prefix/bin/android-alliance-check'
        self.runtime = stage / 'prefix/lib/libc++_shared.so'
        for path in (self.artifact, self.runtime):
            path.parent.mkdir(parents=True)
            path.write_bytes(('synthetic ' + path.name).encode())
        self.receipt = stage / 'receipt.json'
        hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in (self.artifact, self.runtime)}
        self.receipt.write_text(json.dumps({'artifacts': {
            str(path.relative_to(stage)): {'bytes': path.stat().st_size, 'sha256': hashes[path.name]}
            for path in (self.artifact, self.runtime)}}))
        self.keep = self.repo / 'build/android-game/keep'
        self.keep.parent.mkdir(parents=True)
        self.keep.write_text('preserved game')
        (self.repo / '.env').write_text('PRIVATE_SENTINEL_DO_NOT_PRINT')
        self.config_path = self.base / 'config.json'
        self.config = {'calls': str(self.base / 'calls.jsonl'), 'serial':'127.0.0.1:5555',
                       'hashes':hashes, 'runtime_output':'\n'.join(MARKERS)}

    def invoke(self, serial='127.0.0.1:5555'):
        self.config['serial'] = serial
        self.config_path.write_text(json.dumps(self.config))
        calls = Path(self.config['calls'])
        calls.unlink(missing_ok=True)
        before, after = self.base / 'before.json', self.base / 'after.json'
        capture = '@{Path=$env:PATH; MSYSTEM=$env:MSYSTEM; BASH_ENV=$env:BASH_ENV; ENV=$env:ENV} | ConvertTo-Json | Set-Content -LiteralPath '
        launcher = self.base / 'run.ps1'
        launcher.write_text(capture + ps(before) + '\n& ' + ps(self.helper)
            + ' -PythonPath ' + ps(self.python) + ' -AdbPath ' + ps(self.adb) + ' -Serial ' + ps(serial)
            + '\n$code=$LASTEXITCODE\n' + capture + ps(after) + '\nexit $code\n')
        env = dict(os.environ, OS='Windows_NT', LOCALAPPDATA=str(self.base),
                   ALLIANCE_DEVICE_FIXTURE=str(self.config_path), MSYSTEM='unchanged',
                   BASH_ENV='parent hook', ENV='parent other hook')
        result = subprocess.run([PWSH, '-NoLogo', '-NoProfile', '-File', str(launcher)],
            cwd=self.base, env=env, capture_output=True, text=True, timeout=25)
        self.output = result.stdout + result.stderr
        self.calls = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
        self.assertEqual(json.loads(before.read_text()), json.loads(after.read_text()))
        self.assertEqual(self.keep.read_text(), 'preserved game')
        self.assertNotIn('PRIVATE_SENTINEL_DO_NOT_PRINT', self.output)
        self.logs = list((self.repo / 'diagnostics').glob('android-alliance-device-*.txt'))
        if self.logs:
            self.assertIn('Saved log:', self.output)
        return result.returncode

    def commands(self):
        return [call['args'][6:] for call in self.calls if call['tool']=='adb.exe' and call['args'][4]=='-s']

    def no_push(self):
        self.assertFalse(any(c[0]=='push' for c in self.commands()))

    def no_execution(self):
        self.assertFalse(any(c[0]=='shell' and len(c)==2 and c[1].startswith('cd ') for c in self.commands()))

    def test_success_verifies_before_adb_pushes_only_two_and_checks_hashes(self):
        self.assertEqual(self.invoke(), 0, self.output)
        self.assertEqual(self.calls[0]['tool'], 'python.exe')
        self.assertEqual(self.calls[0]['args'], ['-I','-B','-u',str(self.builder),'--verify'])
        self.assertEqual(len(self.calls),13)
        self.assertTrue(all(c['cwd']==str(self.repo) for c in self.calls))
        child = self.calls[0]['env']
        for key in ('BASH_ENV','ENV','PYTHONHOME','PYTHONPATH'):
            self.assertIsNone(child[key])
        self.assertEqual(child['PYTHONDONTWRITEBYTECODE'],'1')
        self.assertEqual(child['PYTHONNOUSERSITE'],'1')
        commands = self.commands()
        remote = commands[4][-1]
        self.assertRegex(remote,r'^/data/local/tmp/wesnoth-alliance-check/[0-9a-f]{32}$')
        self.assertEqual(commands[5:7], [['push',str(self.artifact),remote+'/android-alliance-check'],
            ['push',str(self.runtime),remote+'/libc++_shared.so']])
        self.assertEqual(commands[7:9], [['shell','sha256sum',remote+'/android-alliance-check'],
            ['shell','sha256sum',remote+'/libc++_shared.so']])
        self.assertIn('ANDROID ALLIANCE TEST PASSED',self.output)
        self.assertEqual(len(self.logs),1)
        log = self.logs[0].read_text()
        for text in ('ANDROID ALLIANCE TEST PASSED','VISIBLE VERIFY STDERR','VISIBLE RUNTIME STDERR'):
            self.assertIn(text,log)

    def test_verifier_failure_or_missing_stdout_marker_stops_before_adb(self):
        for change in ({'verify_exit':1}, {'verify_output':'missing'},
                       {'verify_output':'missing','verify_stderr':'ANDROID ALLIANCE ARTIFACTS VERIFIED'}):
            with self.subTest(change=change):
                previous=self.config.copy(); self.config.update(change)
                self.assertEqual(self.invoke(),2,self.output)
                self.assertEqual(len(self.calls),1)
                self.assertIn('ERROR:',self.logs[-1].read_text())
                self.config=previous

    def test_bad_artifacts_and_receipt_fail_before_adb(self):
        for path in (self.artifact,self.runtime,self.receipt):
            for kind in ('missing','empty','symlink'):
                with self.subTest(path=path.name,kind=kind):
                    data=path.read_bytes(); path.unlink()
                    if kind=='empty': path.write_bytes(b'')
                    if kind=='symlink':
                        target=self.base/'outside'; target.write_bytes(data); path.symlink_to(target)
                    self.assertEqual(self.invoke(),2,self.output)
                    self.assertEqual([c['tool'] for c in self.calls],['python.exe'])
                    path.unlink(missing_ok=True); path.write_bytes(data)

    def test_local_hash_differs_from_receipt_stops_before_adb(self):
        self.artifact.write_bytes(b'changed after verifier')
        self.assertEqual(self.invoke(),2,self.output)
        self.assertEqual(len(self.calls),1)
        self.assertIn('Artifact changed',self.output)

    def test_unready_or_wrong_device_stops_before_push(self):
        for change in ({'state':'offline'},{'state_exit':1},{'connect_exit':1},
                       {'properties':{'sys.boot_completed':'0'}},
                       {'properties':{'ro.build.version.sdk':'27'}},
                       {'properties':{'ro.build.version.sdk':'bad'}},
                       {'properties':{'ro.product.cpu.abi':'arm64-v8a'}}):
            with self.subTest(change=change):
                previous=self.config.copy(); self.config.update(change)
                self.assertEqual(self.invoke(),2,self.output); self.no_push()
                self.config=previous

    def test_failed_push_hash_or_chmod_prevents_execution(self):
        for change in ({'mkdir_exit':1},{'push_exits':{'android-alliance-check':1}},
                       {'push_exits':{'libc++_shared.so':1}},{'hash_exit':1},{'chmod_exit':1},
                       {'hash_output':{'android-alliance-check':'not a hash'}}):
            with self.subTest(change=change):
                previous=self.config.copy(); self.config.update(change)
                self.assertEqual(self.invoke(),2,self.output); self.no_execution()
                self.config=previous

    def test_remote_hash_mismatch_prevents_execution(self):
        self.config['hashes']['libc++_shared.so']='0'*64
        self.assertEqual(self.invoke(),2,self.output); self.no_execution()
        self.assertIn('transfer hash did not match',self.output)

    def test_nonzero_probe_and_failure_text_reject_success_markers(self):
        for change in ({'runtime_exit':1},{'runtime_stderr':'FAIL unexpected'},
                       {'runtime_stderr':'ERROR: unexpected'}):
            with self.subTest(change=change):
                previous=self.config.copy(); self.config.update(change)
                self.assertEqual(self.invoke(),2,self.output)
                self.assertNotIn('ANDROID ALLIANCE TEST PASSED',self.output)
                self.config=previous

    def test_missing_or_duplicate_probe_marker_is_failure(self):
        for lines in (MARKERS[:-1],MARKERS[:1]+MARKERS[2:],MARKERS+[MARKERS[-1]]):
            with self.subTest(lines=lines):
                self.config['runtime_output']='\n'.join(lines)
                self.assertEqual(self.invoke(),2,self.output)
                self.assertNotIn('ANDROID ALLIANCE TEST PASSED',self.output)

    def test_stderr_only_probe_marker_cannot_pass(self):
        self.config.update(runtime_output='\n'.join(MARKERS[:-1]),runtime_stderr=MARKERS[-1])
        self.assertEqual(self.invoke(),2,self.output)

    def test_verifier_and_runtime_timeouts_save_errors(self):
        self.helper.write_text(self.helper.read_text().replace('= 60000','= 300').replace('--verify\') 600000','--verify\') 300'))
        for key in ('verify_hang','runtime_hang'):
            with self.subTest(key=key):
                self.config[key]=True
                self.assertEqual(self.invoke(),2,self.output)
                self.assertIn('timed out',self.output)
                if key=='verify_hang': self.assertEqual(len(self.calls),1)
                self.config.pop(key)

    def test_repeated_runs_use_distinct_logs_and_device_directories(self):
        self.assertEqual(self.invoke(),0,self.output)
        first=self.commands()[4][-1]
        self.assertEqual(self.invoke(),0,self.output)
        self.assertNotEqual(first,self.commands()[4][-1])
        self.assertEqual(len(self.logs),2)

    def test_emulator_serial_does_not_connect_tcp_endpoint(self):
        self.assertEqual(self.invoke('emulator-5554'),0,self.output)
        self.assertEqual(len(self.calls),12)
        self.assertFalse(any(c['tool']=='adb.exe' and c['args'][4]=='connect' for c in self.calls))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--pwsh',required=True)
    args,remaining=parser.parse_known_args()
    PWSH=args.pwsh
    unittest.main(argv=[__file__,*remaining])
