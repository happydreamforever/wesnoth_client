"""Opt-in actual six/eight-seat shared engine gameplay, END and replay test.

Requires a fresh disposable daemon fixture using the same contract as
``test_shared_engine.py``. Set ALLIANCE_SHARED_MULTISEAT_SIZE to 3 or 4,
ALLIANCE_SHARED_ENGINE_FIXTURE to its private fixture JSON,
ALLIANCE_SHARED_ENGINE_PROBE to the linked real engine fixture executable,
ALLIANCE_SHARED_ENGINE_SCRATCH to a new absolute result folder, and
ALLIANCE_SHARED_GAMEDATA to the supplied read-only 1.19.26 data tree.

One fixture captain passes turns then surrenders at turn 3; all other seats run
actual engine AI. This proves two synchronized rounds and retained results,
not Android rendering, Windows resources or multiple-human acceptance.
"""
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from types import SimpleNamespace
import unittest


def run_multiseat(size, fixture, output, binary, gamedata):
    args = SimpleNamespace(size=size, fixture=fixture, output=output,
                           binary=binary, gamedata=gamedata)
    if not args.output.is_absolute() or args.output.exists():
        raise RuntimeError('A new absolute audit output directory is required')
    if not args.binary.is_file() or args.binary.stat().st_size < 1000000:
        raise RuntimeError('Completed native fixture executable is required')
    data = json.loads(args.fixture.read_text())
    assert data['purpose'] == 'disposable-shared-engine-fixture'
    assert data['size'] == args.size and len(data['players']) == args.size * 2
    assert data['map_id'] == {3: 'hexcake', 4: 'mokena-prairie'}[args.size]
    assert data['captain_indices'] == [0, args.size]
    assert sorted(actor['side'] for actor in data['players']) == list(range(1, args.size * 2 + 1))
    args.output.mkdir(mode=0o700)
    children = []
    records = []
    started_at = time.monotonic()
    try:
        for index, actor in enumerate(data['players']):
            own = args.output / str(index)
            own.mkdir(); (own / 'private').mkdir(); (own / 'userdata').mkdir()
            profile = dict(data['profile']); profile['port'] = 15002
            public = own / 'private/alliance-public-profile.json'
            public.write_text(json.dumps(profile))
            env = dict(os.environ, SDL_VIDEO_DRIVER='dummy', SDL_AUDIO_DRIVER='dummy',
                SDL_VIDEODRIVER='dummy', SDL_AUDIODRIVER='dummy',
                WESNOTH_ALLIANCE_PROFILE=str(public), ALLIANCE_SHARED_FIXTURE_TERMINAL='yes',
                XDG_CACHE_HOME=str(own / 'font-cache'))
            extra = []
            if index == 0:
                env['ALLIANCE_SHARED_FIXTURE_HUMAN'] = 'yes'
                plugin = own / 'captain.lua'
                plugin.write_text('''return function(events, context, info)
     local sent=false
     while true do
      if info.name=='Game' and info.can_move().can_move and info.current_side().current_side==SIDE then
       if not sent and info.current_turn().current_turn>=3 then
        sent=true;std_print('MULTISEAT CAPTAIN TERMINAL REQUESTED AFTER TWO ROUNDS')
        context.synced_command{{'custom_command',{name='alliance_captain_v1',{'data',{action='surrender'}}}}}
       else context.end_turn{} end
      elseif info.name=='Dialog' then context.skip_dialog{} end
      events,context,info=wesnoth.plugin.next_slice()
     end
    end
    '''.replace('SIDE', str(actor['side'])))
                extra = ['--plugin', str(plugin)]
            log = (own / 'engine.log').open('x')
            command = [str(args.binary), '--shared-seat', str(args.fixture), str(index), 'shared-fixture',
                str(args.gamedata), '--userdata-dir', str(own / 'userdata'), '--test=test', '--multiplayer',
                '--nogui', '--nosound', '--no-log-to-file', '--log-none=audio', *extra]
            process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)
            children.append((process, log, own, actor))
        deadline = time.monotonic() + 480
        while time.monotonic() < deadline and any(process.poll() is None for process, *_ in children):
            time.sleep(.25)
        for process, log, own, actor in children:
            timed_out = process.poll() is None
            if timed_out: process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
            log.close()
            contents = (own / 'engine.log').read_text()
            row = {'side': actor['side'], 'exit': process.returncode, 'timed_out': timed_out,
                'started': 'SHARED ENGINE STARTED' in contents,
                'initial_config_verified': 'SHARED REAL INITIAL CONFIG VERIFIED' in contents,
                'network_round_verified': 'SHARED REAL NETWORK ROUND VERIFIED' in contents,
                'finished': 'SHARED ENGINE FINISHED' in contents,
                'acknowledged': 'SHARED RESULT UPLOAD ACKNOWLEDGED' in contents,
                'failed': 'SHARED ENGINE FAILED' in contents}
            journal = own / 'private/alliance-shared' / actor['account_id'] / (data['run_id'] + '.cfg')
            if journal.is_file():
                saved = journal.read_text()
                row['journal_acknowledged'] = 'status="acknowledged"' in saved
                row['journal_draw_false'] = 'draw=no' in saved
                row['journal_winner_matches_opponent'] = 'winner_team_id="' + data['teams'][1] + '"' in saved
                name = re.search(r'^replay_filename="([A-Za-z0-9.-]+)"$', saved, re.M)
                expected_hash = re.search(r'^replay_sha256="([0-9a-f]{64})"$', saved, re.M)
                replays = list((own / 'userdata').rglob(name[1])) if name else []
                if len(replays) == 1 and expected_hash:
                    replay = replays[0].read_bytes()
                    row['replay_hash_verified'] = hashlib.sha256(replay).hexdigest() == expected_hash[1]
                    wml = gzip.decompress(replay).decode()
                    row['replay_end_turn_commands'] = wml.count('[end_turn]')
            records.append(row)
        result = {'size': args.size, 'map_id': data['map_id'], 'clients': len(records),
            'seconds': round(time.monotonic() - started_at, 3),
            'binary_sha256': hashlib.sha256(args.binary.read_bytes()).hexdigest(), 'records': records,
            'scope': 'Real Linux host engine, strict TLS daemon and disposable MongoDB; one scripted human captain plus computer-controlled seats. Fixture prestart assertions and explicit surrender at round3; not Android rendering or multiple-human acceptance.'}
        (args.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        assert all(row['exit'] == 0 and not row['timed_out'] and row['started'] and
            row['initial_config_verified'] and row['network_round_verified'] and row['finished'] and
            row['acknowledged'] and not row['failed'] and row.get('journal_acknowledged') and
            row.get('journal_draw_false') and row.get('journal_winner_matches_opponent') and
            row.get('replay_hash_verified') and row.get('replay_end_turn_commands', 0) >= args.size * 4
            for row in records), records
        assert 'MULTISEAT CAPTAIN TERMINAL REQUESTED AFTER TWO ROUNDS' in (args.output / '0/engine.log').read_text()
        print('PASS actual ' + str(args.size) + 'v' + str(args.size) + ' native clients: fixed initial config, two rounds, captain surrender and all replay acknowledgements', flush=True)
    finally:
        for process, log, _, _ in children:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
            log.close()


@unittest.skipUnless(os.environ.get('ALLIANCE_SHARED_MULTISEAT_SIZE'),
                     'Requires isolated real engine and a fresh six/eight-seat battle')
class SharedMultiseatEngineTests(unittest.TestCase):
    def test_two_rounds_then_native_surrender_and_all_replay_acknowledgements(self):
        size = int(os.environ['ALLIANCE_SHARED_MULTISEAT_SIZE'])
        self.assertIn(size, (3, 4))
        fixture = Path(os.environ['ALLIANCE_SHARED_ENGINE_FIXTURE'])
        metadata = json.loads(fixture.read_text())
        self.assertTrue(metadata['database'].startswith('alliance_test_shared_engine_'))
        expected = {3: [[1, 4, 5], [2, 3, 6]], 4: [[1, 3, 5, 8], [2, 4, 6, 7]]}[size]
        self.assertEqual([[p['side'] for p in team['players']] for team in metadata['battle']['teams']], expected)
        self.assertTrue(all(team['approved'] and all(p['ready'] for p in team['players'])
                            for team in metadata['battle']['teams']))
        run_multiseat(size, fixture, Path(os.environ['ALLIANCE_SHARED_ENGINE_SCRATCH']),
                      Path(os.environ['ALLIANCE_SHARED_ENGINE_PROBE']),
                      Path(os.environ['ALLIANCE_SHARED_GAMEDATA']))


if __name__ == '__main__': unittest.main()
