"""Real team view models and ordered local lineup controller, with GUI/artwork boundaries substituted."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest
from hero_catalog_fixture import catalogue_source
from test_battle_practice_ui import extract
ROOT=Path(__file__).resolve().parents[1]

class TeamViewTests(unittest.TestCase):
    def test_rosters_maps_history_and_explicit_ordered_lineup(self):
        compiler=shutil.which(os.environ.get('CXX','c++'))
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix='wesnoth-team-views-') as temporary:
            directory=Path(os.environ.get('ALLIANCE_TEAM_UI_AUDIT',temporary));directory.mkdir(parents=True,exist_ok=True)
            source=directory/'team_views_fixture.cpp';binary=directory/'team_views_fixture'
            body=(ROOT/'tests/team_views_fixture.cpp.in').read_text().replace('// @LINEUP@',extract((ROOT/'alliance_game/account_ui.cpp').read_text(),'bool choose_battle_lineup('))
            source.write_text(body)
            catalogue=directory/'catalogue.cpp';includes=catalogue_source(ROOT,catalogue)
            command=[compiler,'-std=c++17','-Wall','-Wextra','-Werror','-O0','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer','-I'+str(ROOT),str(source),str(ROOT/'alliance_game/team_views.cpp'),str(catalogue),*includes,'-o',str(binary)]
            built=subprocess.run(command,capture_output=True,text=True,timeout=180)
            self.assertEqual(built.returncode,0,built.stdout+built.stderr)
            ran=subprocess.run([str(binary)],capture_output=True,text=True,timeout=30)
            self.assertEqual(ran.returncode,0,ran.stdout+ran.stderr)
            (directory/'behavior.log').write_text(ran.stdout+ran.stderr)
            print(ran.stdout,end='')
            self.assertIn('PASS team view models and ordered lineup',ran.stdout)
if __name__=='__main__':unittest.main()
