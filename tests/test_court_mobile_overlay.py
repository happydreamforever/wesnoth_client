"""Release 1192628 presentation isolation and guarded production generation."""
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('court_mobile_overlay',ROOT/'tools/android-presentation/court_mobile_overlay.py')
mobile=importlib.util.module_from_spec(spec);spec.loader.exec_module(mobile)

class CourtMobileOverlay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original=(ROOT/'alliance_game/court_ui.hpp').read_bytes()
        cls.generated=mobile.generate_workspace(cls.original).decode()
    def test_worker_sources_pinned_and_not_written(self):
        self.assertEqual(hashlib.sha256(self.original).hexdigest(),mobile.WORKSPACE_PIN)
        source=(ROOT/'alliance_game/account_ui.cpp').read_bytes()
        self.assertEqual(mobile.generate_account_ui(source),source.replace(b'#include "alliance_game/court_ui.hpp"',b'#include "court_mobile_workspace.hpp"'))
    def test_fail_closed_for_shared_source_drift(self):
        for data in (self.original+b'\n',self.original.replace(b'void submit()',b'void submit_changed()')):
            with self.assertRaises(ValueError):mobile.generate_workspace(data)
        with self.assertRaises(ValueError):mobile.generate_account_ui(b'wrong source')
    def test_mutation_and_confirmation_methods_preserved(self):
        original=self.original.decode()
        for begin,end in [('    void submit(){','    void process()override'),('    void begin(','    void actions();'),('    bool collect_fields(){','    void render_form(){')]:
            if begin=='    void begin(':continue
            self.assertEqual(mobile.section(original,begin,end),mobile.section(self.generated,begin,end))
        self.assertIn('if(form_)submit();else if(mobile_can_run(0))',self.generated)
        self.assertIn('hooks_.journal)court_command_record::acknowledge',self.generated)
    def test_retained_renderer_not_old_png_overlay(self):
        self.assertNotIn('map_cache',self.generated)
        self.assertIn('#include "court_mobile_workspace.inc"',self.generated)
        renderer=(ROOT/'tools/android-presentation/court_mobile_workspace.inc').read_text()
        self.assertNotIn('json::serialize',renderer)
        self.assertNotIn('data:image',renderer)
        self.assertIn('SDL_TOUCH_MOUSEID',renderer)
        self.assertIn('SDL_EVENT_FINGER_CANCELED',renderer)
    def test_overview_manifest_bound_and_originals_retained(self):
        root=ROOT/'tools/native-account-game/ui-assets';manifest=json.loads((root.parent/'ui-assets.json').read_text())
        records={v['path']:v for v in manifest['files']}
        for name in ('world','area'):
            for suffix in ('','-overview'):
                path=f'court/{name}{suffix}.png';data=(root/path).read_bytes()
                self.assertEqual(records[path]['sha256'],hashlib.sha256(data).hexdigest())
                self.assertEqual(records[path]['bytes'],len(data))
            self.assertEqual(int.from_bytes((root/f'court/{name}-overview.png').read_bytes()[16:20],'big'),1024)
    def test_profile_passive_and_fitting(self):
        text=(ROOT/'tools/android-presentation/mobile_home.hpp').read_text()
        self.assertNotIn('describe_profile',text)
        self.assertNotIn('profile_tooltip',text)
        self.assertIn('r["default_width"]=fit?0:width',text)
        self.assertIn('set_use_tooltip_on_label_overflow(false)',text)
        self.assertIn('if(label.get_label().str()!=value)',text)

if __name__=='__main__':unittest.main()
