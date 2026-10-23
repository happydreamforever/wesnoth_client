"""Real private-pipe helper execution with substituted Android/engine boundaries."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('individual_helper', ROOT / 'tools/run_android_individual_companion.py')
I = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(I)


def review(action='join', revision=4):
    return {'action': action, 'room_id': 7, 'room_revision': revision, 'name': 'Global room', 'map': 'The Freelands',
        'members': 1 if action == 'join' else 2, 'capacity': 2, 'gold': '100', 'turns': '20', 'side': 0 if action == 'join' else 2,
        'era': 'era_default', 'fog': True, 'shroud': False, 'observers': False, 'timer': False,
        'mp_countdown_init_time': 300, 'mp_countdown_reservoir_time': 300, 'mp_countdown_turn_bonus': 30,
        'mp_countdown_action_bonus': 0, 'sides': [dict(side=n, team=str(n), player='Player' if n == 1 else '', gold=100,
        income=2, village_gold=2, village_support=1, fog=True, shroud=False) for n in (1, 2)]}


class IndividualCompanion(unittest.TestCase):
    def test_review_includes_current_settings_and_separate_join_ready(self):
        for action in ('join', 'ready'):
            self.assertEqual(I.reviewed_message(json.dumps(review(action)), 7), review(action))

    def test_malformed_stale_wrong_room_or_unbounded_review_refused(self):
        changes = [{'room_id': 8}, {'room_id': True}, {'room_revision': 0}, {'room_revision': True}, {'action': 'start'},
            {'capacity': 9}, {'members': 3}, {'side': 2}, {'name': 'Injected\nline'}, {'map': 'x' * 257},
            {'timer': 'yes'}, {'mp_countdown_init_time': -1}, {'sides': []}, {'secret': 'never accepted'}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(I.C.CompanionError):
                I.reviewed_message(json.dumps(review() | change), 7)
        for change in ({'side': 0}, {'room_revision': 2147483648}):
            with self.assertRaises(I.C.CompanionError):
                I.reviewed_message(json.dumps(review('ready') | change), 7)

    def test_side_alliances_settings_are_bounded_and_canonical(self):
        for change in ({'side': 2}, {'gold': 1001}, {'income': True}, {'team': 'bad\n'}, {'shroud': 'yes'}, {'player': 'x' * 25}):
            value = review(); value['sides'][0].update(change)
            with self.subTest(change=change), self.assertRaises(I.C.CompanionError):
                I.reviewed_message(json.dumps(value), 7)

    def test_owned_hero_form_selection_requires_a_real_owned_choice(self):
        account = {'heroes': [{'hero_id': 'konrad', 'name': 'Konrad', 'hero_level': 2, 'forms': ['fighter', 'lord']}]}
        with mock.patch.object(I.C, 'file_document', return_value={'forms': [{'id': 'fighter', 'name': 'Fighter'}, {'id': 'lord', 'name': 'Lord'}]}), \
                mock.patch('builtins.input', return_value='2'), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(I.choose_hero(account), ('konrad', 'lord'))
        self.assertIn('permanent level 2', output.getvalue())
        with mock.patch.object(I.C, 'file_document', return_value={'forms': []}), contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(I.C.CompanionError, 'no_owned_heroes'):
                I.choose_hero({'heroes': []})
        self.assertIn('Campaign', output.getvalue())

    def test_hero_cancel_never_invents_selection(self):
        account = {'heroes': [{'hero_id': 'konrad', 'name': 'Konrad', 'hero_level': 1, 'forms': ['fighter']}]}
        for answer in ('', '-1', '0', '2', '١', 'fighter'):
            with mock.patch.object(I.C, 'file_document', return_value={'forms': [{'id': 'fighter', 'name': 'Fighter'}]}), \
                    mock.patch('builtins.input', return_value=answer), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(I.C.CompanionError):
                I.choose_hero(account)

    def test_launcher_separates_individual_storage_and_env_without_secret_arguments(self):
        hashes = {name: 'a' * 64 for name in ('wesnoth-engine-cli', *I.C.LIBRARIES)}
        session, script = I.launch_script('/data/user/0/org.wesnoth.offline', '/data/local/tmp/public',
            '11111111-1111-4111-8111-111111111111', 7, 'b' * 32, '/sdcard/game', hashes, 'c' * 64)
        self.assertIn('/individual-companions/', session)
        self.assertIn('export WESNOTH_INDIVIDUAL_COMPANION_PROFILE=', script)
        self.assertNotIn('export WESNOTH_ALLIANCE_COMPANION_PROFILE=', script)
        self.assertNotIn('/alliance-companions', script)
        self.assertIn('room-7', session)
        self.assertIn('exec 3<&0', script)
        self.assertIn('kill -TERM "$child"', script)
        subprocess.run(['bash', '-n'], input=script, text=True, check=True)

    def run_pipe(self, reviews=None, answers=None, raw_noise=True):
        reviews = reviews or [review(), review('ready', 5)]
        answers = answers or ['JOIN Player', 'READY Player']
        temp = tempfile.TemporaryDirectory(prefix='individual-pipe-'); self.addCleanup(temp.cleanup)
        child = Path(temp.name) / 'child.py'
        child.write_text('''import json,sys
admission=json.loads(sys.stdin.readline())
assert admission['token']=='t'*43
assert not any('tttt' in arg or 'password' in arg for arg in sys.argv)
print('RAW ENGINE CONTENT MUST REMAIN PRIVATE',flush=True)
reviews=json.loads(REVIEW_DATA)
for index,value in enumerate(reviews):
 print(PREFIX+json.dumps(value),flush=True)
 answer=json.loads(sys.stdin.readline())
 assert answer=={'action':value['action'],'revision':value['room_revision']}
 if index==0:print(JOINING,flush=True)
print(STARTED,flush=True)
print(FINISHED,flush=True)
'''.replace('REVIEW_DATA', repr(json.dumps(reviews))).replace('PREFIX', repr(I.REVIEW)).replace('JOINING', repr(I.JOINING)).replace('STARTED', repr(I.STARTED)).replace('FINISHED', repr(I.FINISHED)))
        device = mock.Mock(base=[sys.executable, '-u', str(child)])
        admission = {'token': 't' * 43, 'room_id': 7}
        output = io.StringIO()
        with mock.patch('builtins.input', side_effect=answers), mock.patch.object(I.C, 'stop_owned') as stopped, contextlib.redirect_stdout(output):
            result = I.run_native(device, 'public launcher only', admission, '/private/owned-session', 'Player')
        self.assertNotIn('RAW ENGINE CONTENT', output.getvalue())
        self.assertNotIn('t' * 43, output.getvalue())
        stopped.assert_not_called()
        return result, output.getvalue()

    def test_real_pipe_join_then_ready_and_complete(self):
        result, output = self.run_pipe()
        self.assertEqual(result, {'result': 'completed', 'room_reviews': 2})
        self.assertIn('Side 2:', output)
        self.assertIn('village gold/support', output)
        self.assertIn(I.FINISHED, output)

    def test_changed_room_requires_another_explicit_ready(self):
        result, output = self.run_pipe([review(), review('ready', 5), review('ready', 8)],
            ['JOIN Player', 'READY Player', 'READY Player'])
        self.assertEqual(result['room_reviews'], 3)
        self.assertIn('revision 8', output)

    def test_cancel_stops_only_the_owned_native_process(self):
        with mock.patch.object(I.C, 'stop_owned') as stopped:
            with self.assertRaisesRegex(I.C.CompanionError, 'room_confirmation_cancelled'):
                # Independent child still uses a private stdin, and receives no
                # Ready command after the rejected Join review.
                temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
                child = Path(temp.name) / 'wait.py'
                child.write_text('import sys,time\nsys.stdin.readline()\nprint(' + repr(I.REVIEW + json.dumps(review())) + ',flush=True)\ntime.sleep(30)\n')
                device = mock.Mock(base=[sys.executable, '-u', str(child)])
                with mock.patch('builtins.input', return_value=''), contextlib.redirect_stdout(io.StringIO()):
                    I.run_native(device, 'public', {'token': 't' * 43, 'room_id': 7}, '/private/owned', 'Player')
            stopped.assert_called_once_with(device, '/private/owned')

    def test_failure_logs_only_fixed_codes_and_no_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            child = Path(folder) / 'fail.py'; child.write_text("import sys\nsys.stdin.readline()\nprint('PRIVATE TOKEN password uri')\nsys.exit(1)\n")
            with contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaisesRegex(I.C.CompanionError, '^individual_companion_stopped$'):
                I.run_native(mock.Mock(base=[sys.executable, '-u', str(child)]), 'public', {'token': 't' * 43, 'room_id': 7}, '/private/owned', 'Player')
            self.assertEqual(output.getvalue(), '')


if __name__ == '__main__':
    unittest.main()
