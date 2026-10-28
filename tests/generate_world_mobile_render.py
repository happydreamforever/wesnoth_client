"""Render production World/Company GUI2 windows with deterministic public replies.

The generated hook observes windows and sends touch-equivalent button events;
it is not compiled into the product and never contacts a player database.
"""
from pathlib import Path
import argparse
import subprocess
import sys
import os

parser=argparse.ArgumentParser()
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
root=Path(__file__).resolve().parents[1]
out=args.output
retained={p:(p.read_bytes(),p.stat().st_mtime_ns) for p in out.glob("*.cpp")} if out.exists() else {}
subprocess.run([sys.executable,str(root/'tests/generate_emerald_ui_render.py'),'--output',str(out)],check=True)
p=out/'render_probe.cpp'
s=p.read_text()
# Keep the actual native battle widgets and theme helpers, replacing only the
# audit cases. Async public reply fixtures use the production receiver.
start=s.index('bool auth_render_fixture(game_launcher&)')
end=s.index('\nvoid citadel_native_arm',start)
s=s[:start]+(root/'alliance_game/world_ui.hpp').read_text().replace('#pragma once','').replace('void pre_show()override{','void pre_show()override{arm(*this);')+'\n'+(root/'tests/world_mobile_render_cases.hpp').read_text()+s[end:]
s=s.replace('void arm(gui2::window& w){', 'Uint64 world_armed_at=0;void arm(gui2::window& w){world_armed_at=SDL_GetTicks();')
s=s.replace('if(!watched)return;++ticks;', 'if(!watched)return;++ticks;world_mobile_pump(*watched,phase,ticks);')
s=s.replace('if(ticks<12)return;', 'if(phase.find("world-")==0&&(ticks<40||SDL_GetTicks()-world_armed_at<500))return; if(ticks<12)return;')
s=s.replace('"home_title","home_player"', '"objective_castle_0","objective_castle_5","objective_area","objectives_accept","use_companies","world_zoom_out","world_zoom_in","world_centre","world_recruit","world_rest","world_expeditions","world_team","world_roster","world_settings","world_tab_0","world_tab_1","world_tab_2","world_tab_3","world_search","world_map_toggle","world_castle_0","world_castle_5","world_primary","world_secondary","world_more","world_refresh","force_slot_0","force_slot_3","force_select_0","force_select_5","force_choose_hero","force_bracket","force_previous","force_next","force_save","picker_choose","picker_list","home_title","home_player"')
s=s.replace('if(!pixels||!SDL_SaveBMP', """if(phase.find("world-")==0){
 Uint8 r=0,g=0,b=0,a=0;const auto screen=video::game_canvas_size();const auto panel=w->get_rectangle();
 if(!pixels||!SDL_ReadSurfacePixel(pixels,(panel.x+12)*pixels->w/screen.x,(panel.y+100)*pixels->h/screen.y,&r,&g,&b,&a)||!(g>r+4&&g>b&&a==255))throw std::runtime_error("World window was not painted before capture: "+phase);
 }
 if(!pixels||!SDL_SaveBMP""")
s=s.replace('if(!c||c->get_visible()!=gui2::widget::visibility::visible)continue;', 'if(!c)continue;bool shown=true;for(auto* ancestor=c;ancestor;ancestor=ancestor->parent())if(ancestor->get_visible()!=gui2::widget::visibility::visible){shown=false;break;}if(!shown)continue;')
s=s.replace('const auto box=w->get_rectangle();', 'if(phase.find("world-")==0)world_mobile_assert(*w,phase);\n const auto box=w->get_rectangle();')
s=s.replace('namespace {gui2::window* watched=', 'void world_mobile_assert(gui2::window&,const std::string&);\nvoid world_mobile_pump(gui2::window&,const std::string&,int);\nnamespace {gui2::window* watched=')
p.write_text(s)
for path,(content,stamp) in retained.items():
    if path.exists() and path.read_bytes()==content:os.utime(path,ns=(stamp,stamp))
print('World mobile production render fixture generated:',p)
