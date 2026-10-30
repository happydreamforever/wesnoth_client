"""Run the new production Cairo paths under native memory sanitizers."""
import importlib.util
from pathlib import Path
import shlex
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('citadel_icon_runner', ROOT / 'tests/test_citadel_home.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class MobileHomeIconsTests(unittest.TestCase):
    run_cpp = runner.CitadelHomeTests.run_cpp

    def test_storybook_and_help_encoding_bounds_and_shared_symbols(self):
        flags = shlex.split(subprocess.check_output(['pkg-config', '--cflags', '--libs', 'cairo', 'libpng'], text=True))
        self.run_cpp(r'''
#include "tools/android-presentation/presentation_icons.hpp"
#include <cassert>
#include <iostream>
using namespace alliance_game;
int main(){
 using presentation_icons::Symbol;
 for(const auto symbol:{Symbol::campaign,Symbol::help})for(int size:{26,39,96,144}){
  const auto image=presentation_icons::image(symbol,size,product_theme::gold_rgb);
  const auto encoded=base64::decode(image.substr(22));png_image png{};png.version=PNG_IMAGE_VERSION;
  assert(png_image_begin_read_from_memory(&png,encoded.data(),encoded.size()));png.format=PNG_FORMAT_RGBA;
  assert(png.width==unsigned(size*2)&&png.height==unsigned(size*2));std::vector<unsigned char> pixels(PNG_IMAGE_SIZE(png));
  assert(png_image_finish_read(&png,nullptr,pixels.data(),0,nullptr));png_image_free(&png);
  unsigned visible=0,soft=0;for(std::size_t p=3;p<pixels.size();p+=4){visible+=pixels[p]>0;soft+=pixels[p]>0&&pixels[p]<255;}
  assert(visible>100&&soft>30&&pixels[3]==0);
  assert(image==presentation_icons::image(symbol,size,product_theme::gold_rgb));
  assert(image!=presentation_icons::image(symbol,size,product_theme::ivory_rgb));
  const auto emblem=presentation_icons::emblem(symbol,size,true);assert(emblem!=image);
  assert(emblem==presentation_icons::emblem(symbol,size,true));
 }
 assert(presentation_icons::image(Symbol::campaign,96,0xd6b46e)!=menu_icons::image(menu_icons::Symbol::campaign,96,0xd6b46e));
 for(int i=0;i<9;++i)if(i!=6){const auto s=static_cast<Symbol>(i);const auto original=static_cast<menu_icons::Symbol>(i);
  assert(presentation_icons::image(s,26,0xd6b46e)==menu_icons::image(original,26,0xd6b46e));
  assert(presentation_icons::emblem(s,64)==emerald_chrome::emblem(original,64));
 }
 const auto first=presentation_icons::image(Symbol::help,26,0xd6b46e);
 for(unsigned colour=0;colour<220;++colour)presentation_icons::image(Symbol::help,26,colour);
 assert(first==presentation_icons::image(Symbol::help,26,0xd6b46e));
 for(int size:{0,15,257}){bool refused=false;try{presentation_icons::image(Symbol::help,size,0xd6b46e);}catch(const std::invalid_argument&){refused=true;}assert(refused);}
 std::cout<<"MOBILE HOME ICONS PASS\n";
}''', [str(ROOT / 'engine/src/serialization/base64.cpp'), *flags])


if __name__ == '__main__':
    unittest.main()
