"""Presentation coverage and pinned-art identity; no gameplay data generation."""
import hashlib
import json
from pathlib import Path
import struct
import unittest

ROOT=Path(__file__).resolve().parents[1]
class HeroPresentationTests(unittest.TestCase):
    def setUp(self):
        self.catalog=json.loads((ROOT/'alliance_game/hero_catalog.json').read_text())
        self.meta=json.loads((ROOT/'alliance_game/hero_presentation.json').read_text())
    def test_exact_canonical_cast_and_sources(self):
        self.assertEqual(self.meta['catalog_sha256'],hashlib.sha256((ROOT/'alliance_game/hero_catalog.json').read_bytes()).hexdigest())
        ids=[h['id'] for h in self.meta['heroes']]
        self.assertEqual(len(ids),117);self.assertEqual(len(set(ids)),117)
        self.assertEqual(set(ids),{h['id'] for h in self.catalog['heroes']})
        for hero in self.meta['heroes']:
            expected={c['id'] for c in self.catalog['campaigns'] if any(a['hero_id']==hero['id'] for a in c['aliases'])}
            self.assertEqual({c['id'] for c in hero['campaigns']},expected)
            self.assertTrue(hero['biography']);self.assertLess(len(hero['biography']),240)
            self.assertEqual(set(hero['provenance']['native_types']),{f['source_type'] for f in self.catalog['forms'] if f['hero_id']==hero['id']})
    def test_portrait_content_and_additional_alpha_assets(self):
        game=ROOT.parent/'gamedata-1.19.26'
        for hero in self.meta['heroes']:
            info=hero['portrait_source'];path=(ROOT if info['kind']=='generated' else game)/info['path']
            self.assertTrue(path.is_file(),str(path));raw=path.read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),info['sha256'])
            if info['kind']=='generated':
                self.assertIn(hero['id'],('thea','refumee'))
                self.assertEqual(raw[:8],b'\x89PNG\r\n\x1a\n')
                width,height,depth,colour=struct.unpack('>IIBB',raw[16:26])
                self.assertGreaterEqual(min(width,height),512);self.assertEqual(colour,6)
        manifest=json.loads((ROOT/'tools/native-account-game/ui-assets.json').read_text())
        self.assertEqual({f['path'] for f in manifest['files']},{'hero/thea.png','hero/refumee.png'})
        for record in manifest['files']:
            raw=(ROOT/'tools/native-account-game/ui-assets'/record['path']).read_bytes()
            self.assertEqual(record['bytes'],len(raw));self.assertEqual(record['sha256'],hashlib.sha256(raw).hexdigest())
    def test_compiled_metadata_keeps_catalogue_identity(self):
        data=(ROOT/'alliance_game/hero_presentation_data.inc').read_text()
        for hero in self.meta['heroes']:
            self.assertIn(json.dumps(hero['id'])+',',data)
            self.assertIn(json.dumps(hero['portrait']),data)
            self.assertIn(json.dumps(hero['biography'],ensure_ascii=False),data)

if __name__=='__main__':unittest.main()
