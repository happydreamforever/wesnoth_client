"""Real private-pipe helper execution with substituted Android/engine boundaries."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
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

    def run_pipe(self, reviews=None, answers=None, raw_noise=True, selector=None):
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
        admission = {'token': 't' * 43, **({'room_id': 7} if selector is None else selector)}
        output = io.StringIO()
        with mock.patch('builtins.input', side_effect=answers), mock.patch.object(I.C, 'stop_owned') as stopped, contextlib.redirect_stdout(output):
            result = I.run_native(device, 'public launcher only', admission, '/private/owned-session', 'Player')
        self.assertNotIn('RAW ENGINE CONTENT', output.getvalue())
        self.assertNotIn('t' * 43, output.getvalue())
        stopped.assert_not_called()
        return result, output.getvalue()

    def test_real_pipe_join_then_ready_and_complete(self):
        result, output = self.run_pipe()
        self.assertEqual(result, {'result': 'completed', 'room_reviews': 2, 'room_id': 7})
        self.assertIn('Side 2:', output)
        self.assertIn('village gold/support', output)
        self.assertIn(I.FINISHED, output)

    def test_changed_room_requires_another_explicit_ready(self):
        result, output = self.run_pipe([review(), review('ready', 5), review('ready', 8)],
            ['JOIN Player', 'READY Player', 'READY Player'])
        self.assertEqual(result['room_reviews'], 3)
        self.assertIn('revision 8', output)

    def test_name_discovery_binds_id_before_ready_and_reports_resolved_id(self):
        # A rename after joining does not change the selected room identity.
        result, _ = self.run_pipe([review(), review('ready', 5) | {'name': 'Renamed room'}],
            selector={'room_name': 'Global room'})
        self.assertEqual(result['room_id'], 7)
        with self.assertRaisesRegex(I.C.CompanionError, 'invalid_room_review'):
            self.run_pipe(selector={'room_name': 'global room'})
        with self.assertRaisesRegex(I.C.CompanionError, 'invalid_room_review'):
            self.run_pipe([review(), review('ready', 5) | {'room_id': 8}], selector={'room_name': 'Global room'})

    def test_name_discovery_rejects_unbound_ready_or_invalid_id(self):
        for value in (review('ready'), review() | {'room_id': 0}, review() | {'room_id': 2147483648}):
            with self.assertRaisesRegex(I.C.CompanionError, 'invalid_room_review'):
                I.reviewed_message(json.dumps(value), None, 'Global room')

    def test_name_discovery_private_paths_use_fresh_id_not_untrusted_name(self):
        hashes = {name: 'a' * 64 for name in ('wesnoth-engine-cli', *I.C.LIBRARIES)}
        session, script = I.launch_script('/data/user/0/org.wesnoth.offline', '/data/local/tmp/public',
            '11111111-1111-4111-8111-111111111111', None, 'b' * 32, '/sdcard/game', hashes, 'c' * 64)
        self.assertIn('/room-discovery-' + 'b' * 32 + '/', session)
        subprocess.run(['bash', '-n'], input=script, text=True, check=True)

    def test_cli_requires_exactly_one_room_selector(self):
        common = ['--username', 'Player', '--adb', 'adb', '--sdk', 'sdk', '--java', 'java']
        for selection in ([], ['--room-id', '7', '--room-name', 'Room']):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failure:
                I.main(common + selection)
            self.assertEqual(failure.exception.code, 2)

    def test_powershell_name_and_legacy_id_are_exclusive_and_preserve_literal_name(self):
        pwsh = ROOT / 'build/host-tools/powershell-7.4.6/pwsh'
        self.assertTrue(pwsh.is_file())
        with tempfile.TemporaryDirectory(prefix='individual PS & ') as tmp:
            base = Path(tmp); tools = base / 'tools'; tools.mkdir()
            helper = tools / 'Run-AndroidIndividualCompanion.ps1'
            shutil.copyfile(ROOT / 'tools/Run-AndroidIndividualCompanion.ps1', helper)
            (tools / 'run_android_individual_companion.py').write_text('fixture')
            python = base / 'Python & tool/python.exe'; python.parent.mkdir()
            python.write_text('#!/usr/bin/python3\nimport json,os,sys\nopen(os.environ["CALLS"],"w").write(json.dumps(sys.argv[1:]))\nsys.exit(2)\n'); python.chmod(0o755)
            adb = base / 'SDK & tool/adb.exe'; adb.parent.mkdir(); adb.write_text('fixture')
            java = base / 'Java & tool/bin/java.exe'; java.parent.mkdir(parents=True); java.write_text('fixture')
            quote = lambda value: "'" + str(value).replace("'", "''") + "'"
            prefix = "$env:OS='Windows_NT'; & " + quote(helper) + ' -Username Player -PythonPath ' + quote(python) + ' -AdbPath ' + quote(adb) + ' -SdkRoot ' + quote(adb.parent) + ' -JavaHome ' + quote(java.parent.parent)
            calls = base / 'calls.json'; env = dict(os.environ, CALLS=str(calls), LOCALAPPDATA=str(base))
            name = "1192621 acceptance & literal $(no-command) ' exact"
            for selection, expected in ((' -RoomName ' + quote(name), ['--room-name', name]), (' -RoomId 7', ['--room-id', '7'])):
                result = subprocess.run([str(pwsh), '-NoProfile', '-NonInteractive', '-Command', prefix + selection + '; exit $LASTEXITCODE'], capture_output=True, env=env, timeout=20)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                args = json.loads(calls.read_text()); self.assertEqual(args[-2:], expected); calls.unlink()
            for selection in ('', " -RoomId 7 -RoomName 'Room'", ' -RoomId 0', " -RoomName ''"):
                result = subprocess.run([str(pwsh), '-NoProfile', '-NonInteractive', '-Command', prefix + selection + '; exit $LASTEXITCODE'], capture_output=True, env=env, timeout=20)
                self.assertFalse(calls.exists(), result.stdout + result.stderr)

    def test_production_native_selection_rejects_ambiguity_without_fallback(self):
        source = (ROOT / 'alliance_game/individual_companion.cpp').read_text()
        start = source.index('int select_room('); end = source.index('\n}\n}', start) + 2
        function = source[start:end]
        fixture = r'''
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>
#include <cassert>
struct Value { std::string value; std::string str()const{return value;} long long to_long_long()const{try{return std::stoll(value);}catch(...){return 0;}} };
struct config {
 std::map<std::string,Value> fields; std::vector<config> games;
 Value operator[](const std::string& key)const{auto i=fields.find(key);return i==fields.end()?Value{}:i->second;}
 const std::vector<config>& child_range(const std::string& tag)const{assert(tag=="game");return games;}
};
void require(bool value){if(!value)throw std::runtime_error("refused");}
// @FUNCTION@
config room(std::string id,std::string name){return {{{"id",{id}},{"name",{name}}},{}};}
void refused(const config& list,std::optional<int> id,std::string name){try{select_room(list,id,name);assert(false);}catch(const std::runtime_error&){}}
int main(){
 config list{{},{room("7","Exact room"),room("8","Other")}};
 assert(select_room(list,{},"Exact room")==7);
 assert(select_room(list,8,{})==8);
 refused(list,{},"exact room");refused(list,{},"Exact room ");refused(list,{},"missing");
 refused(list,{},{});refused(list,7,"Exact room");refused(list,9,{});
 list.games.push_back(room("9","Exact room"));refused(list,{},"Exact room");
 list.games.back()=room("7","Other duplicate ID");refused(list,7,{});
 for(const auto& bad:{"0","-1","2147483648","07","7x","","abc"}){
  config malformed{{},{room(bad,"Exact room")}};refused(malformed,{},"Exact room");
 }
 config unicode{{},{room("2147483647",u8"部屋")}};assert(select_room(unicode,{},u8"部屋")==2147483647);
}
'''.replace('// @FUNCTION@', function)
        with tempfile.TemporaryDirectory(prefix='individual-selector-') as tmp:
            source_path = Path(tmp) / 'selector.cpp'; binary = Path(tmp) / 'selector'; source_path.write_text(fixture)
            subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined', str(source_path), '-o', str(binary)], check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True, timeout=20)

    def test_generated_and_companion_greetings_require_current_capabilities(self):
        spec = importlib.util.spec_from_file_location('companion_auth_overlay', ROOT / 'alliance_game/ordinary_mp_auth_overlay.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        generated = module.generate_multiplayer_auth_overlay((ROOT / 'engine/src/game_initialization/multiplayer.cpp').read_bytes()).decode()
        opening = generated.index('            const auto& advertised=data.mandatory_child("version");')
        closing = generated.index('            alliance_greeting_checked=true;', opening) + len('            alliance_greeting_checked=true;')
        greeting = generated[opening:closing]
        ticket_guard = generated[generated.index('        if(!alliance_greeting_checked)'):generated.index('        if(admission->ticket.empty())')]
        native = (ROOT / 'alliance_game/individual_companion.cpp').read_text()
        start = native.index('        require(hello["alliance_protocol"]')
        native_greeting = native[start:native.index('\n', start)]
        fixture = r'''
#include <map>
#include <stdexcept>
#include <string>
#include <cassert>
using wesnothd_error=std::runtime_error;
struct Value {std::string value;std::string str()const{return value;}};
struct config {
 std::map<std::string,Value> fields;
 Value operator[](const std::string& key)const{auto i=fields.find(key);return i==fields.end()?Value{}:i->second;}
 const config& mandatory_child(const std::string& tag)const{assert(tag=="version");return *this;}
};
void require(bool value){if(!value)throw std::runtime_error("refused");}
void companion(const config& hello){/* @NATIVE@ */}
void ordinary(const config& data,bool received_version){
 bool alliance_greeting_checked=false;
 if(received_version){/* @GREETING@ */}
 /* @GUARD@ */
}
int main(){
 config current{{{"alliance_protocol",{"3"}},{"implementation_release",{"1192621"}},{"individual_rooms",{"1"}}}};
 companion(current);ordinary(current,true);
 try{ordinary(current,false);assert(false);}catch(const std::runtime_error&){}
 for(const auto& key:{"alliance_protocol","implementation_release","individual_rooms"}) {
  for(const auto& bad:{"","0","2","1192617","01","3junk"}){
   auto changed=current;changed.fields[key]={bad};
   try{companion(changed);assert(false);}catch(const std::runtime_error&){}
   try{ordinary(changed,true);assert(false);}catch(const std::runtime_error&){}
  }
  auto missing=current;missing.fields.erase(key);
  try{companion(missing);assert(false);}catch(const std::runtime_error&){}
  try{ordinary(missing,true);assert(false);}catch(const std::runtime_error&){}
 }
}
'''.replace('/* @NATIVE@ */', native_greeting).replace('/* @GREETING@ */', greeting).replace('/* @GUARD@ */', ticket_guard)
        with tempfile.TemporaryDirectory(prefix='individual-greeting-') as tmp:
            source_path = Path(tmp) / 'greeting.cpp'; binary = Path(tmp) / 'greeting'; source_path.write_text(fixture)
            subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined', str(source_path), '-o', str(binary)], check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True, timeout=20)

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
