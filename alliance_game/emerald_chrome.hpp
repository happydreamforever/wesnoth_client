// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "config.hpp"
#include "alliance_game/menu_icons.hpp"
#include "alliance_game/product_theme.hpp"
#include <array>
#include <functional>

namespace alliance_game::emerald_chrome {
enum class Kind { panel, parchment, button, field, profile };
inline std::string serif_text(const std::string& value) {
    std::string escaped;for(const char ch:value){if(ch=='&')escaped+="&amp;";else if(ch=='<')escaped+="&lt;";else if(ch=='>')escaped+="&gt;";else escaped+=ch;}
    return "<span font_family=\""+std::string(product_theme::title_font_family)+"\">"+escaped+"</span>";
}
inline void reset_markup_cache(config& draw) {
    // The pinned canvas clears Pango attributes before set_text's same-text
    // shortcut. A nonpainting, zero-width glyph makes a fixed markup heading
    // reparse on isolated redraws as well as its first frame.
    auto& reset=draw.add_child("text");reset["text"]="\xE2\x80\x8B";reset["text_markup"]=false;
    reset["x"]=0;reset["y"]=0;reset["w"]=1;reset["h"]=1;reset["maximum_width"]=1;reset["maximum_height"]=1;reset["font_size"]=1;reset["color"]="0,0,0,0";
}
inline void heading_canvas(config& draw,const std::string& heading,int size,const char* colour,bool centered=true) {
    reset_markup_cache(draw);
    auto& text=draw.add_child("text");text["x"]=centered?"((width - text_width) / 2)":"0";text["y"]="((height - text_height) / 2)";
    text["w"]="(text_width)";text["h"]="(text_height)";text["maximum_width"]="(width)";text["maximum_height"]="(height)";
    text["font_size"]=size;text["color"]=colour;text["text"]=serif_text(heading);text["text_markup"]=true;
}
inline void colour(cairo_t* c,std::uint32_t rgb,double alpha=1){cairo_set_source_rgba(c,((rgb>>16)&255)/255.,((rgb>>8)&255)/255.,(rgb&255)/255.,alpha);}
inline void gradient_stop(cairo_pattern_t* p,double at,std::uint32_t rgb,double alpha=1){cairo_pattern_add_color_stop_rgba(p,at,((rgb>>16)&255)/255.,((rgb>>8)&255)/255.,(rgb&255)/255.,alpha);}
inline void stroke(cairo_t* c,std::uint32_t rgb,double width,double alpha=1){colour(c,rgb,alpha);cairo_set_line_width(c,width);cairo_stroke(c);}

// A pointed, lobed oak leaf. The common 100-unit sprig below is also the
// reference for the Android startup's native Canvas decoration.
inline void leaf(cairo_t* c,double x,double y,double length,double angle,bool dark=false,double alpha=1) {
    cairo_save(c);cairo_translate(c,x,y);cairo_rotate(c,angle);cairo_scale(c,length/30.,length/30.);
    cairo_move_to(c,0,0);cairo_curve_to(c,-3,-4,-9,-6,-6,-10);cairo_curve_to(c,-11,-13,-9,-17,-5,-18);
    cairo_curve_to(c,-8,-23,-3,-25,0,-30);cairo_curve_to(c,3,-25,8,-23,5,-18);cairo_curve_to(c,9,-17,11,-13,6,-10);
    cairo_curve_to(c,9,-6,3,-4,0,0);cairo_close_path(c);
    auto* gold=cairo_pattern_create_linear(-8,-20,8,0);gradient_stop(gold,0,dark?product_theme::dark_gold_rgb:product_theme::light_gold_rgb,alpha);gradient_stop(gold,1,product_theme::dark_gold_rgb,alpha);
    cairo_set_source(c,gold);cairo_fill_preserve(c);cairo_pattern_destroy(gold);stroke(c,dark?product_theme::ink_rgb:product_theme::gold_rgb,.65,alpha);
    cairo_move_to(c,0,-2);cairo_line_to(c,0,-25);for(int n=0;n<3;++n){const double yv=-7-n*5;cairo_move_to(c,0,yv);cairo_line_to(c,-4,yv-4);cairo_move_to(c,0,yv);cairo_line_to(c,4,yv-4);}
    stroke(c,dark?product_theme::ink_rgb:product_theme::ivory_rgb,.45,alpha*.75);cairo_restore(c);
}
inline void sprig(cairo_t* c,bool dark=false,double alpha=1) {
    // Stem starts at (50,92); leaf axes fan from the lower middle.
    for(const auto& a:std::array<std::array<double,4>,7>{{{{50,67,42,0}},{{45,76,33,-.65}},{{55,76,33,.65}},{{38,82,27,-1.02}},{{62,82,27,1.02}},{{31,85,20,-1.35}},{{69,85,20,1.35}}}})
        leaf(c,a[0],a[1],a[2],a[3],dark,alpha);
    cairo_move_to(c,50,94);cairo_curve_to(c,50,79,50,66,50,40);cairo_move_to(c,50,91);cairo_curve_to(c,39,86,31,80,22,70);
    cairo_move_to(c,50,91);cairo_curve_to(c,61,86,69,80,78,70);stroke(c,dark?product_theme::dark_gold_rgb:product_theme::light_gold_rgb,1.6,alpha);
}
inline void corner_sprig(cairo_t* c,double x,double y,double size,bool right,bool bottom,bool dark=false) {
    cairo_save(c);cairo_translate(c,x,y);cairo_scale(c,right?-1:1,bottom?-1:1);
    // Both clipping and normalized geometry keep leaves out of stretch strips.
    cairo_rectangle(c,0,0,size,size);cairo_clip(c);cairo_scale(c,size*.75/100.,size*.75/100.);
    for(const auto& a:std::array<std::array<double,4>,5>{{{{38,38,24,-1.0}},{{43,43,24,1.0}},{{55,55,30,-1.0}},{{60,60,28,1.0}},{{83,83,24,-.4}}}})leaf(c,a[0],a[1],a[2],a[3],dark,.9);
    cairo_move_to(c,14,14);cairo_curve_to(c,30,40,58,68,88,88);stroke(c,dark?product_theme::dark_gold_rgb:product_theme::light_gold_rgb,1.3,.85);cairo_restore(c);
}
inline void chamfer(cairo_t* c,double w,double h,double inset,double cut) {
    cairo_move_to(c,inset+cut,inset);cairo_line_to(c,w-inset-cut,inset);cairo_line_to(c,w-inset,inset+cut);
    cairo_line_to(c,w-inset,h-inset-cut);cairo_line_to(c,w-inset-cut,h-inset);cairo_line_to(c,inset+cut,h-inset);
    cairo_line_to(c,inset,h-inset-cut);cairo_line_to(c,inset,inset+cut);cairo_close_path(c);
}
inline void grain(cairo_t* c,double w,double h,bool light) {
    // Deterministic fine stipple, never random process state or image assets.
    for(int y=2;y<h;y+=3)for(int x=2;x<w;x+=3){const auto noise=(x*73856093u)^(y*19349663u);
        colour(c,(noise&1)?0xffffff:0x000000,(light?.07:.045)*((noise>>4)%5)/4.);cairo_rectangle(c,x,y,1,1);cairo_fill(c);}
}
inline std::string raster(int w,int h,int pixel_scale,const std::function<void(cairo_t*)>& paint) {
    if(w<1||h<1||w>2048||h>2048||pixel_scale<1||pixel_scale>4||w*pixel_scale>4096||h*pixel_scale>4096)throw std::invalid_argument("Invalid ornamental raster dimensions.");
    std::unique_ptr<cairo_surface_t,decltype(&cairo_surface_destroy)> surface(cairo_image_surface_create(CAIRO_FORMAT_ARGB32,w*pixel_scale,h*pixel_scale),cairo_surface_destroy);
    std::unique_ptr<cairo_t,decltype(&cairo_destroy)> context(cairo_create(surface.get()),cairo_destroy);
    cairo_scale(context.get(),pixel_scale,pixel_scale);cairo_set_antialias(context.get(),CAIRO_ANTIALIAS_BEST);cairo_set_line_join(context.get(),CAIRO_LINE_JOIN_ROUND);paint(context.get());
    if(cairo_status(context.get())!=CAIRO_STATUS_SUCCESS)throw std::runtime_error("Ornamental frame rendering failed.");
    return menu_icons::png_data_uri(surface.get());
}
inline std::string ornament(int size,bool dark=false,int pixel_scale=2) {
    if(size<8||size>256)throw std::invalid_argument("Invalid leaf ornament size.");
    using Key=std::tuple<int,bool,int>;static std::map<Key,std::string> cache;const Key key{size,dark,pixel_scale};if(const auto it=cache.find(key);it!=cache.end())return it->second;
    if(cache.size()>=48)cache.erase(cache.begin());return cache.emplace(key,raster(size,size,pixel_scale,[=](cairo_t* c){cairo_scale(c,size/100.,size/100.);sprig(c,dark);})).first->second;
}
inline std::string emblem(menu_icons::Symbol symbol,int size,bool dark=false,int pixel_scale=2) {
    using Key=std::tuple<menu_icons::Symbol,int,bool,int>;static std::map<Key,std::string> cache;const Key key{symbol,size,dark,pixel_scale};if(const auto it=cache.find(key);it!=cache.end())return it->second;
    if(cache.size()>=96)cache.erase(cache.begin());return cache.emplace(key,raster(size,size,pixel_scale,[=](cairo_t* c){
        cairo_scale(c,size/100.,size/100.);cairo_set_line_cap(c,CAIRO_LINE_CAP_ROUND);
        if(symbol==menu_icons::Symbol::campaign){cairo_move_to(c,32,23);cairo_line_to(c,82,23);cairo_line_to(c,82,49);cairo_curve_to(c,82,68,66,82,57,86);cairo_curve_to(c,46,81,32,67,32,49);cairo_close_path(c);colour(c,product_theme::stone_rgb);cairo_fill(c);}
        menu_icons::path(c,symbol);stroke(c,product_theme::ink_rgb,5.2,.9);
        menu_icons::path(c,symbol);auto* gold=cairo_pattern_create_linear(15,8,70,85);gradient_stop(gold,0,product_theme::ivory_rgb);gradient_stop(gold,.38,product_theme::light_gold_rgb);gradient_stop(gold,.65,product_theme::dark_gold_rgb);gradient_stop(gold,1,product_theme::gold_rgb);cairo_set_source(c,gold);cairo_set_line_width(c,3);cairo_stroke(c);cairo_pattern_destroy(gold);
        if(dark){cairo_save(c);cairo_translate(c,.4,.4);menu_icons::path(c,symbol);stroke(c,product_theme::dark_gold_rgb,.6,.65);cairo_restore(c);}
    })).first->second;
}
inline void ornament_canvas(config& draw,int size=40,const std::string& x="((width - 40) / 2)",const std::string& y="0",bool dark=false) {
    auto& image=draw.add_child("image");image["name"]=ornament(size,dark);image["x"]=x;image["y"]=y;image["w"]=size;image["h"]=size;image["resize_mode"]="scale";
}
inline void frame_path(cairo_t* c,int w,int h,Kind kind,bool highlighted,bool disabled,bool filled,int corner) {
    const bool parchment=kind==Kind::parchment;const double alpha=disabled?.5:1;
    const auto bright=highlighted?product_theme::ivory_rgb:product_theme::light_gold_rgb;
    const auto edge=disabled?0x777765u:product_theme::dark_gold_rgb;
    const double cut=std::min(10.,corner*.27);
    auto fill=parchment?product_theme::parchment_rgb:product_theme::stone_rgb;
    if(highlighted&&!parchment){std::uint32_t brighter=0;for(int shift:{0,8,16}){const auto channel=(fill>>shift)&255;const auto light=(product_theme::ivory_rgb>>shift)&255;brighter|=static_cast<std::uint32_t>(channel*.88+light*.12)<<shift;}fill=brighter;}
    if(filled){chamfer(c,w,h,2,cut);colour(c,fill);cairo_fill(c);}
    chamfer(c,w,h,2,cut);stroke(c,product_theme::ink_rgb,4,alpha*.8);
    chamfer(c,w,h,2,cut);stroke(c,edge,2.6,alpha);
    chamfer(c,w,h,3.5,cut);stroke(c,bright,.9,alpha);
    chamfer(c,w,h,6,cut*.7);stroke(c,product_theme::gold_rgb,.8,alpha*.85);
    if(kind==Kind::panel||kind==Kind::profile||kind==Kind::parchment){
        if(kind!=Kind::profile){corner_sprig(c,0,0,corner,false,false,parchment);corner_sprig(c,w,0,corner,true,false,parchment);corner_sprig(c,0,h,corner,false,true,parchment);}
        corner_sprig(c,w,h,corner,true,true,parchment);
    }
}
inline std::array<std::string,9> frame(Kind kind,bool highlighted,bool disabled,int corner,bool filled) {
    if(corner<8||corner>80)throw std::invalid_argument("Invalid ornamental frame corner.");
    using Key=std::tuple<Kind,bool,bool,int,bool>;static std::map<Key,std::array<std::string,9>> cache;const Key key{kind,highlighted,disabled,corner,filled};if(const auto it=cache.find(key);it!=cache.end())return it->second;
    if(cache.size()>=48)cache.erase(cache.begin());std::array<std::string,9> tiles;
    for(int y=0;y<3;++y)for(int x=0;x<3;++x)tiles[y*3+x]=raster(corner,corner,2,[=](cairo_t* c){cairo_translate(c,-x*corner,-y*corner);frame_path(c,corner*3,corner*3,kind,highlighted,disabled,filled,corner);
        if(x==1&&y==1&&filled){cairo_translate(c,corner,corner);grain(c,corner,corner,kind==Kind::parchment);}});
    return cache.emplace(key,std::move(tiles)).first->second;
}
inline void frame_canvas(config& draw,Kind kind=Kind::panel,bool highlighted=false,bool disabled=false,int corner=24,bool filled=true) {
    const auto images=frame(kind,highlighted,disabled,corner,filled);const std::string n=std::to_string(corner);
    // Corners never stretch; only the straight edge and centre tiles grow.
    for(int y=0;y<3;++y)for(int x=0;x<3;++x){auto& image=draw.add_child("image");image["name"]=images[y*3+x];image["resize_mode"]=x==1&&y==1?"tile_highres":"scale";
        image["x"]=x==0?"0":x==1?n:"(width - "+n+")";image["y"]=y==0?"0":y==1?n:"(height - "+n+")";
        image["w"]=x==1?"(max(0, width - "+std::to_string(corner*2)+"))":n;image["h"]=y==1?"(max(0, height - "+std::to_string(corner*2)+"))":n;
    }
}

inline std::string plaque(int width,int height,bool campaign,bool highlighted,bool disabled,int pixel_scale=2) {
    using Key=std::tuple<int,int,bool,bool,bool,int>;static std::map<Key,std::string> cache;const Key key{width,height,campaign,highlighted,disabled,pixel_scale};if(const auto it=cache.find(key);it!=cache.end())return it->second;
    if(cache.size()>=48)cache.erase(cache.begin());const auto uri=raster(width,height,pixel_scale,[=](cairo_t* c){
        const double s=width/(campaign?184.:164.);cairo_scale(c,s,s);const double w=width/s,h=height/s;
        const auto path=[&](double inset){const double top=campaign?16:6,bottom=campaign?16:6;
            cairo_move_to(c,inset+9,top+inset);cairo_line_to(c,w/2-20,top+inset);
            if(campaign){cairo_curve_to(c,w/2-7,top+inset-3,w/2-3,inset+3,w/2,inset);cairo_curve_to(c,w/2+3,inset+3,w/2+7,top+inset-3,w/2+20,top+inset);}
            cairo_line_to(c,w-inset-9,top+inset);cairo_line_to(c,w-inset,top+inset+9);cairo_line_to(c,w-inset,h-bottom-inset-9);cairo_line_to(c,w-inset-9,h-bottom-inset);
            cairo_line_to(c,w/2+20,h-bottom-inset);if(campaign){cairo_curve_to(c,w/2+7,h-bottom-inset+3,w/2+3,h-inset-3,w/2,h-inset);cairo_curve_to(c,w/2-3,h-inset-3,w/2-7,h-bottom-inset+3,w/2-20,h-bottom-inset);}
            cairo_line_to(c,inset+9,h-bottom-inset);cairo_line_to(c,inset,h-bottom-inset-9);cairo_line_to(c,inset,top+inset+9);cairo_close_path(c);};
        path(2);colour(c,product_theme::ink_rgb,.8);cairo_set_line_width(c,5);cairo_stroke(c);
        path(3);auto* fill=cairo_pattern_create_linear(0,0,w,h);gradient_stop(fill,0,campaign?product_theme::light_gold_rgb:product_theme::teal_shadow_rgb);gradient_stop(fill,.45,campaign?product_theme::parchment_rgb:product_theme::stone_rgb);gradient_stop(fill,1,campaign?0xD2B675u:product_theme::ink_rgb);cairo_set_source(c,fill);cairo_fill_preserve(c);cairo_pattern_destroy(fill);stroke(c,product_theme::dark_gold_rgb,3);
        cairo_save(c);path(4);cairo_clip(c);grain(c,w,h,campaign);if(campaign){cairo_save(c);cairo_translate(c,w*.07,h*.37);cairo_scale(c,w/130.,w/130.);sprig(c,true,.12);cairo_restore(c);}cairo_restore(c);
        path(4.7);stroke(c,highlighted?product_theme::ivory_rgb:product_theme::light_gold_rgb,1.3);
        path(8);stroke(c,product_theme::dark_gold_rgb,.9);path(10);stroke(c,product_theme::gold_rgb,.65,.65);
        for(int n=0;n<4;++n){cairo_save(c);cairo_translate(c,n%2?w-12:12,n<2?(campaign?29:19):h-(campaign?29:19));cairo_scale(c,n%2?-1:1,n>=2?-1:1);cairo_move_to(c,0,18);cairo_curve_to(c,7,9,6,1,19,0);cairo_curve_to(c,9,7,1,7,0,18);stroke(c,product_theme::light_gold_rgb,1);cairo_restore(c);}
        if(disabled){path(3);colour(c,0x77776C,.55);cairo_fill(c);}
    });return cache.emplace(key,uri).first->second;
}
}
