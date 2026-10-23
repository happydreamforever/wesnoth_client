"""Opt-in real native captain commands, END, replay upload and companion stdin.

Uses the same disposable daemon and linked real engine contract as
``test_shared_engine.py``. Every invocation requires a fresh approved battle.
ALLIANCE_SHARED_TERMINAL_MODE is companion-draw or draw-sequence.
"""
import gzip
import json
import os
from pathlib import Path
import subprocess
import time
import unittest


@unittest.skipUnless(os.environ.get('ALLIANCE_SHARED_TERMINAL_MODE'),
                     'Requires isolated real engine and fresh disposable battle')
class SharedTerminalEngine(unittest.TestCase):
    def test_confirmed_native_captain_actions(self):
        mode = os.environ['ALLIANCE_SHARED_TERMINAL_MODE']
        self.assertIn(mode, ('companion-draw', 'draw-sequence'))
        root = Path(os.environ['ALLIANCE_SHARED_ENGINE_SCRATCH'])
        root.mkdir()
        fixture = Path(os.environ['ALLIANCE_SHARED_ENGINE_FIXTURE'])
        data = json.loads(fixture.read_text())
        self.assertEqual(data['purpose'], 'disposable-shared-engine-fixture')
        self.assertTrue(data['database'].startswith('alliance_test_shared_engine_'))
        self.assertEqual([p['side'] for p in data['players']], [1, 4, 2, 3])
        children = []
        records = []
        try:
            for index, actor in enumerate(data['players']):
                own = root / str(index)
                own.mkdir()
                (own / 'private').mkdir()
                (own / 'userdata').mkdir()
                profile = dict(data['profile'], port=15002)
                profile_path = own / 'private/alliance-public-profile.json'
                profile_path.write_text(json.dumps(profile))
                env = os.environ.copy()
                env.update(SDL_VIDEODRIVER='dummy', SDL_AUDIODRIVER='dummy',
                           WESNOTH_ALLIANCE_PROFILE=str(profile_path),
                           ALLIANCE_SHARED_FIXTURE_TERMINAL='yes',
                           XDG_CACHE_HOME=str(own / 'font-cache'))
                args = [os.environ['ALLIANCE_SHARED_GAMEDATA'], '--userdata-dir',
                        str(own / 'userdata'), '--test=test', '--multiplayer',
                        '--nogui', '--nosound', '--no-log-to-file', '--log-none=audio']
                binary = os.environ['ALLIANCE_SHARED_ENGINE_PROBE']
                captain = index in (0, 2)
                payload = None
                if mode == 'companion-draw':
                    env.update(ALLIANCE_SHARED_FIXTURE_COMPANION='yes',
                               WESNOTH_ALLIANCE_COMPANION_PROFILE=str(profile_path))
                    payload = dict(run_id=data['run_id'], account_id=actor['account_id'],
                                   token=actor['token'], intent='play')
                    if captain:
                        payload.update(captain_action='offer_draw', after_turn=1)
                    command = [binary, *args]
                else:
                    command = [binary, '--shared-seat', str(fixture), str(index),
                               'shared-fixture', *args]
                    if captain:
                        env['ALLIANCE_SHARED_FIXTURE_HUMAN'] = 'yes'
                        schedule = ("{[1]='offer_draw',[2]='withdraw_draw',[3]='offer_draw'}"
                                    if index == 0 else "{[2]='offer_draw'}")
                        plugin = own / 'captain-fixture.lua'
                        plugin.write_text(
                            "return function(events,context,info) local moved=false local sent={} local schedule="
                            + schedule + " while true do if info.name=='Game' and "
                            "info.can_move().can_move and info.current_side().current_side=="
                            + str(actor['side']) + " then local turn=info.current_turn().current_turn "
                            "if not moved and turn==1 and " + str(actor['side']) + "==1 then "
                            "moved=true; context.synced_command{{'move',{x='13,13',y='22,21'}}} "
                            "elseif not sent[turn] and schedule[turn] then sent[turn]=true; "
                            "local command={'custom_command',{name='alliance_captain_v1',"
                            "{'data',{action=schedule[turn]}}}} "
                            "if turn==1 and " + str(actor['side']) + "==1 then "
                            "context.synced_command{{'move',{x='13,13',y='21,22'}},command} "
                            "else context.synced_command{command} end else context.end_turn{} end "
                            "elseif info.name=='Dialog' then context.skip_dialog{} end "
                            "events,context,info=wesnoth.plugin.next_slice() end end")
                        command += ['--plugin', str(plugin)]
                log = (own / 'engine.log').open('w')
                process = subprocess.Popen(command, env=env, stdout=log,
                                           stderr=subprocess.STDOUT,
                                           stdin=subprocess.PIPE if payload else subprocess.DEVNULL)
                children.append((process, log, own, actor))
                if payload:
                    process.stdin.write((json.dumps(payload) + '\n').encode())
                    process.stdin.close()
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline and any(p.poll() is None for p, _, _, _ in children):
                time.sleep(.2)
            for process, log, own, actor in children:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                log.close()
                text = (own / 'engine.log').read_text()
                row = dict(side=actor['side'], exit=process.returncode,
                           started='SHARED ENGINE STARTED' in text,
                           finished='SHARED ENGINE FINISHED' in text,
                           acknowledged='SHARED RESULT UPLOAD ACKNOWLEDGED' in text,
                           companion='SHARED PRODUCTION COMPANION ACKNOWLEDGED' in text,
                           failed='SHARED ENGINE FAILED' in text or 'error scripting/lua:' in text)
                records.append(row)
            (root / 'result.json').write_text(json.dumps(records, indent=2))
            self.assertTrue(all(r['exit'] == 0 and r['started'] and r['finished']
                                and r['acknowledged'] and not r['failed'] for r in records), records)
            if mode == 'companion-draw':
                self.assertTrue(all(r['companion'] for r in records))
            for _, _, own, actor in children:
                journal = own / 'private/alliance-shared' / actor['account_id'] / (data['run_id'] + '.cfg')
                self.assertIn('status="acknowledged"', journal.read_text())
                self.assertIn('draw=yes', journal.read_text())
                replay = own / 'userdata/sync/saves' / ('Alliance-battle-' + data['run_id'] + '-' + actor['account_id'] + '.gz')
                replay_wml = gzip.decompress(replay.read_bytes())
                self.assertNotIn(actor['token'].encode(), replay_wml)
                self.assertIn(b'alliance_captain_v1', replay_wml)
            events = fixture.parent / 'battles' / data['run_id'] / 'events.jsonl'
            commands = [json.loads(line)['wml'] for line in events.read_text().splitlines()
                        if 'custom_command' in line]
            expected = (['offer_draw', 'offer_draw'] if mode == 'companion-draw'
                        else ['offer_draw', 'withdraw_draw', 'offer_draw', 'offer_draw'])
            self.assertEqual(len(commands), len(expected))
            expected_sides = [1, 2] if mode == 'companion-draw' else [1, 1, 2, 1]
            for command, action, side in zip(commands, expected, expected_sides):
                self.assertIn('name="alliance_captain_v1"', command)
                self.assertIn('action="' + action + '"', command)
                self.assertIn('from_side="' + str(side) + '"', command)
            if mode == 'draw-sequence':
                self.assertIn('[move]', commands[0])
                self.assertLess(commands[0].index('[move]'), commands[0].index('[custom_command]'))
            (root / 'terminal-validation.json').write_text(json.dumps(
                dict(status='passed', mode=mode, actual_captain_actions=expected,
                     native_end_and_acknowledged_replays=4,
                     production_companion_stdin=mode == 'companion-draw',
                     ordinary_move_and_captain_command_same_packet=mode == 'draw-sequence'), indent=2))
            print('PASS real native ' + mode + ': durable commands, all four END/replay acknowledgements', flush=True)
        finally:
            for process, log, _, _ in children:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                log.close()


if __name__ == '__main__':
    unittest.main()
