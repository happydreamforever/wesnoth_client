"""Shared theme generation, stale-output refusal and actual Java constant checks."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('theme_tests', ROOT / 'tools/generate_product_theme.py')
T = importlib.util.module_from_spec(spec); spec.loader.exec_module(T)


class ThemeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='public-theme-')
        self.addCleanup(self.temporary.cleanup); self.repo = Path(self.temporary.name)
        for name in (T.SOURCE, *T.OUTPUTS):
            target = self.repo / name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)

    def test_checked_in_native_and_java_match_one_descriptor(self):
        T.verify_generated(ROOT)
        theme = T.load_theme(ROOT)
        self.assertEqual(theme['background'], 'alliance-ui/background/citadel-dawn.png')
        self.assertEqual(theme['font']['source'], 'fonts/WesScript-Regular.otf')
        self.assertEqual(theme['body_font']['source'], 'fonts/Lato-Regular.ttf')

    def test_either_language_drift_is_refused_without_rewriting(self):
        for name in T.OUTPUTS:
            with self.subTest(name=name):
                original = (self.repo / name).read_bytes()
                changed = original.replace(b'D6B46E', b'FFFFFF')
                self.assertNotEqual(original, changed); (self.repo / name).write_bytes(changed)
                with self.assertRaisesRegex(ValueError, 'Generated interface theme differs'):
                    T.verify_generated(self.repo)
                self.assertEqual((self.repo / name).read_bytes(), changed)
                (self.repo / name).write_bytes(original)

    def test_palette_change_requires_both_outputs_and_changes_descriptor_identity(self):
        before = T.generated(self.repo)
        path = self.repo / T.SOURCE; value = json.loads(path.read_text())
        value['palette']['gold'] = '#C7A25B'; path.write_text(json.dumps(value))
        with self.assertRaises(ValueError): T.verify_generated(self.repo)
        after = T.generated(self.repo)
        for name in T.OUTPUTS:
            self.assertNotEqual(before[name], after[name]); (self.repo / name).write_bytes(after[name])
        T.verify_generated(self.repo)

    def test_invalid_or_ambiguous_theme_input_is_rejected(self):
        path = self.repo / T.SOURCE; original = path.read_text()
        cases = []
        for key, value in (('format', True), ('background', 'alliance-ui/../../private.png'), ('title_name', 'WESNOTH\nprivate')):
            changed = json.loads(original); changed[key] = value; cases.append(json.dumps(changed))
        changed = json.loads(original); changed['palette']['gold'] = '#GGGGGG'; cases.append(json.dumps(changed))
        changed = json.loads(original); changed['font']['source'] = '../secret'; cases.append(json.dumps(changed))
        cases.append(original.replace('"format": 1,', '"format": 1, "format": 1,'))
        for value in cases:
            with self.subTest(value=value[:45]):
                path.write_text(value)
                with self.assertRaises(ValueError): T.generated(self.repo)

    def test_actual_java_uses_exact_shared_colors_font_and_background(self):
        source = self.repo / 'java/org/wesnoth/offline'; source.mkdir(parents=True)
        shutil.copyfile(ROOT / T.OUTPUTS[1], source / 'ProductTheme.java')
        (source / 'ThemeHarness.java').write_text('''package org.wesnoth.offline;
public final class ThemeHarness { public static void main(String[] ignored) {
 if(ProductTheme.GOLD!=0xFFD6B46E || ProductTheme.IVORY!=0xFFF3E7CD || ProductTheme.STONE!=0xFF514737)
  throw new AssertionError("shared palette");
 if(!ProductTheme.BACKGROUND_ASSET.equals("alliance-ui/background/citadel-dawn.png") ||
    !ProductTheme.TITLE_FONT_ASSET.equals("startup/WesScript-Regular.otf") ||
    !ProductTheme.BODY_FONT_ASSET.equals("startup/Lato-Regular.ttf") ||
    !ProductTheme.TITLE_KICKER.equals("THE BATTLE FOR") || !ProductTheme.TITLE_NAME.equals("WESNOTH"))
  throw new AssertionError("shared theme assets");
 System.out.println("SHARED ANDROID THEME PASS"); }}''')
        subprocess.run(['javac', '--release', '8', '-d', str(self.repo / 'classes'), *map(str, source.glob('*.java'))],
                       check=True, capture_output=True, timeout=30)
        result = subprocess.run(['java', '-cp', str(self.repo / 'classes'), 'org.wesnoth.offline.ThemeHarness'],
                                check=True, capture_output=True, text=True, timeout=30)
        self.assertIn('SHARED ANDROID THEME PASS', result.stdout)


if __name__ == '__main__': unittest.main()
