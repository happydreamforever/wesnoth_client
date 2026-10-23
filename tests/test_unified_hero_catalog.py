"""Consistency checks for the reviewed campaign roster and native export."""
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HeroCatalogueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = (ROOT / 'alliance_game/hero_catalog.json').read_bytes()
        cls.catalog = json.loads(cls.raw)
        cls.forms = {v['id']: v for v in cls.catalog['forms']}
        cls.heroes = {v['id']: v for v in cls.catalog['heroes']}

    def test_reviewed_roster_and_distinct_konrads(self):
        self.assertEqual(len(self.heroes), 117)
        self.assertEqual(len(self.forms), 346)
        self.assertEqual(len(self.catalog['campaigns']), 21)
        self.assertIn('konrad', self.heroes)
        self.assertIn('konrad_ii', self.heroes)
        self.assertNotEqual(self.heroes['konrad'], self.heroes['konrad_ii'])

    def test_every_alias_uses_its_canonical_hero(self):
        covered = set()
        for campaign in self.catalog['campaigns']:
            self.assertTrue(campaign['scenario_ids'])
            self.assertEqual(len(campaign['scenario_ids']), len(set(campaign['scenario_ids'])))
            for alias in campaign['aliases']:
                self.assertIn(alias['hero_id'], self.heroes)
                self.assertTrue(alias['forms'])
                for form in alias['forms']:
                    self.assertEqual(self.forms[form['form_id']]['hero_id'], alias['hero_id'])
                    covered.add(form['form_id'])
        self.assertEqual(covered, set(self.forms))

    def test_quest_variants_map_to_intrinsic_forms(self):
        for form in self.forms.values():
            self.assertNotIn('sceptre', form['source_variation'])
            self.assertNotIn('bloodaxe', form['source_variation'])
            self.assertEqual(form['unit_type'], 'AH_' + form['id'])
            self.assertGreater(form['base_hitpoints'], 0)
            self.assertIn(form['native_level'], range(6))
            self.assertNotIn('^', form['name'])

    def test_embedded_and_server_catalogue_match(self):
        embedded = (ROOT / 'alliance_game/hero_catalog_data.inc').read_text()
        self.assertIn(self.raw.decode(), embedded)
        self.assertIn(hashlib.sha256(self.raw).hexdigest(), embedded)
        server = ROOT.parent / 'wesnoth_server/alliance/hero_catalog.json'
        if server.is_file():
            self.assertEqual(server.read_bytes(), self.raw)
        units = (ROOT / 'alliance_game/hero_units.cfg').read_text()
        self.assertIn(units, (ROOT / 'alliance_game/hero_unit_data.inc').read_text())


if __name__ == '__main__':
    unittest.main()
