"""Run the production immutable plan/queue logic, plus pinned dispatch overlays."""
from pathlib import Path
import hashlib
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from hero_catalog_fixture import catalogue_source

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('practice_engine_overlay', ROOT / 'alliance_game/engine_overlay.py')
overlay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlay)


class PracticePlanTests(unittest.TestCase):
    def test_production_plan_and_one_shot_queue(self):
        compiler = shutil.which(os.environ.get('CXX', 'c++'))
        self.assertIsNotNone(compiler, 'A C++17 compiler is required; execute in the cached verification container.')
        with tempfile.TemporaryDirectory(prefix='wesnoth-practice-plan-') as temp:
            executable = Path(temp) / 'practice-plan'
            catalogue = Path(temp) / 'catalogue.cpp'
            includes = catalogue_source(ROOT, catalogue)
            build = subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror', '-O0',
                                    '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                                    '-I', str(ROOT), str(ROOT / 'tests/practice_plan_fixture.cpp'),
                                    str(ROOT / 'alliance_game/practice_plan.cpp'), str(catalogue), *includes, '-o', str(executable)],
                                   capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True,
                                    env=dict(os.environ, ASAN_OPTIONS='detect_leaks=1:abort_on_error=1'))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertRegex(result.stdout, r'PRACTICE PLAN CHECKS PASSED: [0-9]+')
            print(result.stdout.strip())

    def test_engine_dispatch_preserves_original_and_ordinary_cases(self):
        path = ROOT / 'engine/src/wesnoth.cpp'
        original = path.read_bytes()
        generated = overlay.generate_engine_overlay(original)
        self.assertEqual(hashlib.sha256(original).hexdigest(), overlay.ENGINE_SHA256)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(generated.count(b'case alliance_game::practice_title_result:'), 1)
        self.assertIn(b'case title_screen::MP_LOCAL:\n\t\t\tgame->play_multiplayer(game_launcher::mp_mode::LOCAL);', generated)
        self.assertIn(b'case title_screen::LOAD_GAME:', generated)
        self.assertIn(b'title_screen dlg{game};', generated)

    def test_launcher_uses_existing_load_exception_boundary(self):
        original = (ROOT / 'engine/src/game_launcher.cpp').read_bytes()
        generated = overlay.generate_launcher_overlay(original)
        section = generated.split(b'bool game_launcher::play_multiplayer(mp_mode mode)',1)[1].split(b'bool game_launcher::play_multiplayer_commandline',1)[0]
        self.assertLess(section.index(b'\ttry {'), section.index(b'alliance_game::run_pending_practice();'))
        self.assertLess(section.index(b'alliance_game::run_pending_practice();'), section.index(b'load_game_config_for_create(true)'))
        self.assertIn(b'if(mode == mp_mode::LOCAL && alliance_game::practice_pending())', section)
        self.assertIn(b'mp::start_local_game();', section)
        self.assertIn(b'catch(savegame::load_game_exception& e)', section)
        self.assertIn(b'load_data_ = std::move(e.data_);', section)

    def test_both_generators_reject_modified_or_already_overlaid_source(self):
        for name, generator in [('wesnoth.cpp',overlay.generate_engine_overlay),('game_launcher.cpp',overlay.generate_launcher_overlay)]:
            source = (ROOT / 'engine/src' / name).read_bytes()
            for bad in (source + b'\n', source.replace(b'\n',b'\r\n'), generator(source)):
                with self.subTest(source=name):
                    with self.assertRaises(ValueError): generator(bad)


if __name__ == '__main__':
    unittest.main()
