// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "serialization/base64.hpp"
#include <cairo.h>
#include <png.h>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>

namespace alliance_game::menu_icons {
enum class Symbol { quit, preferences, heroes, achievements, load, room, campaign, team, editor, visibility, visibility_hidden };

// All paths share a 100-unit design square. Cairo is already part of the
// pinned engine; GUI2's line primitive ignores thickness and is unsuitable
// for these small outlined symbols. No font glyph or external image is used.
inline void path(cairo_t* c, Symbol symbol) {
    const auto line=[c](double x,double y,double a,double b){cairo_move_to(c,x,y);cairo_line_to(c,a,b);};
    const auto circle=[c](double x,double y,double r){cairo_new_sub_path(c);cairo_arc(c,x,y,r,0,6.283185307179586);};
    const auto shield=[c](double x,double y,double w,double h){cairo_move_to(c,x,y);cairo_line_to(c,x+w,y);cairo_line_to(c,x+w,y+h*.42);cairo_curve_to(c,x+w,y+h*.72,x+w*.7,y+h*.88,x+w*.5,y+h);cairo_curve_to(c,x+w*.3,y+h*.88,x,y+h*.72,x,y+h*.42);cairo_close_path(c);};
    switch(symbol) {
    case Symbol::quit:
        cairo_move_to(c,54,15);cairo_line_to(c,22,15);cairo_line_to(c,22,85);cairo_line_to(c,54,85);
        line(48,50,88,50);line(75,36,89,50);line(89,50,75,64);break;
    case Symbol::preferences:
        for(int n=0;n<48;++n){const double a=n*6.283185307179586/48;const double r=(n%6==1||n%6==2||n%6==3)?40:32;
            const double x=50+std::cos(a)*r,y=50+std::sin(a)*r;if(n)cairo_line_to(c,x,y);else cairo_move_to(c,x,y);}
        cairo_close_path(c);circle(50,50,13);break;
    case Symbol::heroes:
        cairo_move_to(c,42,15);cairo_curve_to(c,45,5,67,5,71,15);cairo_line_to(c,66,23);
        cairo_move_to(c,24,57);cairo_line_to(c,24,43);cairo_curve_to(c,24,21,40,13,52,13);cairo_curve_to(c,70,13,79,28,79,47);
        cairo_line_to(c,79,71);cairo_line_to(c,63,85);cairo_line_to(c,58,61);cairo_line_to(c,45,61);cairo_line_to(c,39,85);cairo_line_to(c,23,73);cairo_line_to(c,24,57);
        line(25,45,77,45);line(51,16,51,42);line(29,54,40,54);line(64,54,73,54);break;
    case Symbol::achievements:
        circle(50,39,18);
        cairo_move_to(c,41,55);cairo_line_to(c,37,83);cairo_line_to(c,50,74);cairo_line_to(c,63,83);cairo_line_to(c,59,55);
        cairo_move_to(c,29,70);cairo_curve_to(c,15,58,14,36,25,22);cairo_move_to(c,71,70);cairo_curve_to(c,85,58,86,36,75,22);
        line(18,32,27,37);line(16,46,26,49);line(20,59,29,60);
        line(82,32,73,37);line(84,46,74,49);line(80,59,71,60);break;
    case Symbol::load:
        cairo_move_to(c,13,35);cairo_line_to(c,13,77);cairo_line_to(c,87,77);cairo_line_to(c,87,35);cairo_line_to(c,69,35);
        cairo_move_to(c,13,35);cairo_line_to(c,13,24);cairo_line_to(c,37,24);cairo_line_to(c,44,34);
        line(54,14,54,60);line(40,47,54,61);line(54,61,68,47);break;
    case Symbol::room:
        cairo_move_to(c,18,16);cairo_line_to(c,34,22);cairo_line_to(c,73,64);cairo_line_to(c,64,73);cairo_line_to(c,22,34);cairo_close_path(c);
        cairo_move_to(c,82,16);cairo_line_to(c,66,22);cairo_line_to(c,27,64);cairo_line_to(c,36,73);cairo_line_to(c,78,34);cairo_close_path(c);
        line(57,75,77,55);line(23,55,43,75);line(70,70,85,85);line(30,70,15,85);break;
    case Symbol::campaign:
        shield(32,23,50,63);
        cairo_move_to(c,16,10);cairo_line_to(c,32,16);cairo_line_to(c,61,55);cairo_line_to(c,52,63);cairo_line_to(c,20,28);cairo_close_path(c);
        line(47,67,66,50);line(59,61,78,81);line(73,85,83,76);break;
    case Symbol::team:
        line(23,12,23,88);line(13,88,35,88);circle(23,10,3);
        cairo_move_to(c,25,19);cairo_curve_to(c,43,8,57,29,83,16);cairo_line_to(c,76,32);cairo_line_to(c,83,48);cairo_curve_to(c,59,60,42,37,25,49);break;
    case Symbol::editor:
        cairo_move_to(c,12,27);cairo_line_to(c,34,17);cairo_line_to(c,56,27);cairo_line_to(c,72,20);cairo_move_to(c,83,51);cairo_line_to(c,83,79);cairo_line_to(c,59,88);cairo_line_to(c,34,78);cairo_line_to(c,12,88);cairo_line_to(c,12,27);
        line(34,17,34,78);line(59,68,59,88);
        cairo_move_to(c,45,68);cairo_curve_to(c,52,31,70,12,92,9);cairo_curve_to(c,86,36,73,54,45,68);line(43,73,83,20);break;
    case Symbol::visibility:
    case Symbol::visibility_hidden:
        cairo_move_to(c,9,50);cairo_curve_to(c,31,17,69,17,91,50);cairo_curve_to(c,69,83,31,83,9,50);circle(50,50,13);
        if(symbol==Symbol::visibility_hidden)line(16,15,84,85);break;
    }
}

inline std::string png_data_uri(cairo_surface_t* surface) {
    if(!surface||cairo_surface_status(surface)!=CAIRO_STATUS_SUCCESS)throw std::runtime_error("Invalid interface raster.");
    const int width=cairo_image_surface_get_width(surface),height=cairo_image_surface_get_height(surface);
    if(width<1||height<1||width>4096||height>4096)throw std::invalid_argument("Invalid interface raster dimensions.");
    // The pinned Android Cairo has its optional PNG encoder disabled. Its
    // ARGB32 raster is native-endian and premultiplied; libpng (already linked
    // by the engine) takes straight RGBA. Unpremultiply before encoding so
    // antialiased gold edges retain their colour over any menu background.
    cairo_surface_flush(surface);
    const auto* data=cairo_image_surface_get_data(surface);const int stride=cairo_image_surface_get_stride(surface);
    std::vector<std::uint8_t> rgba(static_cast<std::size_t>(width)*height*4);
    for(int y=0;y<height;++y)for(int x=0;x<width;++x){std::uint32_t pixel;std::memcpy(&pixel,data+y*stride+x*4,4);const auto alpha=pixel>>24;
        const auto offset=(static_cast<std::size_t>(y)*width+x)*4;
        for(unsigned channel=0;channel<3;++channel){const auto premultiplied=(pixel>>(16-channel*8))&255;
            rgba[offset+channel]=alpha?static_cast<std::uint8_t>(std::min(255u,(premultiplied*255+alpha/2)/alpha)):0;}
        rgba[offset+3]=static_cast<std::uint8_t>(alpha);
    }
    png_image png{};png.version=PNG_IMAGE_VERSION;png.width=width;png.height=height;png.format=PNG_FORMAT_RGBA;
    struct PngCleanup{png_image& image;~PngCleanup(){png_image_free(&image);}}cleanup{png};
    png_alloc_size_t encoded_size=0;
    if(!png_image_write_to_memory(&png,nullptr,&encoded_size,0,rgba.data(),width*4,nullptr)||encoded_size>8*1024*1024)
        throw std::runtime_error("Menu icon encoding failed.");
    std::vector<std::uint8_t> bytes(encoded_size);
    if(!png_image_write_to_memory(&png,bytes.data(),&encoded_size,0,rgba.data(),width*4,nullptr))
        throw std::runtime_error("Menu icon encoding failed.");
    bytes.resize(encoded_size);
    return "data:image/png;base64,"+base64::encode(bytes);
}

inline std::string image(Symbol symbol,int logical_size,std::uint32_t rgb,int pixel_scale=2) {
    if(symbol<Symbol::quit||symbol>Symbol::visibility_hidden||logical_size<16||logical_size>256||pixel_scale<1||pixel_scale>4||rgb>0xffffff)
        throw std::invalid_argument("Invalid menu icon dimensions or colour.");
    using Key=std::tuple<Symbol,int,std::uint32_t,int>;
    static std::map<Key,std::string> cache;
    const Key key{symbol,logical_size,rgb,pixel_scale};const auto found=cache.find(key);if(found!=cache.end())return found->second;
    // Fixed menu symbols/sizes/states keep this cache small; do not allow an
    // unbounded caller-controlled stream of rendered variants.
    if(cache.size()>=192)cache.erase(cache.begin());
    const int size=logical_size*pixel_scale;
    std::unique_ptr<cairo_surface_t,decltype(&cairo_surface_destroy)> surface(cairo_image_surface_create(CAIRO_FORMAT_ARGB32,size,size),cairo_surface_destroy);
    std::unique_ptr<cairo_t,decltype(&cairo_destroy)> context(cairo_create(surface.get()),cairo_destroy);
    auto* c=context.get();cairo_scale(c,size/100.,size/100.);cairo_set_antialias(c,CAIRO_ANTIALIAS_BEST);
    cairo_set_line_width(c,220./logical_size);cairo_set_line_join(c,CAIRO_LINE_JOIN_ROUND);cairo_set_line_cap(c,CAIRO_LINE_CAP_ROUND);
    cairo_set_source_rgb(c,((rgb>>16)&255)/255.,((rgb>>8)&255)/255.,(rgb&255)/255.);path(c,symbol);cairo_stroke(c);
    if(cairo_status(c)!=CAIRO_STATUS_SUCCESS)throw std::runtime_error("Menu icon rendering failed.");
    return cache.emplace(key,png_data_uri(surface.get())).first->second;
}
}
