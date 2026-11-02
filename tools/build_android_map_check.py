#!/usr/bin/env python3
"""Build a separate, locally signed touch-test companion from offline SDK tools."""
import argparse, hashlib, json, os
from pathlib import Path
import subprocess, zipfile

def build(sdk,java,out,signer_dir=None):
    out=out.resolve();out.mkdir(exist_ok=False,parents=True)
    src=Path(__file__).resolve().parent/'android-map-check';exe='.exe' if os.name=='nt' else ''
    tools=sdk/'build-tools/34.0.0';android=sdk/'platforms/android-35/android.jar'
    j=java/'bin'/('java'+exe);javac=java/'bin'/('javac'+exe);keytool=java/'bin'/('keytool'+exe)
    def run(args):subprocess.run([str(x) for x in args],check=True,timeout=120,stdin=subprocess.DEVNULL)
    classes=out/'classes';classes.mkdir();dex=out/'dex';dex.mkdir()
    run([javac,'-encoding','UTF-8','-source','8','-target','8','-classpath',android,'-d',classes,src/'MapGestureCheck.java'])
    with zipfile.ZipFile(out/'classes.jar','w') as jar:
        for path in sorted(classes.rglob('*.class')):jar.write(path,path.relative_to(classes).as_posix())
    run([j,'-cp',tools/'lib/d8.jar','com.android.tools.r8.D8','--min-api','23','--lib',android,'--output',dex,out/'classes.jar'])
    run([tools/('aapt2'+exe),'link','--manifest',src/'AndroidManifest.xml','-I',android,'-o',out/'unsigned.apk'])
    with zipfile.ZipFile(out/'unsigned.apk','a') as apk:apk.write(dex/'classes.dex','classes.dex')
    run([tools/('zipalign'+exe),'-p','4',out/'unsigned.apk',out/'aligned.apk'])
    # Retain one separate test signer so World then Area can install -r.
    # An interrupted signer creation stops; it never replaces an existing key.
    signer_dir=signer_dir or Path(__file__).resolve().parent.parent/'build/android-map-check-test-signer'
    if signer_dir.is_symlink():raise ValueError('Test signer directory is redirected')
    if not signer_dir.exists():
        signer_dir.mkdir(parents=True,exist_ok=False)
        run([keytool,'-genkeypair','-keystore',signer_dir/'test-only.keystore','-storepass','mapcheck-only','-keypass','mapcheck-only','-alias','mapcheck','-dname','CN=Local Map Check','-keyalg','RSA','-validity','3650'])
    signer=signer_dir/'test-only.keystore'
    if not signer.is_file() or signer.is_symlink():raise ValueError('Retain the incomplete test signer directory and report it')
    run([keytool,'-list','-keystore',signer,'-storepass','mapcheck-only','-alias','mapcheck'])
    apk=out/'map-check.apk'
    run([j,'-jar',tools/'lib/apksigner.jar','sign','--ks',signer,'--ks-pass','pass:mapcheck-only','--key-pass','pass:mapcheck-only','--out',apk,out/'aligned.apk'])
    run([j,'-jar',tools/'lib/apksigner.jar','verify',apk])
    report={'purpose':'local-map-input-companion','game_modified':False,'device_test_performed':False,'test_signer_sha256':hashlib.sha256(signer.read_bytes()).hexdigest(),'apk_sha256':hashlib.sha256(apk.read_bytes()).hexdigest(),'sources':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in src.iterdir() if p.is_file()}}
    (out/'build-report.json').write_text(json.dumps(report,indent=2)+'\n');print('MAP INPUT COMPANION BUILD VERIFIED')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--sdk',type=Path,required=True);p.add_argument('--java-home',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();build(a.sdk,a.java_home,a.output)
