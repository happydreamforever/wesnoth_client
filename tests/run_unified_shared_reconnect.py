from pathlib import Path
import argparse,json,subprocess,os,time,gzip
parser=argparse.ArgumentParser()
parser.add_argument("--fixture",type=Path,required=True)
parser.add_argument("--output",type=Path,required=True)
parser.add_argument("--binary",type=Path,required=True)
args=parser.parse_args()
os.umask(0o077)
root=args.output;root.mkdir();fixture=args.fixture;data=json.loads(fixture.read_text());children=[];records=[]
assert data['purpose']=='disposable-shared-engine-fixture' and data['size']==2
def spawn(index,reconnect=False):
 actor=data['players'][index];own=root/str(index)
 if not reconnect:
  own.mkdir();(own/'private').mkdir();(own/'userdata').mkdir();profile=dict(data['profile']);profile['port']=15002;(own/'private/alliance-public-profile.json').write_text(json.dumps(profile))
 env=os.environ.copy();env.update(SDL_VIDEODRIVER='dummy',SDL_AUDIODRIVER='dummy',WESNOTH_ALLIANCE_PROFILE=str(own/'private/alliance-public-profile.json'),XDG_CACHE_HOME=str(own/'font-cache'))
 if index==2 and not reconnect:env['ALLIANCE_SHARED_FIXTURE_DELAY']='yes'
 log=(own/('reconnect.log' if reconnect else 'engine.log')).open('w')
 cmd=[str(args.binary),'--shared-seat',str(fixture),str(index),'shared-fixture','/gamedata','--userdata-dir',str(own/'userdata'),'--test=test','--multiplayer','--nogui','--nosound','--no-log-to-file','--log-none=audio']
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
  row={'side':actor['side'],'exit':p.returncode,'started':'SHARED ENGINE STARTED' in text,'initial':'SHARED REAL INITIAL CONFIG VERIFIED' in text,'round':'SHARED REAL NETWORK ROUND VERIFIED' in text,'second_round':'SHARED REAL SECOND NETWORK ROUND VERIFIED' in text,'finished':'SHARED ENGINE FINISHED' in text,'acknowledged':'SHARED RESULT UPLOAD ACKNOWLEDGED' in text,'failed':'SHARED ENGINE FAILED' in text};records.append(row)
 (root/'result.json').write_text(json.dumps(records,indent=2))
 assert all(r['exit']==0 and r['started'] and r['initial'] and r['round'] and r['second_round'] and r['finished'] and r['acknowledged'] and not r['failed'] for r in records),records
 for _,_,own,actor in children:
  pending=own/'private/alliance-shared'/actor['account_id']/(data['run_id']+'.cfg');assert 'status="acknowledged"' in pending.read_text()
  replay=own/'userdata/sync/saves'/('Alliance-battle-'+data['run_id']+'-'+actor['account_id']+'.gz');assert replay.is_file()
  assert actor['token'].encode() not in gzip.decompress(replay.read_bytes())
 print('PASS four actual engine seats, disconnected-seat history replay/reconnect, two shared rounds, native END and acknowledged durable replays',flush=True)
finally:
 for p,log,_,_ in children:
  if p.poll() is None:p.kill();p.wait()
  log.close()
