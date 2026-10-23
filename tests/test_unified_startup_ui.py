"""Execute the production startup/signup/login controllers with scripted UI and service boundaries."""
from pathlib import Path
import os, shutil, subprocess, tempfile, unittest
ROOT=Path(__file__).resolve().parents[1]
def extract(source,signature):
    start=source.index(signature);opening=source.index('\n{',start)+1;end=opening+1;depth=1
    while depth:
        depth+=(source[end]=='{')-(source[end]=='}');end+=1
    return source[start:end]
class UnifiedStartup(unittest.TestCase):
    def test_actual_gate_signup_session_and_generation(self):
        source=(ROOT/'alliance_game/account_ui.cpp').read_text()
        names=['std::string profile_text(', 'bool reload_player(', 'bool choose_hero(', 'void register_player(', 'void login(', 'std::optional<PlayerSession> player_session(', 'bool ensure_player_session(']
        fixture=(ROOT/'tests/unified_startup_ui_fixture.cpp.in').read_text().replace('// @FUNCTIONS@','\n\n'.join(extract(source,n) for n in names))
        compiler=shutil.which(os.environ.get('CXX','c++'));self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix='unified-startup-') as folder:
            root=Path(folder);cpp=root/'ui.cpp';binary=root/'ui';cpp.write_text(fixture)
            built=subprocess.run([compiler,'-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-g','-I'+str(ROOT),str(cpp),'-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(built.returncode,0,built.stdout+built.stderr)
            ran=subprocess.run([str(binary)],capture_output=True,text=True,timeout=20)
            self.assertEqual(ran.returncode,0,ran.stdout+ran.stderr)
            self.assertIn('PASS all unified startup UI cases',ran.stdout)
            print(ran.stdout,end='')
if __name__=='__main__':unittest.main()
