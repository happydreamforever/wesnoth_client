// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "alliance_game/emerald_chrome.hpp"

// Android-only menu symbols keep the accepted Windows engine source binding
// unchanged. Existing symbols still use the shared Emerald Court renderer.
namespace alliance_game::presentation_icons {
enum class Symbol { quit, preferences, heroes, achievements, load, room, campaign, team, editor, help };
inline menu_icons::Symbol existing(Symbol symbol) {
    return static_cast<menu_icons::Symbol>(symbol);
}
inline void path(cairo_t* c,Symbol symbol) {
    if(symbol==Symbol::help) {
        cairo_move_to(c,31,31);cairo_curve_to(c,31,9,72,8,72,32);
        cairo_curve_to(c,72,46,51,48,51,64);
        cairo_move_to(c,51,80);cairo_line_to(c,51,82);
    } else if(symbol==Symbol::campaign) {
        // An open, timeworn storybook: gilt covers, raised spine, leaves and
        // a ribbon bookmark. Clear at the native card's rendered size.
        cairo_move_to(c,9,24);cairo_curve_to(c,25,17,38,18,50,28);
        cairo_curve_to(c,62,18,75,17,91,24);cairo_line_to(c,91,79);
        cairo_curve_to(c,75,72,62,73,50,84);cairo_curve_to(c,38,73,25,72,9,79);cairo_close_path(c);
        cairo_move_to(c,14,20);cairo_curve_to(c,27,14,39,17,50,26);
        cairo_curve_to(c,61,17,73,14,86,20);cairo_line_to(c,86,73);
        cairo_curve_to(c,72,68,61,71,50,79);cairo_curve_to(c,39,71,28,68,14,73);cairo_close_path(c);
        cairo_move_to(c,50,27);cairo_line_to(c,50,78);
        cairo_move_to(c,65,22);cairo_line_to(c,65,50);cairo_line_to(c,71,45);cairo_line_to(c,77,48);cairo_line_to(c,77,19);
        for(int y:{35,45,55,65}) {cairo_move_to(c,22,y);cairo_curve_to(c,29,y-2,36,y,41,y+3);}
        for(int y:{57,66}) {cairo_move_to(c,58,y);cairo_curve_to(c,65,y-4,72,y-5,79,y-3);}
    } else menu_icons::path(c,existing(symbol));
}
inline std::string image(Symbol symbol,int logical_size,std::uint32_t rgb,int pixel_scale=2) {
    if(symbol<Symbol::quit||symbol>Symbol::help||logical_size<16||logical_size>256||pixel_scale<1||pixel_scale>4||rgb>0xffffff)
        throw std::invalid_argument("Invalid presentation icon dimensions or colour.");
    if(symbol!=Symbol::help&&symbol!=Symbol::campaign)return menu_icons::image(existing(symbol),logical_size,rgb,pixel_scale);
    using Key=std::tuple<Symbol,int,std::uint32_t,int>;static std::map<Key,std::string> cache;
    const Key key{symbol,logical_size,rgb,pixel_scale};if(const auto i=cache.find(key);i!=cache.end())return i->second;
    if(cache.size()>=96)cache.erase(cache.begin());
    return cache.emplace(key,emerald_chrome::raster(logical_size,logical_size,pixel_scale,[=](cairo_t* c){
        cairo_scale(c,logical_size/100.,logical_size/100.);cairo_set_line_cap(c,CAIRO_LINE_CAP_ROUND);
        path(c,symbol);emerald_chrome::stroke(c,rgb,220./logical_size);
    })).first->second;
}
inline std::string emblem(Symbol symbol,int size,bool dark=false,int pixel_scale=2) {
    if(symbol!=Symbol::campaign&&symbol!=Symbol::help)return emerald_chrome::emblem(existing(symbol),size,dark,pixel_scale);
    if(size<16||size>256||pixel_scale<1||pixel_scale>4)throw std::invalid_argument("Invalid presentation emblem dimensions.");
    using Key=std::tuple<Symbol,int,bool,int>;static std::map<Key,std::string> cache;
    const Key key{symbol,size,dark,pixel_scale};if(const auto i=cache.find(key);i!=cache.end())return i->second;
    if(cache.size()>=96)cache.erase(cache.begin());
    return cache.emplace(key,emerald_chrome::raster(size,size,pixel_scale,[=](cairo_t* c){
        cairo_scale(c,size/100.,size/100.);cairo_set_line_cap(c,CAIRO_LINE_CAP_ROUND);
        path(c,symbol);emerald_chrome::stroke(c,product_theme::ink_rgb,5.2,.9);
        path(c,symbol);auto* gold=cairo_pattern_create_linear(15,8,70,85);
        emerald_chrome::gradient_stop(gold,0,product_theme::ivory_rgb);emerald_chrome::gradient_stop(gold,.38,product_theme::light_gold_rgb);
        emerald_chrome::gradient_stop(gold,.65,product_theme::dark_gold_rgb);emerald_chrome::gradient_stop(gold,1,product_theme::gold_rgb);
        cairo_set_source(c,gold);cairo_set_line_width(c,3);cairo_stroke(c);cairo_pattern_destroy(gold);
        if(dark){cairo_save(c);cairo_translate(c,.4,.4);path(c,symbol);emerald_chrome::stroke(c,product_theme::dark_gold_rgb,.6,.65);cairo_restore(c);}
    })).first->second;
}
}
