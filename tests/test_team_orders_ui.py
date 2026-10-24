"""Current captain orders: execute production controllers and strict JSON validation with scripted UI/network boundaries."""
from pathlib import Path
import os,shutil,subprocess,tempfile,unittest
from test_battle_practice_ui import extract
ROOT=Path(__file__).resolve().parents[1]
class TeamOrdersUi(unittest.TestCase):
    def test_current_captain_orders_consent_and_acknowledgement(self):
        compiler=shutil.which(os.environ.get('CXX','c++'));self.assertIsNotNone(compiler)
        ui=(ROOT/'alliance_game/account_ui.cpp').read_text();extension=(ROOT/'alliance_game/progression_ui.inc').read_text();team=(ROOT/'alliance_game/team_extension_ui.inc').read_text()
        functions='\n'.join([extract(ui,'int select('),extract(ui,'int select_change('),extract(extension,'bool same_extension_actor('),extract(extension,'bool extension_request('),extract(team,'void captain_orders(')])
        with tempfile.TemporaryDirectory(prefix='team-orders-ui-') as temp:
            out=Path(os.environ.get('ALLIANCE_TEAM_ORDERS_AUDIT',temp));out.mkdir(parents=True,exist_ok=True)
            cpp=out/'team_orders.cpp';binary=out/'team_orders'
            cpp.write_text((ROOT/'tests/team_orders_ui_fixture.cpp.in').read_text().replace('// @FUNCTIONS@',functions))
            command=[compiler,'-std=c++17','-Wall','-Wextra','-Werror','-O0','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer','-I'+str(ROOT),'-I'+str(ROOT/'engine/src'),str(cpp),str(ROOT/'alliance_native/account_protocol.cpp'),'-lz','-o',str(binary)]
            built=subprocess.run(command,capture_output=True,text=True,timeout=180);(out/'compile.log').write_text(built.stdout+built.stderr);self.assertEqual(built.returncode,0,built.stdout+built.stderr)
            ran=subprocess.run([str(binary)],capture_output=True,text=True,timeout=30);(out/'behavior.log').write_text(ran.stdout+ran.stderr);self.assertEqual(ran.returncode,0,ran.stdout+ran.stderr);print(ran.stdout,end='');self.assertIn('PASS all current captain order cases',ran.stdout)
if __name__=='__main__':unittest.main()
