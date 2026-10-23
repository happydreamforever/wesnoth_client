"""Real four-engine reconnect/END fixture against an owned disposable daemon.

Requires ALLIANCE_SHARED_ENGINE_PROBE, ALLIANCE_SHARED_ENGINE_FIXTURE,
ALLIANCE_SHARED_ENGINE_SCRATCH and ALLIANCE_SHARED_GAMEDATA. The provided engine
must link shared_engine_probe.cpp and the documented test-only campaign wrapper.
The daemon fixture must be fresh and use the production domain/TLS protocol.
"""
import unittest
from pathlib import Path
import json,subprocess,os,time,gzip

@unittest.skipUnless(os.environ.get('ALLIANCE_SHARED_ENGINE_PROBE'), 'Requires isolated real engine and disposable daemon fixture')
class SharedEngine(unittest.TestCase):
 def test_four_seats_reconnect_native_end_and_durable_upload(self):
  root=Path(os.environ['ALLIANCE_SHARED_ENGINE_SCRATCH']);root.mkdir();fixture=Path(os.environ['ALLIANCE_SHARED_ENGINE_FIXTURE']);data=json.loads(fixture.read_text());children=[];records=[]
  assert data['purpose']=='disposable-shared-engine-fixture' and data['database'].startswith('alliance_test_shared_engine_')
  assert len(data['players'])==4 and data['players'][2]['side']==2
  binary=os.environ['ALLIANCE_SHARED_ENGINE_PROBE'];gamedata=os.environ['ALLIANCE_SHARED_GAMEDATA']
  def spawn(index,reconnect=False):
   actor=data['players'][index];own=root/str(index)
   if not reconnect:
    own.mkdir();(own/'private').mkdir();(own/'userdata').mkdir();profile=dict(data['profile']);profile['port']=15002;(own/'private/alliance-public-profile.json').write_text(json.dumps(profile))
   env=os.environ.copy();env.update(SDL_VIDEODRIVER='dummy',SDL_AUDIODRIVER='dummy',WESNOTH_ALLIANCE_PROFILE=str(own/'private/alliance-public-profile.json'),XDG_CACHE_HOME=str(own/'font-cache'))
   if index==2 and not reconnect:env['ALLIANCE_SHARED_FIXTURE_DELAY']='yes'
   log=(own/('reconnect.log' if reconnect else 'engine.log')).open('w')
   cmd=[binary,'--shared-seat',str(fixture),str(index),'shared-fixture',gamedata,'--userdata-dir',str(own/'userdata'),'--test=test','--multiplayer','--nogui','--nosound','--no-log-to-file','--log-none=audio']
   return subprocess.Popen(cmd,env=env,stdout=log,stderr=subprocess.STDOUT),log,own,actor
  try:
   for index in range(4):children.append(spawn(index))
   end=time.monotonic()+150
   events=fixture.parent/'battles'/data['run_id']/'events.jsonl'
   while time.monotonic()<end:
    if events.exists() and any('[end_turn]' in json.loads(line)['wml'] for line in events.read_text().splitlines()):break
    if any(p.poll() is not None for p,_,_,_ in children):raise RuntimeError('Engine stopped before reconnect boundary')
    time.sleep(.1)
   else:raise RuntimeError('First actual side did not finish')
   p,log,_,_=children[2];p.kill();p.wait();log.close();(root/'disconnect.json').write_text(json.dumps({'side':2,'exit':p.returncode,'after_durable_first_turn':True}))
   time.sleep(1);children[2]=spawn(2,True)
   end=time.monotonic()+180
   while time.monotonic()<end and any(p.poll() is None for p,_,_,_ in children):time.sleep(.2)
   for p,log,own,actor in children:
    if p.poll() is None:p.terminate()
    try:p.wait(timeout=10)
    except subprocess.TimeoutExpired:p.kill();p.wait()
    log.close();text=(own/('reconnect.log' if actor['side']==2 else 'engine.log')).read_text()
    row={'side':actor['side'],'exit':p.returncode,'started':'SHARED ENGINE STARTED' in text,'round':'SHARED REAL NETWORK ROUND VERIFIED' in text,'second_round':'SHARED REAL SECOND NETWORK ROUND VERIFIED' in text,'finished':'SHARED ENGINE FINISHED' in text,'acknowledged':'SHARED RESULT UPLOAD ACKNOWLEDGED' in text,'failed':'SHARED ENGINE FAILED' in text};records.append(row)
   (root/'result.json').write_text(json.dumps(records,indent=2))
   assert all(r['exit']==0 and r['started'] and r['round'] and r['second_round'] and r['finished'] and r['acknowledged'] and not r['failed'] for r in records),records
   for _,_,own,actor in children:
    pending=own/'private/alliance-shared'/actor['account_id']/(data['run_id']+'.cfg');assert 'status="acknowledged"' in pending.read_text()
    replay=own/'userdata/sync/saves'/('Alliance-battle-'+data['run_id']+'-'+actor['account_id']+'.gz');assert replay.is_file()
    assert actor['token'].encode() not in gzip.decompress(replay.read_bytes())
   print('PASS four actual engine seats, disconnected-seat history replay/reconnect, two shared rounds, native END and acknowledged durable replays',flush=True)
  finally:
   for p,log,_,_ in children:
    if p.poll() is None:p.kill();p.wait()
    log.close()

if __name__=='__main__':unittest.main()
