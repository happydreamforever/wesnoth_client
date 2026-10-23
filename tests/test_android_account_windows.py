"""Execute account installer PowerShell with simulated tools; preserve the app/data."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[1]
PWSH = ROOT / 'build/host-tools/powershell-7.4.6/pwsh'
FAKE = r'''#!/usr/bin/python3
import hashlib,json,os,pathlib,sys,signal,subprocess,time
cfg=json.loads(pathlib.Path(os.environ['ACCOUNT_FIXTURE']).read_text()); args=sys.argv[1:]
name=pathlib.Path(sys.argv[0]).name
with open(cfg['calls'],'a') as out: out.write(json.dumps({'tool':name,'args':args})+'\n')
assert sys.stdin.read()==''
if name=='taskkill.exe':
 assert len(args)==4 and args[0]=='/PID' and args[2:]==['/T','/F'],args
 if cfg.get('taskkill_hang'):time.sleep(30)
 if cfg.get('taskkill_fail'):sys.exit(1)
 pids=json.loads(pathlib.Path(cfg['pids']).read_text())
 assert int(args[1])==pids['parent']
 for pid in (pids['child'],pids['parent']):
  try:os.kill(pid,signal.SIGKILL)
  except ProcessLookupError:pass
 print('fixture owned process tree stopped');sys.exit(0)
if name=='python.exe':
 if '--prepare-update' in args:
  print('ANDROID ACCOUNT UPDATE PREPARED');sys.exit(cfg.get('prepare_exit',0))
 if cfg.get('verify_hang') and '--verify-only' in args:
  child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
  pathlib.Path(cfg['pids']).write_text(json.dumps({'parent':os.getpid(),'child':child.pid}))
  print('fixture command started',flush=True);time.sleep(30)

 if '--ndk' in args:
  print(cfg.get('build_marker','ANDROID ACCOUNT GAME BUILD PASS'));print(cfg.get('build_stderr','fixture warning'),file=sys.stderr);sys.exit(cfg.get('build_exit',0))
 if any(x.endswith('validate_android_account_profile.py') for x in args):
  if cfg.get('profile_fail'): print('ERROR profile',file=sys.stderr);sys.exit(2)
  dest=pathlib.Path(args[args.index('--copy-to')+1]);dest.write_bytes(b'{"public":"fixture"}')
  print('PUBLIC ALLIANCE PROFILE VERIFIED');sys.exit(0)
 print(cfg.get('verify_marker','ANDROID ACCOUNT GAME ARTIFACTS VERIFIED'))
 sys.exit(cfg.get('verify_exit',0))
if name=='powershell.exe':
 print('ANDROID ACCOUNT GAME BUILD COMPLETE');sys.exit(cfg.get('rebuild_child_exit',0))
assert name=='adb.exe'
assert args[:4]==['-H','localhost','-P','5037']
if args[4]=='connect': print('connected');sys.exit(cfg.get('connect_exit',0))
assert args[4:6]==['-s','127.0.0.1:5555']; cmd=args[6:]
if cmd==['get-state']: print(cfg.get('state','device'))
elif cmd[:2]==['shell','getprop']:
 vals={'sys.boot_completed':'1','ro.build.version.sdk':'28','ro.product.cpu.abi':'x86_64'};print(cfg.get('props',{}).get(cmd[2],vals[cmd[2]]))
elif cmd[:3]==['shell','pm','path']:print(cfg.get('installed','package:/data/app/base.apk'))
elif cmd[:3]==['shell','dumpsys','package']:
 updated=pathlib.Path(cfg['installed_flag']).exists()
 default='  versionCode='+str(cfg.get('updated_version',1192616) if updated else cfg.get('installed_version',1192608))+' minSdk=23 targetSdk=35'
 print(cfg.get('updated_package_details' if updated else 'installed_package_details',default))
elif cmd[:2]==['install','-r']:
 pathlib.Path(cfg['installed_flag']).write_text('updated')
 print(cfg.get('install','Success'));sys.exit(cfg.get('install_exit',0))
elif cmd[:3]==['shell','mkdir','-p']:pass
elif cmd[:1]==['push']:
 assert pathlib.Path(cmd[1]).name.startswith('public-alliance-profile-');print('public profile push');sys.exit(cfg.get('push_exit',0))
elif cmd[:2]==['shell','sha256sum']:
 digest=hashlib.sha256(b'{"public":"fixture"}').hexdigest();print(cfg.get('remote_hash',digest)+'  '+cmd[2])
elif cmd[:3]==['shell','am','force-stop']:pass
elif cmd[:3]==['shell','am','start']:
 pathlib.Path(cfg['runid']).write_text(cmd[-1]);print(cfg.get('launch','Status: ok'))
elif cmd[:2]==['shell','pidof']:print('999')
elif cmd[:1]==['logcat']:
 print('WESNOTH_ACCOUNT_SETUP_READY run_id='+pathlib.Path(cfg['runid']).read_text())
else:raise AssertionError(cmd)
'''
def ps(value):return "'"+str(value).replace("'","''")+"'"

@unittest.skipUnless(PWSH.is_file(),'Portable PowerShell unavailable')
class InstallTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='account helpers ');self.addCleanup(self.temp.cleanup)
  self.base=Path(self.temp.name);self.repo=self.base/"client & literal $ ' with spaces"
  (self.repo/'tools').mkdir(parents=True)
  self.helper=self.repo/'tools/Install-AndroidAccountGame.ps1';shutil.copyfile(ROOT/'tools/Install-AndroidAccountGame.ps1',self.helper)
  for name in ('build_android_account_game.py','validate_android_account_profile.py'):(self.repo/'tools'/name).write_text('simulator')
  self.python=self.base/'Native Python/python.exe';self.adb=self.base/'Android SDK/platform-tools/adb.exe';self.java=self.base/'Java/bin/java.exe';self.taskkill=self.base/'Windows/System32/taskkill.exe'
  for file in (self.python,self.adb,self.java,self.taskkill):file.parent.mkdir(parents=True,exist_ok=True);file.write_text(FAKE);file.chmod(0o755)
  self.apk=self.repo/'build/android-account-game/app/build/outputs/apk/debug/app-debug.apk';self.apk.parent.mkdir(parents=True);self.apk.write_bytes(b'fixture apk')
  (self.repo/'build/android-account-game/receipt.json').write_text(json.dumps({'artifacts':{'app/build/outputs/apk/debug/app-debug.apk':{'bytes':self.apk.stat().st_size,'sha256':hashlib.sha256(self.apk.read_bytes()).hexdigest()}}}))
  self.profile=self.base/'public-profile.json';self.profile.write_text('fixture public')
  self.keep=self.repo/'build/android-game/keep';self.keep.parent.mkdir(parents=True);self.keep.write_text('unchanged game OBB data')
  (self.repo/'.env').write_text('SECRET_SENTINEL_NEVER_READ')
  self.protected_files={
   self.repo/'build/android-engine/receipt.json':b'original engine receipt',
   self.repo/'build/android-game-data/main.1192600.org.wesnoth.offline.obb':b'original OBB',
   self.repo/'build/android-game-host-user/.android/debug.keystore':b'unchanged signing fixture',
   self.base/'saved-app/private-profile.json':b'saved trusted profile fixture',
   self.base/'saved-app/credentials.json':b'saved credentials fixture',
   self.repo/'.env':b'SECRET_SENTINEL_NEVER_READ',
  }
  for path,data in self.protected_files.items():
   path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
  self.config={'calls':str(self.base/'calls.jsonl'),'runid':str(self.base/'runid'),'pids':str(self.base/'owned-pids.json'), 'installed_flag':str(self.base/'installed-flag')};self.configpath=self.base/'config.json'
  self.keep_saved_profile=False
  self.addCleanup(self.stop_fixture_processes)
 def stop_fixture_processes(self):
  path=Path(self.config['pids'])
  if path.exists():
   for pid in json.loads(path.read_text()).values():
    try:os.kill(pid,signal.SIGKILL)
    except ProcessLookupError:pass
 def invoke(self):
  self.configpath.write_text(json.dumps(self.config));Path(self.config['calls']).unlink(missing_ok=True)
  Path(self.config['installed_flag']).unlink(missing_ok=True)
  profile_arguments=' -KeepSavedProfile' if self.keep_saved_profile else ' -ProfilePath '+ps(self.profile)
  launcher=self.base/'run.ps1';launcher.write_text('& '+ps(self.helper)+' -PythonPath '+ps(self.python)+' -AdbPath '+ps(self.adb)+' -JavaHome '+ps(self.java.parent.parent)+' -SdkRoot '+ps(self.base/'Android SDK')+profile_arguments+'\nexit $LASTEXITCODE\n')
  env=dict(os.environ,OS='Windows_NT',LOCALAPPDATA=str(self.base),SystemRoot=str(self.base/'Windows'),ACCOUNT_FIXTURE=str(self.configpath))
  proc=subprocess.run([str(PWSH),'-NoProfile','-File',str(launcher)],env=env,capture_output=True,text=True,timeout=30)
  self.output=proc.stdout+proc.stderr; path=Path(self.config['calls']);self.calls=[json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
  self.assertEqual(self.keep.read_text(),'unchanged game OBB data');self.assertNotIn('SECRET_SENTINEL_NEVER_READ',self.output)
  for path,data in self.protected_files.items():self.assertEqual(path.read_bytes(),data)
  self.logs=list((self.repo/'diagnostics').glob('android-account-install-*.txt'));self.assertTrue(self.logs,self.output)
  return proc.returncode
 def commands(self):return [x['args'][6:] for x in self.calls if x['tool']=='adb.exe' and x['args'][4]=='-s']
 def test_update_and_public_snapshot_only(self):
  self.assertEqual(self.invoke(),0,self.output)
  cmd=self.commands();self.assertIn(['install','-r',str(self.apk)],cmd)
  pushes=[x for x in cmd if x[0]=='push'];self.assertEqual(len(pushes),1);self.assertNotEqual(pushes[0][1],str(self.profile));self.assertIn('/sdcard/Download/wesnoth-alliance-public-profile-',pushes[0][2])
  self.assertFalse(any('uninstall' in x or 'clear' in x for x in cmd));self.assertFalse(any('.obb' in str(x) for x in cmd))
  self.assertIn('ANDROID ACCOUNT GAME INSTALL COMPLETE',self.output)
 def test_preserve_saved_profile_previous_version_1192608_has_no_profile_read_or_copy(self):
  self.keep_saved_profile=True
  self.config['installed_version']=1192608
  self.profile.unlink()
  self.assertEqual(self.invoke(),0,self.output)
  cmd=self.commands();self.assertIn(['install','-r',str(self.apk)],cmd)
  self.assertEqual(sum(x[:3]==['shell','dumpsys','package'] for x in cmd),2)
  self.assertFalse(any(x[0] in ('push','uninstall') or 'clear' in x for x in cmd))
  self.assertFalse(any(any('validate_android_account_profile' in arg for arg in call['args']) for call in self.calls))
  self.assertFalse(list((self.repo/'diagnostics').glob('public-alliance-profile-*')))
  self.assertIn('Confirm the existing Trusted server is displayed',self.output)
  self.assertIn('Version 1192616 and setup activity startup are verified.',self.output)
  self.assertIn('If setup says no saved profile',self.output)
  self.assertNotIn('PUBLIC ALLIANCE PROFILE VERIFIED',self.output)
 def test_preserve_saved_profile_previous_version1192609_has_no_profile_read_or_copy(self):
  self.keep_saved_profile=True;self.config['installed_version']=1192609;self.profile.unlink()
  self.assertEqual(self.invoke(),0,self.output)
  cmd=self.commands();self.assertIn(['install','-r',str(self.apk)],cmd)
  self.assertFalse(any(x[0] in ('push','uninstall') or 'clear' in x for x in cmd))
  self.assertFalse(any(any('validate_android_account_profile' in arg for arg in call['args']) for call in self.calls))
  self.assertIn('Version 1192616 and setup activity startup are verified.',self.output)
 def test_preserve_saved_profile_previous_version1192610_has_no_profile_read_or_copy(self):
  self.keep_saved_profile=True;self.config['installed_version']=1192610;self.profile.unlink()
  self.assertEqual(self.invoke(),0,self.output)
  cmd=self.commands();self.assertIn(['install','-r',str(self.apk)],cmd)
  self.assertFalse(any(x[0] in ('push','uninstall') or 'clear' in x for x in cmd))
  self.assertFalse(any(any('validate_android_account_profile' in arg for arg in call['args']) for call in self.calls))
  self.assertIn('Version 1192616 and setup activity startup are verified.',self.output)
 def test_preserve_saved_profile_previous_version1192611_has_no_profile_read_or_copy(self):
  self.keep_saved_profile=True;self.config['installed_version']=1192611;self.profile.unlink()
  self.assertEqual(self.invoke(),0,self.output)
  cmd=self.commands();self.assertIn(['install','-r',str(self.apk)],cmd)
  self.assertFalse(any(x[0] in ('push','uninstall') or 'clear' in x for x in cmd))
  self.assertFalse(any(any('validate_android_account_profile' in arg for arg in call['args']) for call in self.calls))
  self.assertIn('Version 1192616 and setup activity startup are verified.',self.output)
 def test_preserve_saved_profile_previous_version1192612_has_no_profile_read_or_copy(self):
  self.keep_saved_profile=True;self.config['installed_version']=1192612;self.profile.unlink()
  self.assertEqual(self.invoke(),0,self.output)
  cmd=self.commands();self.assertIn(['install','-r',str(self.apk)],cmd)
  self.assertFalse(any(x[0] in ('push','uninstall') or 'clear' in x for x in cmd))
  self.assertFalse(any(any('validate_android_account_profile' in arg for arg in call['args']) for call in self.calls))
  self.assertIn('Version 1192616 and setup activity startup are verified.',self.output)
 def test_preserve_saved_profile_previous_version1192613_has_no_profile_read_or_copy(self):
  self.keep_saved_profile=True;self.config['installed_version']=1192613;self.profile.unlink()
  self.assertEqual(self.invoke(),0,self.output)
  cmd=self.commands();self.assertIn(['install','-r',str(self.apk)],cmd)
  self.assertFalse(any(x[0] in ('push','uninstall') or 'clear' in x for x in cmd))
  self.assertFalse(any(any('validate_android_account_profile' in arg for arg in call['args']) for call in self.calls))
  self.assertIn('Version 1192616 and setup activity startup are verified.',self.output)
 def test_preserve_saved_profile_previous_version1192614_has_no_profile_read_or_copy(self):
  self.keep_saved_profile=True;self.config['installed_version']=1192614;self.profile.unlink()
  self.assertEqual(self.invoke(),0,self.output)
  cmd=self.commands();self.assertIn(['install','-r',str(self.apk)],cmd)
  self.assertFalse(any(x[0] in ('push','uninstall') or 'clear' in x for x in cmd))
  self.assertFalse(any(any('validate_android_account_profile' in arg for arg in call['args']) for call in self.calls))
  self.assertIn('Version 1192616 and setup activity startup are verified.',self.output)
 def test_preserve_saved_profile_previous_version1192615_has_no_profile_read_or_copy(self):
  self.keep_saved_profile=True;self.config['installed_version']=1192615;self.profile.unlink()
  self.assertEqual(self.invoke(),0,self.output)
  cmd=self.commands();self.assertIn(['install','-r',str(self.apk)],cmd)
  self.assertFalse(any(x[0] in ('push','uninstall') or 'clear' in x for x in cmd))
  self.assertFalse(any(any('validate_android_account_profile' in arg for arg in call['args']) for call in self.calls))
  self.assertIn('Version 1192616 and setup activity startup are verified.',self.output)
 def test_keep_saved_profile_rejects_unexpected_old_version_before_install(self):
  self.keep_saved_profile=True
  for version in (1192600,1192601,1192602,1192603,1192604,1192605,1192606,1192607,1192617,1192699,'not a version'):
   with self.subTest(version=version):
    self.config['installed_version']=version
    self.assertEqual(self.invoke(),2,self.output)
    self.assertFalse(any(x[0]=='install' for x in self.commands()))
 def test_keep_saved_profile_allows_repeat_install_of_current_version(self):
  self.keep_saved_profile=True;self.config['installed_version']=1192616
  self.assertEqual(self.invoke(),0,self.output)
 def test_keep_saved_profile_checks_installed_new_version_before_launch(self):
  self.keep_saved_profile=True;self.config['updated_version']=1192608
  self.assertEqual(self.invoke(),2,self.output)
  self.assertFalse(any(x[:3]==['shell','am','start'] for x in self.commands()))
  self.assertNotIn('ANDROID ACCOUNT GAME INSTALL COMPLETE',self.output)
 def test_keep_saved_profile_rejects_ambiguous_package_version(self):
  self.keep_saved_profile=True
  for field,details in (
   ('installed_package_details','versionCode=1192608\nversionCode=1192609'),
   ('updated_package_details','versionCode=1192616\nversionCode=1192616'),
   ('updated_package_details','versionCode=invalid')):
   with self.subTest(field=field,details=details):
    original=self.config.copy();self.config[field]=details
    self.assertEqual(self.invoke(),2,self.output)
    self.assertFalse(any(x[:3]==['shell','am','start'] for x in self.commands()))
    if field=='installed_package_details':self.assertFalse(any(x[0]=='install' for x in self.commands()))
    self.assertNotIn('ANDROID ACCOUNT GAME INSTALL COMPLETE',self.output)
    self.config=original
 def test_verification_failures_precede_adb(self):
  for cfg in ({'verify_exit':2},{'verify_marker':'not complete'},{'profile_fail':True}):
   with self.subTest(cfg=cfg):
    original=self.config.copy();self.config.update(cfg);self.assertEqual(self.invoke(),2,self.output);self.assertFalse(any(x['tool']=='adb.exe' for x in self.calls));self.config=original
 def test_tampered_apk_precedes_adb(self):
  self.apk.write_bytes(b'changed');self.assertEqual(self.invoke(),2,self.output);self.assertFalse(any(x['tool']=='adb.exe' for x in self.calls))
 def test_wrong_device_and_absent_existing_game_prevent_install(self):
  for cfg in ({'props':{'ro.build.version.sdk':'27'}},{'state':'offline'},{'installed':''}):
   with self.subTest(cfg=cfg):
    old=self.config.copy();self.config.update(cfg);self.assertEqual(self.invoke(),2,self.output);self.assertFalse(any(x[0]=='install' for x in self.commands()));self.config=old
 def test_failed_install_does_not_uninstall_or_push_profile(self):
  self.config['install_exit']=1;self.assertEqual(self.invoke(),2,self.output);self.assertFalse(any(x[0] in ('push','uninstall') for x in self.commands()))
 def test_bad_public_transfer_prevents_launch(self):
  for cfg in ({'push_exit':1},{'remote_hash':'0'*64}):
   with self.subTest(cfg=cfg):
    old=self.config.copy();self.config.update(cfg);self.assertEqual(self.invoke(),2,self.output);self.assertFalse(any(x[:3]==['shell','am','start'] for x in self.commands()));self.config=old
 def test_launch_error_does_not_claim_success(self):
  self.config['launch']='Error: activity missing';self.assertEqual(self.invoke(),2,self.output);self.assertNotIn('ANDROID ACCOUNT GAME INSTALL COMPLETE',self.output)

 def timeout_case(self, changes):
  # Shorten only this copied fixture's verification deadline. Production remains ten minutes.
  self.helper.write_text(self.helper.read_text().replace(
   "$verifier, '--verify-only', '--sdk', $SdkRoot, '--java', $java) 600000",
   "$verifier, '--verify-only', '--sdk', $SdkRoot, '--java', $java) 600"))
  self.config.update({'verify_hang':True,**changes})
  self.assertEqual(self.invoke(),2,self.output)
  killers=[x for x in self.calls if x['tool']=='taskkill.exe']
  self.assertEqual(len(killers),1,self.output)
  pids=json.loads(Path(self.config['pids']).read_text())
  self.assertEqual(killers[0]['args'],['/PID',str(pids['parent']),'/T','/F'])
  self.assertFalse(any(x['tool']=='adb.exe' for x in self.calls))
  self.assertNotIn('ANDROID ACCOUNT GAME INSTALL COMPLETE',self.output)
  return pids
 def test_timeout_stops_owned_parent_and_descendant(self):
  pids=self.timeout_case({})
  self.assertIn('fixture owned process tree stopped',self.output)
  self.assertIn('fixture command started',self.output)
  for pid in pids.values():
   status=Path('/proc')/str(pid)/'stat'
   if status.exists():self.assertEqual(status.read_text().split()[2],'Z')
 def test_taskkill_failure_is_reported_and_never_claims_cleanup(self):
  self.timeout_case({'taskkill_fail':True})
  self.assertIn('Taskkill failed with exit 1',self.output)
  self.assertIn('descendants could not be confirmed stopped',self.output)
 def test_taskkill_timeout_is_bounded_and_reported(self):
  self.timeout_case({'taskkill_hang':True})
  self.assertIn('Taskkill timed out',self.output)
  self.assertIn('descendants could not be confirmed stopped',self.output)

@unittest.skipUnless(PWSH.is_file(),'Portable PowerShell unavailable')
class BuildTests(unittest.TestCase):
 setUp = InstallTests.setUp
 stop_fixture_processes = InstallTests.stop_fixture_processes
 def invoke_build(self):
  helper=self.repo/'tools/Build-WindowsAndroidAccountGame.ps1';shutil.copyfile(ROOT/'tools/Build-WindowsAndroidAccountGame.ps1',helper)
  sdk=self.base/'Android SDK';(sdk/'ndk/26.3.11579264').mkdir(parents=True,exist_ok=True)
  cmake=self.base/'CMake bin/cmake.exe';ninja=self.base/'Ninja bin/ninja.exe';kill=self.base/'Windows/System32/taskkill.exe'
  for file in (cmake,ninja,kill):file.parent.mkdir(parents=True,exist_ok=True);file.write_text(FAKE);file.chmod(0o755)
  self.configpath.write_text(json.dumps(self.config));Path(self.config['calls']).unlink(missing_ok=True)
  launcher=self.base/'build.ps1';launcher.write_text('& '+ps(helper)+' -PythonPath '+ps(self.python)+' -JavaHome '+ps(self.java.parent.parent)+' -SdkRoot '+ps(sdk)+' -CMakePath '+ps(cmake)+' -NinjaPath '+ps(ninja)+' -GradleUserHome '+ps(self.base/'Gradle cache')+'\nexit $LASTEXITCODE\n')
  env=dict(os.environ,OS='Windows_NT',LOCALAPPDATA=str(self.base),USERPROFILE=str(self.base),SystemRoot=str(self.base/'Windows'),ACCOUNT_FIXTURE=str(self.configpath))
  proc=subprocess.run([str(PWSH),'-NoProfile','-File',str(launcher)],env=env,capture_output=True,text=True,timeout=30)
  self.output=proc.stdout+proc.stderr
  self.calls=[json.loads(x) for x in Path(self.config['calls']).read_text().splitlines()]
  self.assertEqual(self.keep.read_text(),'unchanged game OBB data');self.assertNotIn('SECRET_SENTINEL_NEVER_READ',self.output)
  for path,data in self.protected_files.items():self.assertEqual(path.read_bytes(),data)
  self.assertTrue(list((self.repo/'diagnostics').glob('android-account-build-*.txt')),self.output)
  return proc.returncode
 def test_build_command_includes_native_tools_and_saves_log(self):
  self.assertEqual(self.invoke_build(),0,self.output)
  self.assertEqual(len(self.calls),1);args=self.calls[0]['args']
  self.assertIn('--cmake',args);self.assertIn('--ninja',args);self.assertIn('--gradle-user-home',args)
  self.assertIn('ANDROID ACCOUNT GAME BUILD COMPLETE',self.output)
 def test_failed_build_or_stderr_only_marker_cannot_pass(self):
  for cfg in ({'build_exit':1},{'build_marker':'not complete','build_stderr':'ANDROID ACCOUNT GAME BUILD PASS'}):
   with self.subTest(cfg=cfg):
    old=self.config.copy();self.config.update(cfg);self.assertEqual(self.invoke_build(),2,self.output);self.assertNotIn('ANDROID ACCOUNT GAME BUILD COMPLETE',self.output);self.config=old
 def test_missing_receipt_prevents_build_success(self):
  (self.repo/'build/android-account-game/receipt.json').unlink();self.assertEqual(self.invoke_build(),2,self.output);self.assertNotIn('ANDROID ACCOUNT GAME BUILD COMPLETE',self.output)

@unittest.skipUnless(PWSH.is_file(),'Portable PowerShell unavailable')
class RebuildTests(unittest.TestCase):
 setUp = InstallTests.setUp
 stop_fixture_processes = InstallTests.stop_fixture_processes
 def invoke_rebuild(self):
  helper=self.repo/'tools/Rebuild-WindowsAndroidAccountGame.ps1'
  shutil.copyfile(ROOT/'tools/Rebuild-WindowsAndroidAccountGame.ps1',helper)
  child=self.base/'fake shell/powershell.exe';child.parent.mkdir(exist_ok=True)
  child.write_text(FAKE);child.chmod(0o755)
  self.configpath.write_text(json.dumps(self.config));Path(self.config['calls']).unlink(missing_ok=True)
  launcher=self.base/'rebuild.ps1'
  launcher.write_text('& '+ps(helper)+' -PythonPath '+ps(self.python)+' -JavaHome '+ps(self.java.parent.parent)+
   ' -SdkRoot '+ps(self.base/'Android SDK')+' -CMakePath '+ps(self.base/'cmake.exe')+
   ' -NinjaPath '+ps(self.base/'ninja.exe')+' -GradleUserHome '+ps(self.base/'Gradle cache')+
   ' -ArchiveDirectory '+ps(self.base/'Original archives')+'\nexit $LASTEXITCODE\n')
  env=dict(os.environ,OS='Windows_NT',LOCALAPPDATA=str(self.base),USERPROFILE=str(self.base),
   ACCOUNT_FIXTURE=str(self.configpath),PATH=str(child.parent)+os.pathsep+os.environ['PATH'])
  proc=subprocess.run([str(PWSH),'-NoProfile','-File',str(launcher)],env=env,capture_output=True,text=True,timeout=30)
  self.output=proc.stdout+proc.stderr
  self.calls=[json.loads(x) for x in Path(self.config['calls']).read_text().splitlines()]
  self.assertEqual(self.keep.read_text(),'unchanged game OBB data')
  for path,data in self.protected_files.items():self.assertEqual(path.read_bytes(),data)
  self.assertTrue(list((self.repo/'diagnostics').glob('android-account-rebuild-*.txt')),self.output)
  return proc.returncode
 def test_preparation_precedes_build_and_preserves_literal_arguments(self):
  self.assertEqual(self.invoke_rebuild(),0,self.output)
  self.assertEqual([x['tool'] for x in self.calls],['python.exe','powershell.exe'])
  self.assertEqual(self.calls[0]['args'],['-I','-B','-u',str(self.repo/'tools/build_android_account_game.py'),'--prepare-update'])
  args=self.calls[1]['args']
  for name,value in (('-JavaHome',self.java.parent.parent),('-GradleUserHome',self.base/'Gradle cache'),
                     ('-ArchiveDirectory',self.base/'Original archives')):
   self.assertEqual(args[args.index(name)+1],str(value))
  self.assertIn('ANDROID ACCOUNT GAME REBUILD COMPLETE',self.output)
  self.assertIn('completed 1192608, 1192609, 1192610, 1192611, 1192612, 1192613, 1192614 or 1192615 account stages before the 1192616 unified-game update',self.output)
  self.assertIn('-KeepSavedProfile',self.output)
 def test_failed_preparation_prevents_build(self):
  self.config['prepare_exit']=2
  self.assertEqual(self.invoke_rebuild(),2,self.output)
  self.assertEqual([x['tool'] for x in self.calls],['python.exe'])
  self.assertNotIn('ANDROID ACCOUNT GAME REBUILD COMPLETE',self.output)
 def test_failed_build_does_not_claim_success(self):
  self.config['rebuild_child_exit']=2
  self.assertEqual(self.invoke_rebuild(),2,self.output)
  self.assertIn('Original stages remain in their recorded backups',self.output)
  self.assertNotIn('ANDROID ACCOUNT GAME REBUILD COMPLETE',self.output)

if __name__=='__main__':unittest.main()
