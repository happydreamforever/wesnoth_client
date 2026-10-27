"""Production Cairo icons and generated native title callback regressions.

Run in the pinned native verification image (Cairo/compiler required). Actual
GUI2 geometry, focus, tooltip and long-press checks use the renderer helper.
"""
import importlib.util
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('title_overlay', ROOT / 'alliance_game/title_overlay.py')
TITLE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(TITLE)


class CitadelHomeTests(unittest.TestCase):
    def run_cpp(self, source, extra=()):
        with tempfile.TemporaryDirectory(prefix='citadel-home-') as temporary:
            path = Path(temporary); (path / 'probe.cpp').write_text(source)
            command = [os.environ.get('CXX', 'g++'), '-std=c++17', '-O1', '-g',
                       '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                       '-I' + str(ROOT), '-I' + str(ROOT / 'engine/src'), '-I' + os.environ.get('BOOST_INCLUDE', '/opt/boost/include'),
                       str(path / 'probe.cpp'), *extra, '-o', str(path / 'probe')]
            compiled = subprocess.run(command, capture_output=True, text=True, timeout=90)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(path / 'probe')], check=True, capture_output=True,
                                    text=True, timeout=30)
            self.assertIn('PASS', result.stdout)

    def test_real_cairo_icons_alpha_variants_and_bounded_cache(self):
        flags = shlex.split(subprocess.check_output(['pkg-config', '--cflags', '--libs', 'cairo', 'libpng'], text=True))
        self.run_cpp(r'''
#include "alliance_game/menu_icons.hpp"
#include <cassert>
#include <cstring>
#include <iostream>
#include <set>
using namespace alliance_game::menu_icons;
int main(){
 std::set<std::string> symbols;
 for(int i=0;i<11;++i){const auto uri=image(static_cast<Symbol>(i),26,0xd6b46e,2);
  assert(uri.rfind("data:image/png;base64,",0)==0);assert(symbols.insert(uri).second);
  auto bytes=base64::decode(uri.substr(22));assert(bytes.size()>100);
  struct Reader{const std::vector<std::uint8_t>& bytes;std::size_t position=0;}reader{bytes};
  auto read=[](void* data,unsigned char* out,unsigned count)->cairo_status_t{auto& r=*static_cast<Reader*>(data);if(r.position+count>r.bytes.size())return CAIRO_STATUS_READ_ERROR;std::memcpy(out,r.bytes.data()+r.position,count);r.position+=count;return CAIRO_STATUS_SUCCESS;};
  auto* surface=cairo_image_surface_create_from_png_stream(read,&reader);assert(cairo_surface_status(surface)==CAIRO_STATUS_SUCCESS);
  assert(cairo_image_surface_get_width(surface)==52&&cairo_image_surface_get_height(surface)==52);
  cairo_surface_flush(surface);const auto* pixels=reinterpret_cast<const std::uint32_t*>(cairo_image_surface_get_data(surface));const int stride=cairo_image_surface_get_stride(surface)/4;
  unsigned opaque=0,antialiased=0;for(int y=0;y<52;++y)for(int x=0;x<52;++x){const auto pixel=pixels[y*stride+x],alpha=pixel>>24;opaque+=alpha>0;antialiased+=alpha>0&&alpha<255;
   if(alpha>32&&alpha<224)for(unsigned channel=0;channel<3;++channel){const int actual=(pixel>>(16-channel*8))&255,expected=(((0xd6b46eu>>(16-channel*8))&255)*alpha+127)/255;assert(std::abs(actual-expected)<=1);}
   if(alpha==0)assert((pixel&0xffffff)==0);
  }
  assert((pixels[0]>>24)==0&&opaque>150&&opaque<1800&&antialiased>50);cairo_surface_destroy(surface);
  assert(uri==image(static_cast<Symbol>(i),26,0xd6b46e,2));
  assert(uri!=image(static_cast<Symbol>(i),39,0xd6b46e,2));
  assert(uri!=image(static_cast<Symbol>(i),26,0xf3e7cd,2));
 }
 const auto first=image(Symbol::campaign,64,0xd6b46e);
 for(unsigned i=0;i<300;++i)assert(!image(Symbol::campaign,64,i).empty());
 assert(first==image(Symbol::campaign,64,0xd6b46e));
 for(const int size:{0,15,129}){bool refused=false;try{image(Symbol::quit,size,0xd6b46e);}catch(const std::invalid_argument&){refused=true;}assert(refused);}
 std::cout<<"CAIRO HOME ICONS PASS\n";
}''', [str(ROOT / 'engine/src/serialization/base64.cpp'), *flags])

    def test_generated_native_destination_callbacks_and_profile_refresh(self):
        generated = TITLE.generate_title_overlay((ROOT / 'engine/src/gui/dialogs/title_screen.cpp').read_bytes()).decode()
        callbacks = generated[generated.index('void title_screen::init_callbacks()'):generated.index('\nvoid title_screen::update_static_labels()')]
        multiplayer = generated[generated.index('void title_screen::button_callback_multiplayer()'):generated.index('\nvoid title_screen::button_callback_cores()')]
        self.run_cpp(r'''
#include <cassert>
#include <functional>
#include <iostream>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>
enum{LAUNCH_GAME=11,RELOAD_UI,LOAD_GAME,MAP_EDITOR,QUIT_GAME,MP_CONNECT};
namespace event{enum{SDL_VIDEO_RESIZE};}
namespace hotkey{enum{TITLE_SCREEN__CAMPAIGN,TITLE_SCREEN__MULTIPLAYER,HOTKEY_NULL,HOTKEY_LOAD_GAME,TITLE_SCREEN__ADDONS,HOTKEY_ACHIEVEMENTS,TITLE_SCREEN__EDITOR,HOTKEY_PREFERENCES,HOTKEY_QUIT_TO_DESKTOP};}
namespace config{struct error:std::runtime_error{using runtime_error::runtime_error;};}
namespace gui2{void show_error_message(const std::string&){} }
namespace dialogs{struct achievements_dialog{static inline int shown=0;static void display(){++shown;}};}
namespace alliance_game{
 struct Player{struct{std::string username="UniPlayer";std::vector<int> heroes;}account;struct{std::string address="100.99.99.14";}trusted;};
 inline std::optional<Player> player=Player{};inline int refreshed=0,heroes_shown=0,teams_shown=0;inline bool valid=true,shared=false,replay=false;
 inline constexpr int shared_title_result=51,replay_title_result=52;
 auto player_session(){return player;}
 bool ensure_player_session(){++refreshed;if(valid&&player)player->account.heroes.resize(117);return valid;}
 void show_account_dialog(){++heroes_shown;}void show_teams_dialog(){++teams_shown;}
 bool shared_pending(){return shared;}bool replay_pending(){return replay;}
 namespace home_ui{inline int bound=0;inline std::size_t count=0;inline std::string name;
 template<class T>void set_profile(T&,const std::string& n,std::size_t c){name=n;count=c;}
 template<class T>void bind(T&){++bound;}}
}
struct title_screen{
 struct Game{bool campaign=false,load=false;std::string server;bool new_campaign(){return campaign;}bool load_game_prompt(){return load;}void select_mp_server(std::string value){server=value;}}game_;
 std::map<std::string,std::function<void()>> actions;int result=0,preferences=0;bool hidden=false;
 void init_callbacks();void button_callback_multiplayer();void on_resize(){}
 void set_click_dismiss(bool){}void set_enter_disabled(bool){}void set_escape_disabled(bool){}
 template<int E,class F>void connect_signal(F){}void register_button(std::string id,int,std::function<void()> f){actions[id]=std::move(f);}
 void set_retval(int r){result=r;}void hide(){hidden=true;}void show_preferences(){++preferences;}
};
''' + callbacks + multiplayer + r'''
int main(){using namespace alliance_game;title_screen menu;menu.init_callbacks();assert(menu.actions.size()==9&&home_ui::bound==1&&home_ui::count==0);
 for(const char* id:{"campaign","load","addons","achievements","preferences","community"}){const int before=refreshed;menu.actions.at(id)();assert(refreshed==before+1&&home_ui::count==117&&home_ui::name=="UniPlayer");}
 assert(heroes_shown==1&&teams_shown==1&&menu.preferences==1&&dialogs::achievements_dialog::shown==1);
 int before=refreshed;menu.game_.campaign=true;menu.actions.at("campaign")();assert(menu.result==LAUNCH_GAME&&menu.hidden&&refreshed==before);
 menu.game_.load=true;menu.actions.at("load")();assert(menu.result==LOAD_GAME&&refreshed==before);
 shared=true;menu.actions.at("community")();assert(menu.result==shared_title_result&&refreshed==before);shared=false;replay=true;menu.actions.at("community")();assert(menu.result==replay_title_result&&refreshed==before);replay=false;
 menu.actions.at("editor")();assert(menu.result==MAP_EDITOR);menu.actions.at("quit")();assert(menu.result==QUIT_GAME&&refreshed==before);
 menu.actions.at("multiplayer")();assert(menu.result==MP_CONNECT&&menu.game_.server=="100.99.99.14:15000");
 valid=false;menu.actions.at("preferences")();assert(menu.result==QUIT_GAME);
 player.reset();before=refreshed;menu.actions.at("addons")();assert(menu.result==RELOAD_UI&&refreshed==before);
 std::cout<<"NATIVE HOME CALLBACKS PASS\n";
}''')

    def test_overlay_refuses_changed_pinned_title_source(self):
        source = (ROOT / 'engine/src/gui/dialogs/title_screen.cpp').read_bytes()
        with self.assertRaisesRegex(ValueError, 'original preserved'):
            TITLE.generate_title_overlay(source + b'\n')


if __name__ == '__main__':
    unittest.main()
