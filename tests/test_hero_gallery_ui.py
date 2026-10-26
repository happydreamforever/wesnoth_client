"""Execute actual hero-gallery controllers and scaling with scripted UI/account boundaries.

Plain text models are inspected here; real GUI2 rendering is verified separately.
No sockets, database mutations, Android or human input are executed by this fixture.
"""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def extract(source, signature):
    start = source.index(signature)
    opening = source.index('\n{', start) + 1 if '\n{' in source[start:] else source.index('{', start)
    end, depth = opening + 1, 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


CASES = ('catalogue','campaign','stats','empty','back','explicit','locked','invalid',
         'initial_failure','final_failure','modal_race','identity_race','revision_race','form_race','hero_race')


class HeroGallery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='hero-gallery-ui-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.audit = Path(os.environ.get('HERO_GALLERY_AUDIT', cls.temp.name))
        cls.audit.mkdir(parents=True, exist_ok=True)
        source = (ROOT / 'alliance_game/account_ui.cpp').read_text()
        names = ('bool reload_player(', 'BattleRow hero_form_card(',
                 'std::vector<HeroGalleryEntry> gallery_entries(', 'bool choose_hero(', 'void heroes_dialog(')
        actual = '\n\n'.join(extract(source, name) for name in names)
        actual += '\n' + extract((ROOT / 'alliance_game/hero_catalog.cpp').read_text(), 'int scaled_hero_stat(')
        fixture = (ROOT / 'tests/hero_gallery_ui_fixture.cpp.in').read_text().replace('// @FUNCTIONS@', actual)
        cpp = cls.audit / 'gallery.cpp'; cpp.write_text(fixture)
        cls.binary = cls.audit / 'gallery'
        compiler = shutil.which(os.environ.get('CXX', 'c++'))
        if compiler is None: raise RuntimeError('A C++ compiler is required; no skipped acceptance')
        command = [compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                   '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-g',
                   '-I' + str(ROOT), str(cpp), '-o', str(cls.binary)]
        built = subprocess.run(command, capture_output=True, text=True, timeout=180)
        (cls.audit / 'compile.log').write_text(built.stdout + built.stderr)
        if built.returncode: raise RuntimeError(built.stdout + built.stderr)
        cls.observations = []
        cls.sources = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
            'alliance_game/account_ui.cpp', 'alliance_game/hero_catalog.cpp', 'alliance_game/hero_catalog.hpp',
            'alliance_game/battle_ui.hpp', 'tests/test_hero_gallery_ui.py', 'tests/hero_gallery_ui_fixture.cpp.in')}

    @classmethod
    def tearDownClass(cls):
        (cls.audit / 'validation.json').write_text(json.dumps({
            'cases': cls.observations, 'sources': cls.sources, 'scope': __doc__}, indent=2) + '\n')

    def run_case(self, case):
        ran = subprocess.run([str(self.binary), case], capture_output=True, text=True, timeout=15)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertEqual(ran.stdout, 'PASS hero gallery ' + case + '\n')
        self.assertEqual(ran.stderr, '')
        self.observations.append({'case': case, 'passed': True})


for case in CASES:
    setattr(HeroGallery, 'test_' + case, lambda self, name=case: self.run_case(name))
