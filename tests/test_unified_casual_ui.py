"""Execute production ordinary multiplayer selection/cache controllers with scripted boundaries."""
from pathlib import Path
import os, shutil, subprocess, tempfile, unittest
ROOT=Path(__file__).resolve().parents[1]
class CasualUi(unittest.TestCase):
    def test_actual_selection_readiness_identity_resume_and_start(self):
        source=(ROOT/'alliance_game/casual_sides.cpp').read_text()
        source='\n'.join(line for line in source.splitlines() if not line.startswith('#include "'))
        fixture=(ROOT/'tests/unified_casual_ui_fixture.cpp.in').read_text().replace('// @PRODUCTION@',source)
        compiler=shutil.which(os.environ.get('CXX','c++'));self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix='unified-casual-ui-') as folder:
            directory=Path(folder);cpp=directory/'fixture.cpp';binary=directory/'fixture';cpp.write_text(fixture)
            command=[compiler,'-std=c++17','-Wall','-Wextra','-Werror','-O0','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer','-I'+str(ROOT),str(cpp),'-o',str(binary)]
            built=subprocess.run(command,capture_output=True,text=True,timeout=120)
            self.assertEqual(built.returncode,0,built.stdout+built.stderr)
            ran=subprocess.run([str(binary)],capture_output=True,text=True,timeout=30)
            self.assertEqual(ran.returncode,0,ran.stdout+ran.stderr)
            self.assertIn('PASS all unified casual UI cases',ran.stdout)
            print(ran.stdout,end='')
if __name__=='__main__':unittest.main()
