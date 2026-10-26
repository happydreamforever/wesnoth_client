"""Exercise public-config trust, real X.509 verification and owned APK assets."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bundle_test', ROOT / 'tools/android_public_bundle.py')
B = importlib.util.module_from_spec(spec); spec.loader.exec_module(B)


class PublicBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='public-config-trust-')
        cls.base = Path(cls.temporary.name)
        cls.certificates = {}
        for name, constraints, purpose, days in (
                ('valid', 'CA:FALSE', 'serverAuth', '3'), ('client', 'CA:FALSE', 'clientAuth', '3'),
                ('ca', 'CA:TRUE', 'serverAuth', '3'), ('expired', 'CA:FALSE', 'serverAuth', '-1')):
            cert = cls.base / (name + '.pem')
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '3',
                            '-keyout', str(cls.base / (name + '.key')), '-out', str(cert), '-subj', '/CN=Public fixture',
                            '-addext', 'basicConstraints=critical,' + constraints,
                            '-addext', 'keyUsage=critical,digitalSignature,keyEncipherment',
                            '-addext', 'extendedKeyUsage=' + purpose,
                            '-addext', 'subjectAltName=IP:100.99.99.14'], check=True, capture_output=True)
            if days == '-1':
                request = cls.base / 'expired.csr'
                subprocess.run(['openssl', 'x509', '-x509toreq', '-in', str(cert), '-signkey', str(cls.base / (name + '.key')),
                                '-out', str(request)], check=True, capture_output=True)
                (cls.base / 'index').write_text(''); (cls.base / 'serial').write_text('01\n')
                configuration = cls.base / 'ca.cnf'
                configuration.write_text('[ca]\ndefault_ca=fixture\n[fixture]\ndatabase=' + str(cls.base / 'index') +
                    '\nserial=' + str(cls.base / 'serial') + '\nnew_certs_dir=' + str(cls.base) +
                    '\ndefault_md=sha256\npolicy=subject\nx509_extensions=extensions\n[subject]\ncommonName=supplied\n' +
                    '[extensions]\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n' +
                    'extendedKeyUsage=serverAuth\nsubjectAltName=IP:100.99.99.14\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always\n')
                expired = cls.base / 'expired-result.pem'
                subprocess.run(['openssl', 'ca', '-batch', '-selfsign', '-config', str(configuration),
                                '-keyfile', str(cls.base / (name + '.key')), '-cert', str(cert), '-in', str(request),
                                '-startdate', '20000101000000Z', '-enddate', '20000102000000Z', '-out', str(expired), '-notext'],
                               check=True, capture_output=True)
                cert = expired
            cls.certificates[name] = cert.read_text()

    @classmethod
    def tearDownClass(cls): cls.temporary.cleanup()

    def setUp(self):
        self.temporary_case = tempfile.TemporaryDirectory(prefix='public-client-case-')
        self.addCleanup(self.temporary_case.cleanup)
        self.repo = Path(self.temporary_case.name)
        self.config = self.repo / '.env'
        self.profile = self.repo / 'public profile.json'
        self.config.write_text('WESNOTH_PUBLIC_PROFILE_PATH="public profile.json"\n')
        self.document = dict(format=1, protocol='wesnoth-alliance-tls', server_address='100.99.99.14',
                             port=15001, minimum_tls='1.2', certificate_pem=self.certificates['valid'])
        self.write_profile()

    def write_profile(self):
        self.document['certificate_sha256'] = hashlib.sha256(ssl.PEM_cert_to_DER_cert(self.document['certificate_pem'])).hexdigest()
        self.profile.write_text(json.dumps(self.document))

    def test_existing_public_identity_is_canonicalized_and_bound(self):
        data, binding = B.public_profile(self.repo)
        self.assertEqual(json.loads(data), self.document)
        self.assertEqual(binding['sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(binding['certificate_sha256'], self.document['certificate_sha256'])
        self.assertNotIn(str(self.config), json.dumps(binding))
        self.assertNotIn('PRIVATE', data.decode())

    def test_certificate_purpose_ca_expiry_and_ip_rejected(self):
        for name in ('client', 'ca', 'expired'):
            with self.subTest(name=name):
                self.document['certificate_pem'] = self.certificates[name]; self.write_profile()
                with self.assertRaises(ValueError): B.public_profile(self.repo)
        self.document['certificate_pem'] = self.certificates['valid']
        self.document['server_address'] = '100.99.99.15'; self.write_profile()
        with self.assertRaises(ValueError): B.public_profile(self.repo)

    def test_pin_mismatch_and_private_fields_are_rejected(self):
        self.document['certificate_sha256'] = '0' * 64
        self.profile.write_text(json.dumps(self.document))
        with self.assertRaises(ValueError): B.public_profile(self.repo)
        self.write_profile(); self.document['server_key'] = 'PRIVATE_SENTINEL'
        self.profile.write_text(json.dumps(self.document))
        with self.assertRaisesRegex(ValueError, 'No private material was printed') as result: B.public_profile(self.repo)
        self.assertNotIn('PRIVATE_SENTINEL', str(result.exception))

    def test_literal_path_no_expansion_execution_or_duplicate_public_key(self):
        for value in ('', 'DB_PASSWORD=PRIVATE_SENTINEL', 'WESNOTH_PUBLIC_PROFILE_PATH=one\nWESNOTH_PUBLIC_PROFILE_PATH=two',
                      'WESNOTH_PUBLIC_PROFILE_PATH=$(touch marker)', 'WESNOTH_PUBLIC_PROFILE_PATH=`touch marker`',
                      'WESNOTH_PUBLIC_PROFILE_PATH=$HOME/profile', 'WESNOTH_PUBLIC_PROFILE_PATH="unclosed',
                      'export WESNOTH_PUBLIC_PROFILE_PATH=profile', 'WESNOTH_PUBLIC_PROFILE_PATH=profile\nINVALID KEY=secret'):
            with self.subTest(value=value):
                self.config.write_text(value)
                with self.assertRaises(ValueError) as result: B.configured_profile(self.repo)
                self.assertNotIn('PRIVATE_SENTINEL', str(result.exception))
        self.assertFalse((self.repo / 'marker').exists())

    def test_unrelated_environment_assignments_never_enter_assets_or_receipts(self):
        baseline, identity = B.public_profile(self.repo)
        decoys = ('mongodb://PRIVATE_USER:PRIVATE_PASSWORD@localhost/private',
                  'PRIVATE_PASSWORD_1', '$(touch PRIVATE_EXECUTION_MARKER)')
        self.config.write_text('\ufeffMONGODB_URI=' + decoys[0] + '\nPRIVATE_PASSWORD=' + decoys[1] +
            '\nIGNORED_SHELL_TEXT=' + decoys[2] + '\n' + self.config.read_text())
        before = self.config.read_bytes()
        with patch.object(B, 'artwork', return_value={'startup/fixture': b'public artwork'}):
            files, receipt = B.bundle(self.repo)
        self.assertEqual(files[B.PROFILE_ASSET], baseline)
        self.assertEqual(receipt['public_server'], identity)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertNotIn('.env', files)
        for secret in decoys:
            self.assertNotIn(secret.encode(), b''.join(files.values()))
            self.assertNotIn(secret, json.dumps(receipt))
        self.assertFalse((self.repo / 'PRIVATE_EXECUTION_MARKER').exists())

    def test_missing_oversized_and_linked_config_fail_closed(self):
        self.config.unlink()
        with self.assertRaises(ValueError): B.configured_profile(self.repo)
        self.config.write_text('x' * 8193)
        with self.assertRaises(ValueError): B.configured_profile(self.repo)
        self.config.unlink(); self.config.symlink_to(self.profile)
        with self.assertRaises(ValueError): B.configured_profile(self.repo)

    def test_linked_public_profile_is_rejected(self):
        target = self.repo / 'original'; self.profile.rename(target); self.profile.symlink_to(target)
        with self.assertRaises(ValueError): B.public_profile(self.repo)

    def test_duplicate_profile_keys_and_noncanonical_ip_rejected(self):
        self.profile.write_text(self.profile.read_text().replace('"format": 1', '"format": 1,"format": 1'))
        with self.assertRaises(ValueError): B.public_profile(self.repo)
        for address in ('localhost', '0100.99.99.14', '224.0.0.1'):
            self.document['server_address'] = address; self.write_profile()
            with self.assertRaises(ValueError): B.public_profile(self.repo)

    def test_openssl_failure_does_not_print_certificate_or_process_output(self):
        with patch.object(B.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixture', 15, output=b'PRIVATE_SENTINEL')):
            with self.assertRaises(ValueError) as result: B.public_profile(self.repo)
        self.assertNotIn('PRIVATE_SENTINEL', str(result.exception))

    def test_java_bundled_profile_is_authoritative_and_preserves_legacy_import(self):
        # Android storage/AtomicFile are substitutes; certificate parsing, bounds,
        # hashing and materialization are the actual delivered Java methods.
        source = self.repo / 'java'; source.mkdir()
        data = self.profile.read_bytes()
        definitions = {
            'android/content/Context.java': '''package android.content; import java.io.File;
public class Context { private final File base; public Context(File b){base=b;}
public File getFilesDir(){return new File(base,"files");}
public android.content.res.AssetManager getAssets(){return new android.content.res.AssetManager(new File(base,"assets"));}}''',
            'android/content/res/AssetManager.java': '''package android.content.res; import java.io.*;
public class AssetManager { private final File base; public AssetManager(File b){base=b;}
public InputStream open(String name)throws IOException{return new FileInputStream(new File(base,name));}}''',
            'android/util/AtomicFile.java': '''package android.util; import java.io.*;
public class AtomicFile { private final File file; public AtomicFile(File f){file=f;}
public FileInputStream openRead()throws IOException{return new FileInputStream(file);}
public FileOutputStream startWrite()throws IOException{return new FileOutputStream(file);}
public void finishWrite(FileOutputStream s)throws IOException{s.close();}
public void failWrite(FileOutputStream s){try{s.close();}catch(IOException e){}}}''',
            'org/json/JSONException.java': 'package org.json; public class JSONException extends Exception {}',
            'org/json/JSONObject.java': '''package org.json; public class JSONObject {
public JSONObject(String s)throws JSONException{throw new JSONException();}
public JSONArray getJSONArray(String n)throws JSONException{throw new JSONException();}
public String getString(String n)throws JSONException{throw new JSONException();}
public int getInt(String n)throws JSONException{throw new JSONException();}}''',
            'org/json/JSONArray.java': '''package org.json; public class JSONArray {
public int length(){return 0;} public JSONObject getJSONObject(int i)throws JSONException{throw new JSONException();}}''',
            'org/wesnoth/offline/BundleHarness.java': '''package org.wesnoth.offline;
import java.io.*; import java.nio.file.*; import java.util.*;
public class BundleHarness {public static void main(String[] args)throws Exception{
 File base=new File(args[0]); android.content.Context context=new android.content.Context(base);
 Path legacy=new File(context.getFilesDir(),"alliance-public-profile.json").toPath();
 Files.write(legacy,"unrelated legacy trust kept".getBytes("UTF-8"));
 File active=BundledStartup.prepareProfile(context); byte[] expected=Files.readAllBytes(active.toPath());
 if(!active.getName().equals("alliance-bundled-profile.json"))throw new AssertionError("wrong active path");
 if(!new String(Files.readAllBytes(legacy),"UTF-8").equals("unrelated legacy trust kept"))throw new AssertionError("legacy changed");
 active.setLastModified(100000); BundledStartup.prepareProfile(context);
 if(active.lastModified()!=100000)throw new AssertionError("unchanged profile rewritten");
 Files.write(active.toPath(),"stale active copy".getBytes("UTF-8")); BundledStartup.prepareProfile(context);
 if(!Arrays.equals(expected,Files.readAllBytes(active.toPath())))throw new AssertionError("not authoritative");
 Files.write(new File(base,"assets/alliance-public-profile.json").toPath(),"tampered".getBytes("UTF-8"));
 try {BundledStartup.prepareProfile(context);throw new AssertionError("tamper accepted");}catch(IOException expectedError){}
 if(!Arrays.equals(expected,Files.readAllBytes(active.toPath())))throw new AssertionError("tamper changed active");
 System.out.println("BUNDLED PROFILE JAVA PASS");}}''',
        }
        for name in ('BundledStartup', 'AlliancePublicProfile'):
            definitions['org/wesnoth/offline/' + name + '.java'] = (ROOT / 'tools/native-account-game' / (name + '.java')).read_text().replace(
                '@PUBLIC_PROFILE_SHA256@', hashlib.sha256(data).hexdigest()).replace('@UI_MANIFEST_SHA256@', '0' * 64)
        for name, text in definitions.items():
            path = source / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text)
        files = self.repo / 'device/files'; files.mkdir(parents=True)
        assets = self.repo / 'device/assets'; assets.mkdir(); (assets / 'alliance-public-profile.json').write_bytes(data)
        classes = self.repo / 'classes'
        subprocess.run(['javac', '--release', '8', '-d', str(classes), *map(str, source.rglob('*.java'))], check=True, capture_output=True, timeout=30)
        result = subprocess.run(['java', '-cp', str(classes), 'org.wesnoth.offline.BundleHarness', str(files.parent)],
                                check=True, capture_output=True, text=True, timeout=30)
        self.assertIn('BUNDLED PROFILE JAVA PASS', result.stdout)

    def test_actual_pinned_startup_art_and_empty_ui_manifest(self):
        assets = B.artwork(ROOT)
        for name, (_, size, sha) in B.STARTUP.items():
            self.assertEqual(B.record(assets[name]), {'bytes': size, 'sha256': sha})
        self.assertEqual(json.loads(assets[B.UI_MANIFEST])['format'], 1)

    def test_asset_manifest_rejects_path_traversal_and_tampering(self):
        root = self.repo / 'tools/native-account-game'; root.mkdir(parents=True)
        (self.repo / 'build/android-game-data').mkdir(parents=True)
        with zipfile.ZipFile(self.repo / 'build/android-game-data/main.1192600.org.wesnoth.offline.obb', 'w') as archive:
            archive.writestr('fixture', b'fixture')
        entry = {'path': 'hero/example.webp', 'bytes': 3, 'sha256': hashlib.sha256(b'art').hexdigest()}
        asset = root / 'ui-assets/hero/example.webp'; asset.parent.mkdir(parents=True); asset.write_bytes(b'art')
        manifest = root / 'ui-assets.json'
        with patch.object(B, 'STARTUP', {}):
            manifest.write_text(json.dumps({'format': 1, 'files': [entry]}))
            self.assertEqual(B.artwork(self.repo)['alliance-ui/hero/example.webp'], b'art')
            asset.write_bytes(b'bad')
            with self.assertRaises(ValueError): B.artwork(self.repo)
            for path in ('../secret', 'hero/../../secret', '/absolute', 'hero/../secret'):
                entry['path'] = path; manifest.write_text(json.dumps({'format': 1, 'files': [entry]}))
                with self.assertRaises(ValueError): B.artwork(self.repo)


class ProgressTests(unittest.TestCase):
    def test_real_installer_interruption_resume_and_tamper_never_report_early_completion(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary); source = directory / 'org/wesnoth/offline'; source.mkdir(parents=True)
            for name in ('StartupProgress', 'OfflineDataInstaller'):
                shutil.copyfile(ROOT / ('tools/native-account-game/' + name + '.java'), source / (name + '.java'))
            payload = {'data/a.bin': b'a' * 100000, 'data/b.bin': b'b' * 200000, 'data/c.bin': b'c' * 300000}
            manifest = b''.join((json.dumps([name, len(data), hashlib.sha256(data).hexdigest()], separators=(',', ':')) + '\n').encode() for name, data in payload.items())
            (directory / 'manifest').write_bytes(manifest)
            with zipfile.ZipFile(directory / 'data.obb', 'w', compression=zipfile.ZIP_STORED) as archive:
                for name, data in payload.items():
                    entry = zipfile.ZipInfo(name); entry.create_system = 3; entry.external_attr = 0o100644 << 16
                    archive.writestr(entry, data)
            (source / 'InstallerHarness.java').write_text('''package org.wesnoth.offline;
import java.io.*;import java.nio.file.*;import java.util.*;
public class InstallerHarness {
 static final OfflineDataInstaller.FileGuard GUARD=new OfflineDataInstaller.FileGuard(){
  public boolean exists(File f){return Files.exists(f.toPath(),LinkOption.NOFOLLOW_LINKS);}
  public void check(File f)throws IOException{if(Files.isSymbolicLink(f.toPath()))throw new IOException("link");}
 };
 public static void main(String[] args)throws Exception{
  Path root=Paths.get(args[0]),base=Files.createDirectory(root.resolve("storage"));
  OfflineDataInstaller.Manifest manifest=OfflineDataInstaller.readManifest(Files.newInputStream(root.resolve("manifest")),args[1],3,600000L);
  List<String> phases=new ArrayList<>();List<Integer> values=new ArrayList<>();
  OfflineDataInstaller.Progress progress=(phase,files,totalFiles,bytes,totalBytes)->{
   phases.add(phase);int value=StartupProgress.percent(bytes,totalBytes,false);values.add(value);
   if(value<0||value>99)throw new AssertionError("early completion");
   if(value!=(int)Math.min(99,bytes*100/totalBytes))throw new AssertionError("unmeasured percent");
  };
  try{
   OfflineDataInstaller.install(base.toFile(),root.resolve("data.obb").toFile(),manifest,GUARD,(phase,files,n,bytes,total)->{
    progress.update(phase,files,n,bytes,total);
    if(phase.equals("Installing game data")&&files==1)Thread.currentThread().interrupt();
   });throw new AssertionError("interruption ignored");
  }catch(InterruptedIOException expected){Thread.interrupted();}
  if(Files.exists(base.resolve(".wesnoth-gamedata-ready"))||Files.exists(base.resolve("gamedata")))throw new AssertionError("interrupted publication");
  Path retained=base.resolve(".wesnoth-gamedata-import/payload/data/a.bin");byte[] saved=Files.readAllBytes(retained);
  File installed=OfflineDataInstaller.install(base.toFile(),root.resolve("data.obb").toFile(),manifest,GUARD,progress);
  if(!Arrays.equals(saved,Files.readAllBytes(installed.toPath().resolve("data/a.bin"))))throw new AssertionError("retained data changed");
  if(!phases.contains("Checking interrupted import")||!Files.isRegularFile(base.resolve(".wesnoth-gamedata-ready")))throw new AssertionError("resume not checked");
  if(StartupProgress.percent(manifest.bytes,manifest.bytes,true)!=100||values.get(values.size()-1)!=99)throw new AssertionError("publication boundary");
  OfflineDataInstaller.install(base.toFile(),root.resolve("data.obb").toFile(),manifest,GUARD,progress);
  byte[] damaged=new byte[200000];Arrays.fill(damaged,(byte)'x');Path target=installed.toPath().resolve("data/b.bin");Files.write(target,damaged);
  try{OfflineDataInstaller.install(base.toFile(),root.resolve("data.obb").toFile(),manifest,GUARD,progress);throw new AssertionError("tamper accepted");}catch(IOException expected){}
  if(!Arrays.equals(damaged,Files.readAllBytes(target)))throw new AssertionError("tampered user file replaced");
  if(values.contains(100))throw new AssertionError("early100");
  System.out.println("INSTALLER INTERRUPTION RESUME TAMPER PROGRESS PASS");
 }}''')
            subprocess.run(['javac', '--release', '8', '-d', str(directory), *map(str, source.glob('*.java'))], check=True, capture_output=True)
            result = subprocess.run(['java', '-cp', str(directory), 'org.wesnoth.offline.InstallerHarness', str(directory), hashlib.sha256(manifest).hexdigest()], check=True, capture_output=True, text=True)
            self.assertIn('INSTALLER INTERRUPTION RESUME TAMPER PROGRESS PASS', result.stdout)

    def test_actual_activity_progress_callback_shows_only_measured_percent(self):
        activity = (ROOT / 'tools/native-account-game/OfflineInitActivity.java').read_text()
        start = activity.index('    private void showProgress(')
        end = activity.index('    @Override protected void onDestroy()', start)
        function = activity[start:end]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary); source = directory / 'org/wesnoth/offline'; source.mkdir(parents=True)
            shutil.copyfile(ROOT / 'tools/native-account-game/StartupProgress.java', source / 'StartupProgress.java')
            (source / 'ActivityProgressHarness.java').write_text('''package org.wesnoth.offline;
public class ActivityProgressHarness {
 static class SystemClock {static long tick;static long elapsedRealtime(){return tick+=200;}}
 static class Log {static String last;static void i(String tag,String text){last=text;}}
 static class Text {String value;void setText(String text){value=text;}}
 static class Bar {int value;boolean indeterminate;void setProgress(int v){value=v;}void setIndeterminate(boolean v){indeterminate=v;}}
 String runId="fixture",lastPhase="";long lastProgress;boolean destroyed,finishing;
 static final String TAG="fixture";Text status=new Text();Bar progress=new Bar();
 boolean isFinishing(){return finishing;}void runOnUiThread(Runnable action){action.run();}
/* @CALLBACK@ */
 public static void main(String[] args){
  ActivityProgressHarness a=new ActivityProgressHarness();
  for(int bytes:new int[]{0,1,50,99,100}){
   a.showProgress("Checking game data",bytes,100,bytes,100);
   int expected=Math.min(99,bytes);
   if(a.progress.indeterminate||a.progress.value!=expected||!a.status.value.equals(expected+"%"))throw new AssertionError("visible processing text or wrong percent");
  }
  a.showProgress("Installing game data",0,100,0,100);
  if(!a.status.value.equals("0%")||!Log.last.contains("phase=Installing game data files=0/100"))throw new AssertionError("phase leaked or lost");
  a.destroyed=true;a.showProgress("Installing game data",50,100,50,100);
  if(!a.status.value.equals("0%"))throw new AssertionError("destroyed view updated");
  System.out.println("ACTIVITY PERCENT ONLY PASS");
 }}'''.replace('/* @CALLBACK@ */', function))
            subprocess.run(['javac', '--release', '8', '-d', str(directory), *map(str, source.glob('*.java'))], check=True, capture_output=True)
            result = subprocess.run(['java', '-cp', str(directory), 'org.wesnoth.offline.ActivityProgressHarness'], check=True, capture_output=True, text=True)
            self.assertIn('ACTIVITY PERCENT ONLY PASS', result.stdout)

    def test_real_java_progress_waits_for_commit_and_handles_large_totals(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / 'org/wesnoth/offline'; source.mkdir(parents=True)
            shutil.copyfile(ROOT / 'tools/native-account-game/StartupProgress.java', source / 'StartupProgress.java')
            (source / 'ProgressHarness.java').write_text('''package org.wesnoth.offline;
public class ProgressHarness { public static void main(String[] args) {
 long total=726207542L; int previous=-1;
 for(long done=0;done<=total;done+=100001) {
  int value=StartupProgress.percent(done,total,false);
  if(value<previous||value>99)throw new AssertionError("nonmonotonic");previous=value;
 }
 if(StartupProgress.percent(total,total,false)!=99||StartupProgress.percent(total,total,true)!=100)
  throw new AssertionError("premature completion");
 if(StartupProgress.percent(Long.MAX_VALUE/2,Long.MAX_VALUE,false)!=50)
  throw new AssertionError("overflow");
 if(StartupProgress.percent(-1,10,false)!=0||StartupProgress.percent(0,0,false)!=0)
  throw new AssertionError("empty counters");
 System.out.println("STARTUP PROGRESS PASS"); }}''')
            subprocess.run(['javac', '-d', str(directory), *map(str, source.glob('*.java'))], check=True, capture_output=True)
            result = subprocess.run(['java', '-cp', str(directory), 'org.wesnoth.offline.ProgressHarness'], check=True, capture_output=True, text=True)
            self.assertIn('STARTUP PROGRESS PASS', result.stdout)


class ApkBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('account_asset_inspector', ROOT / 'tools/build_android_account_game.py')
        cls.A = importlib.util.module_from_spec(spec); spec.loader.exec_module(cls.A)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='account-apk-assets-'); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name); self.apk = self.root / 'fixture.apk'
        manifest = (ROOT / 'build/android-game-data/gamedata-files.jsonl').read_bytes()
        self.assets = {'gamedata-files.jsonl': manifest, 'alliance-public-profile.json': b'public fixture',
                       'alliance-ui-manifest.json': b'{"format":1,"files":[]}\n', 'startup/logo.png': b'fixture art'}
        self.content = {'AndroidManifest.xml': b'compiled manifest', 'classes.dex': b'compiled java'}
        for name in self.A.LIBRARIES:
            data = ('fixture payload ' + name).encode(); path = self.root / self.A.JNI_ROOT / name
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
            self.content['lib/x86_64/' + name] = data
        self.marker = {'files': {}, 'inputs': {'bundled_assets': {}}}
        for name, data in self.assets.items():
            path = self.root / 'app/src/main/assets' / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
            self.marker['files']['app/src/main/assets/' + name] = B.record(data)
            if name != 'gamedata-files.jsonl': self.marker['inputs']['bundled_assets'][name] = B.record(data)
            self.content['assets/' + name] = data
        (self.root / self.A.MARKER).write_text(json.dumps(self.marker)); self.write_apk()

    def write_apk(self):
        with zipfile.ZipFile(self.apk, 'w') as archive:
            for name, data in self.content.items(): archive.writestr(name, data)

    def test_new_apk_inspector_binds_every_public_asset(self):
        result = self.A.inspect_apk(self.apk, self.root)
        self.assertEqual(result['version_code'], 1192621)
        self.assertEqual(result['activity'], 'org.wesnoth.offline.OfflineInitActivity')
        self.assertEqual(result['bundled_assets'], self.marker['inputs']['bundled_assets'])

    def test_tampered_missing_unexpected_and_native_assets_rejected(self):
        original = self.content.copy()
        for name, data in (('assets/startup/logo.png', b'changed'), ('assets/.env', b'SECRET_SENTINEL'),
                           ('lib/x86_64/libmain.so', b'changed')):
            with self.subTest(name=name):
                self.content = dict(original, **{name: data}); self.write_apk()
                with self.assertRaises(self.A.BuildError): self.A.inspect_apk(self.apk, self.root)
        self.content = original.copy(); del self.content['assets/alliance-public-profile.json']; self.write_apk()
        with self.assertRaises(self.A.BuildError): self.A.inspect_apk(self.apk, self.root)

    def test_historical1192620_keeps_original_single_asset_and_launcher(self):
        self.content = {name: data for name, data in self.content.items() if not name.startswith('assets/') or name == 'assets/gamedata-files.jsonl'}
        self.write_apk()
        result = self.A.inspect_apk(self.apk, self.root, 1192620)
        self.assertEqual(result['activity'], 'org.wesnoth.offline.AccountSetupActivity')
        self.assertNotIn('bundled_assets', result)
        self.assertEqual(result['version_code'], 1192620)


if __name__ == '__main__': unittest.main()
