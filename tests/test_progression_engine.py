"""Actual native campaign result capture, retained replay and mission semantics.

The explicit opt-in fixture links production modules and the exact end-of-level
hook to an isolated pinned host engine. It never contacts an account service.
"""
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import unittest

KEYS = ('ALLIANCE_PROGRESSION_ENGINE', 'ALLIANCE_PROGRESSION_DATA', 'ALLIANCE_PROGRESSION_SCRATCH')

@unittest.skipUnless(all(os.environ.get(k) for k in KEYS), 'Requires isolated native campaign fixture')
class ProgressionEngine(unittest.TestCase):
    def test_real_missions_results_and_replays(self):
        engine,data,scratch=(Path(os.environ[k]).resolve() for k in KEYS)
        scratch.mkdir(parents=True,exist_ok=False)
        userdata=scratch/'userdata';userdata.mkdir()
        private=scratch/'private';private.mkdir(mode=0o700)
        (private/'alliance-public-profile.json').write_text('{}')
        env=os.environ.copy();env.update(SDL_VIDEODRIVER='dummy',SDL_AUDIODRIVER='dummy',
             WESNOTH_ALLIANCE_PROFILE=str(private/'alliance-public-profile.json'),XDG_CACHE_HOME=str(scratch/'font-cache'))
        records=[]
        def command(name,args):
            result=subprocess.run(list(map(str,args)),env=env,text=True,capture_output=True,timeout=180)
            text=result.stdout+result.stderr;(scratch/(name+'.log')).write_text(text)
            records.append({'name':name,'exit':result.returncode,'args':list(map(str,args))})
            (scratch/'commands.json').write_text(json.dumps(records,indent=2))
            self.assertEqual(result.returncode,0,text[-5000:]);return text
        # The wrapped executable exports for ordinary arguments. Use the
        # pristine host executable for preprocessing only.
        host=Path(os.environ['ALLIANCE_PROGRESSION_HOST'])
        include=scratch/'era.cfg';include.write_text('{multiplayer/eras.cfg}\n')
        command('preprocess',[host,data,'--userdata-dir',userdata,'--preprocess',include,scratch/'preprocessed'])
        addon=userdata/'data/add-ons/alliance_progression_fixture'
        text=command('export',[engine,scratch/'preprocessed/era.cfg',data,addon])
        self.assertIn('checks=580; missions=3; leader_types=24',text)
        (addon/'_main.cfg').write_text('#ifdef MULTIPLAYER\n'+''.join(
            '{~add-ons/alliance_progression_fixture/mission-'+str(m)+'-'+str(v)+'.cfg}\n' for m in range(3) for v in range(2))+'#endif\n')
        results=[]
        for mission in range(3):
            for variant in range(2):
                run_id=f'20000000-0000-4000-8000-0000000000{mission}{variant}'
                text=command(f'mission-{mission}-{variant}',[engine,'--registered',mission,variant,'progression-fixture',data,
                    '--userdata-dir',userdata,'--multiplayer','--scenario','alliance_progression_'+run_id,
                    '--nogui','--nosound','--no-log-to-file','--log-strict','error','--log-none=audio'])
                self.assertIn('PROGRESSION START VERIFIED',text)
                self.assertIn('PROGRESSION AI ROUND VERIFIED',text)
                self.assertIn('PROGRESSION REAL '+('VICTORY' if variant==0 else 'DEFEAT'),text)
                checked=command(f'inspect-{mission}-{variant}',[engine,'--inspect-result',mission,variant,userdata])
                self.assertIn('PROGRESSION ACTUAL RESULT AND IDEMPOTENT ACK VERIFIED',checked)
                replay=userdata/'sync/saves'/f'Alliance-mission-{run_id}.gz'
                self.assertTrue(replay.is_file())
                self.assertGreater(replay.stat().st_size,100)
                self.assertNotIn(b'a'*64,gzip.decompress(replay.read_bytes()), 'run capability leaked into replay')
                results.append({'mission':mission+1,'outcome':'win' if variant==0 else 'loss',
                    'replay_sha256':hashlib.sha256(replay.read_bytes()).hexdigest(),'replay_bytes':replay.stat().st_size})
        checked=command('storage-contract',[engine,'--storage-contract',userdata])
        self.assertIn('PROGRESSION STORAGE CONTRACT VERIFIED',checked)
        (scratch/'result.json').write_text(json.dumps({'status':'passed','scope':'real Linux native mission/AI/end-hook/replay capture; no server settlement or Android UI','cases':results},indent=2))

if __name__=='__main__':unittest.main()
