"""Real geometry/camera and exact public-map packaging checks; no game runtime."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class CourtMapTest(unittest.TestCase):
    def test_exact_manifest_and_shared_geometry(self):
        manifest=json.loads((ROOT/'tools/native-account-game/ui-assets.json').read_text())
        entries={r['path']:r for r in manifest['files'] if r['path'].startswith('court/')}
        self.assertEqual(set(entries),{'court/world.png','court/area.png','court/geometry.json'})
        for name,entry in entries.items():
            data=(ROOT/'tools/native-account-game/ui-assets'/name).read_bytes()
            self.assertEqual((len(data),hashlib.sha256(data).hexdigest()),(entry['bytes'],entry['sha256']))
        geometry=json.loads((ROOT/'tools/native-account-game/ui-assets/court/geometry.json').read_text())
        self.assertEqual(len(geometry['world']),128);self.assertEqual(len(geometry['castle_template']),64)
        ids={area['id']+'-'+castle['id'] for area in geometry['world'] for castle in geometry['castle_template']}
        self.assertEqual(len(ids),8192)
        for node in geometry['world']+geometry['castle_template']:
            self.assertEqual(set(node),{'id','name','x','y','polygon'})
            self.assertEqual(len(node['polygon']),6)
            self.assertTrue(0<=node['x']<=1 and 0<=node['y']<=1)
        source=json.loads((ROOT/'alliance_game/court_assets/source-verification.json').read_text())
        self.assertEqual(len(source['source_files']),27)
        self.assertFalse(any('Area_A001_' in r['path'] for r in source['source_files']))
        self.assertEqual(len({r['path'] for r in source['source_files']}),27)

    def test_court_sources_are_bound_to_the_owned_android_stage(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('court_android_builder',ROOT/'tools/build_android_account_game.py')
        builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
        for name in ('ui','common','command_record','map','geometry','bracket'):
            path='alliance_game/court_'+name+'.hpp'
            self.assertIn(path,builder.NEW_INPUTS)
            self.assertTrue((ROOT/path).is_file())
        self.assertIn('alliance_native/court_protocol.hpp',builder.NEW_INPUTS)
        self.assertEqual(builder.VERSION_CODE,1192626)
        self.assertIn(1192625,builder.PREVIOUS_VERSION_CODES)

    def test_touch_overlay_preserves_native_execution_and_refuses_changed_sources(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('court_touch_overlay',ROOT/'alliance_game/court_touch_overlay.py')
        overlay=importlib.util.module_from_spec(spec);spec.loader.exec_module(overlay)
        source=ROOT/'engine/src/hotkey/hotkey_handler.cpp';original=source.read_bytes()
        generated=overlay.generate(original)
        self.assertEqual(source.read_bytes(),original)
        self.assertIn(b'expand_wml_commands(available)',generated)
        self.assertIn(b'return can_execute_command(hotkey::ui_command(id))',generated)
        dispatch=original[original.index(b'bool play_controller::hotkey_handler::do_execute_command'):original.index(b'bool play_controller::hotkey_handler::can_execute_command')]
        self.assertIn(dispatch,generated)
        with self.assertRaises(ValueError):overlay.generate(original+b'\n')
        with self.assertRaises(ValueError):overlay.generate(generated)

    def test_native_camera_and_gestures(self):
        source=r'''
#include "alliance_game/court_map.hpp"
#include <cassert>
#include <iostream>
using namespace alliance_game::court_map;
int main(){
 Camera c;c.resize(900,600,1.5);
 for(const auto& n:world_nodes){assert(contains(n,{n.x,n.y}));auto p=c.project({n.x,n.y});auto q=c.inverse(p);assert(std::abs(q.x-n.x)<1e-9&&std::abs(q.y-n.y)<1e-9);}
 for(const auto& n:castle_nodes){assert(contains(n,{n.x,n.y}));assert(castle_id("A128",n).substr(0,5)=="A128-");}
 auto anchor=Point{380,240};auto before=c.inverse(anchor);c.zoom_at(3,anchor);auto after=c.inverse(anchor);assert(std::abs(before.x-after.x)<1e-9&&std::abs(before.y-after.y)<1e-9);
 c.pan({100000,100000});assert(c.cx>=0&&c.cy>=0);c.pan({-100000,-100000});assert(c.cx<=1&&c.cy<=1);c.centre();assert(c.zoom==1&&c.cx==.5&&c.cy==.5);
 Gesture gesture;gesture.down(1,{200,200});assert(gesture.up(1).has_value());
 gesture.down(1,{200,200});gesture.move(1,{220,210},c);assert(!gesture.up(1));
 gesture.down(1,{200,200});gesture.down(2,{300,200});gesture.move(2,{400,200},c);assert(c.zoom==2);assert(!gesture.up(2));assert(!gesture.up(1));
 gesture.down(1,{200,200});gesture.down(2,{300,200});assert(!gesture.up(2));assert(!gesture.up(1));
 gesture.down(1,{200,200});gesture.cancel();assert(!gesture.up(1));
 c.resize(1600,900,1672./941.);c.zoom_at(100,{800,450});assert(c.zoom==8);c.zoom_at(.00001,{800,450});assert(c.zoom==1);
 std::cout<<"COURT CAMERA AND MULTITOUCH PASSED\n";
}'''
        with tempfile.TemporaryDirectory(prefix='court-map-') as raw:
            path=Path(raw);(path/'test.cpp').write_text(source)
            subprocess.run(['g++','-std=c++17','-fsanitize=address,undefined','-fno-omit-frame-pointer','-I',str(ROOT),str(path/'test.cpp'),'-o',str(path/'check')],check=True)
            subprocess.run([str(path/'check')],check=True)

if __name__=='__main__':unittest.main()
