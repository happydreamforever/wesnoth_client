"""Actual mixed battle: same-account AI-host reconnect and read-only spectator."""
import argparse, json, os, subprocess, time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--fixture',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--binary',type=Path,required=True);a=p.parse_args()
os.umask(0o077);f=json.loads(a.fixture.read_text());assert f['purpose']=='disposable-shared-engine-fixture' and f['flexible'] and len(f['players'])==2 and len(f['observers'])==1
assert a.output.is_absolute() and not a.output.exists();a.output.mkdir();children=[]
def spawn(index,observer=False,reconnect=False):
 actor=f['observers' if observer else 'players'][index];own=a.output/('observer' if observer else str(index));own.mkdir(exist_ok=reconnect)
 if not reconnect:
  (own/'private').mkdir();(own/'userdata').mkdir();profile=dict(f['profile']);profile['port']=15002;(own/'private/alliance-public-profile.json').write_text(json.dumps(profile))
 env=dict(os.environ,SDL_VIDEODRIVER='dummy',SDL_AUDIODRIVER='dummy',WESNOTH_ALLIANCE_PROFILE=str(own/'private/alliance-public-profile.json'),ALLIANCE_SHARED_FIXTURE_TERMINAL='yes',ALLIANCE_SHARED_FIXTURE_FLEXIBLE='yes',XDG_CACHE_HOME=str(own/'font-cache'))
 extra=[]
 if observer:
  env['ALLIANCE_SHARED_FIXTURE_OBSERVER']='yes'
  extra=['--log-debug=engine,network,replay']
 elif index==0:
  env['ALLIANCE_SHARED_FIXTURE_HUMAN']='yes';plugin=own/'captain.lua'
  plugin.write_text('''return function(events,context,info)
 local sent=false local hold=nil
 while true do
  if info.name=='Game' and info.can_move().can_move and info.current_side().current_side==4 then
   if not hold then hold=wesnoth.ms_since_init()+30000 end
   if wesnoth.ms_since_init()>=hold then
    if not sent and info.current_turn().current_turn>=3 then
     sent=true;std_print('MIXED CAPTAIN TERMINAL AFTER HOST RECONNECT')
     context.synced_command{{'custom_command',{name='alliance_captain_v1',{'data',{action='surrender'}}}}}
    else context.end_turn{} end
   end
  elseif info.name=='Dialog' then context.skip_dialog{} end
  events,context,info=wesnoth.plugin.next_slice()
 end
end
''');extra=['--plugin',str(plugin)]
 log=(own/('reconnect.log' if reconnect else 'engine.log')).open('x')
 cmd=[str(a.binary),'--shared-seat',str(a.fixture),str(index),'shared-fixture','/gamedata','--userdata-dir',str(own/'userdata'),'--test=test','--multiplayer','--nogui','--nosound','--no-log-to-file','--log-none=audio',*extra]
 return [subprocess.Popen(cmd,env=env,stdout=log,stderr=subprocess.STDOUT),log,own,actor]
def stop(row):
 process,log,*_=row
 if process.poll() is None:process.terminate()
 try:process.wait(timeout=10)
 except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
 log.close()
try:
 children=[spawn(0),spawn(1)]
 events=a.fixture.parent/'battles'/f['run_id']/'events.jsonl';deadline=time.monotonic()+180
 while time.monotonic()<deadline:
  if events.exists() and '[end_turn]' in events.read_text():break
  if any(row[0].poll() is not None for row in children):raise RuntimeError('A client stopped before host reconnect boundary')
  time.sleep(.02)
 else:raise RuntimeError('No durable native turn before reconnect')
 children[1][0].kill();children[1][0].wait();children[1][1].close()
 time.sleep(.5);before=events.read_bytes();time.sleep(2);assert events.read_bytes()==before,'Battle advanced without AI host'
 (a.output/'pause.json').write_text(json.dumps({'host_side':2,'same_account':True,'durable_bytes_while_host_absent':len(before),'paused':True}))
 children[1]=spawn(1,reconnect=True)
 children.append(spawn(0,observer=True))
 deadline=time.monotonic()+360
 while time.monotonic()<deadline and any(row[0].poll() is None for row in children):time.sleep(.2)
 records=[]
 for index,row in enumerate(children):
  timed_out=row[0].poll() is None;stop(row);own=row[2];text=(own/('reconnect.log' if index==1 else 'engine.log')).read_text()
  record={'side':row[3]['side'],'observer':index==2,'exit':row[0].returncode,'timed_out':timed_out,'started':'SHARED ENGINE STARTED' in text,'initial':'SHARED REAL INITIAL CONFIG VERIFIED' in text,'round':'SHARED REAL NETWORK ROUND VERIFIED' in text,'finished':'SHARED ENGINE FINISHED' in text,'acknowledged':'SHARED RESULT UPLOAD ACKNOWLEDGED' in text,'failed':'SHARED ENGINE FAILED' in text};records.append(record)
  if index==2:assert not (own/'private/alliance-shared').exists(),'Observer created player result journal'
 (a.output/'validation.json').write_text(json.dumps({'records':records,'host_pause_verified':True,'same_account_reconnect':True,'scope':'actual Linux engine and strict TLS/Mongo; scripted participants, not multiple human or Android acceptance'},indent=2))
 assert all(r['exit']==0 and not r['timed_out'] and r['started'] and r['initial'] and r['round'] and r['finished'] and not r['failed'] and r['acknowledged']==(not r['observer']) for r in records),records
 print('PASS native mixed battle: host loss pauses, same-account host reconnects, observer catches up without control or rewards, both human replays acknowledged')
finally:
 for row in children:stop(row)
