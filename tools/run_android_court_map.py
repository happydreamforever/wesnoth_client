#!/usr/bin/env python3
"""Send a bounded two-pointer gesture to the already-open Court map only."""
import argparse, importlib.util, json, re, subprocess
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--sdk',type=Path,required=True);p.add_argument('--java-home',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--adb',type=Path,required=True)
    p.add_argument('--serial',default='127.0.0.1:5555');p.add_argument('--screen',choices=('world','area'),required=True)
    p.add_argument('--map-is-open',action='store_true',required=True);a=p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]*',a.serial):raise ValueError('Invalid device serial')
    spec=importlib.util.spec_from_file_location('map_check_build',Path(__file__).with_name('build_android_map_check.py'))
    builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    def adb(*args,timeout=45):
        r=subprocess.run([str(a.adb),'-s',a.serial,*map(str,args)],capture_output=True,text=True,timeout=timeout,stdin=subprocess.DEVNULL)
        if r.returncode:raise RuntimeError('ADB step failed: '+args[0])
        return r.stdout+r.stderr
    if adb('get-state').strip()!='device':raise RuntimeError('Existing LDPlayer device is unavailable')
    installed=adb('shell','dumpsys','package','org.wesnoth.offline')
    if re.findall(r'\bversionCode=(\d+)\b',installed)!=['1192627']:raise RuntimeError('Install verified game 1192627 first')
    activity=adb('shell','dumpsys','activity','activities')
    resumed=[line for line in activity.splitlines() if 'ResumedActivity' in line or 'mFocusedActivity' in line]
    if not any('org.wesnoth.offline/.OfflineGameActivity' in line for line in resumed):raise RuntimeError('Keep the Court map open in the foreground')
    builder.build(a.sdk,a.java_home,a.output)
    if 'Success' not in adb('install','-r',a.output/'map-check.apk',timeout=120):raise RuntimeError('Companion installation did not succeed')
    result=adb('shell','am','instrument','-w','-e','screen',a.screen,'org.wesnoth.mapcheck/.MapGestureCheck',timeout=45)
    (a.output/'device-result.txt').write_text(result,encoding='utf-8');print(result)
    if 'MOBILE MAP GESTURES SENT; VISUAL REVIEW REQUIRED' not in result or 'INSTRUMENTATION_CODE: -1' not in result:raise RuntimeError('Gesture instrumentation did not complete; retain its report')
    paths=re.findall(r'Images: (/storage/emulated/\d+/Android/data/org\.wesnoth\.mapcheck/files/map-check-(?:world|area)-\d+)',result)
    if len(paths)!=1:raise RuntimeError('Companion image path was unexpected; retained on device')
    adb('pull',paths[0],a.output/'images',timeout=60)
    report={'purpose':'court-map-synthetic-touch','screen':a.screen,'gestures_sent':True,'physical_touchscreen':False,'visual_acceptance':'pending','game_data_modified':False}
    (a.output/'device-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print('MAP INPUT CHECK COMPLETE. Review movement and retained before/after images; this does not declare UI acceptance.')
if __name__=='__main__':main()
