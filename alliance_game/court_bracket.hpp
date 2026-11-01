// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "alliance_game/court_common.hpp"
#include "alliance_game/emerald_chrome.hpp"
#include <cmath>
#include <map>
namespace alliance_game::court_ui {
struct BracketArt {std::string image;int width=1,height=1;};
inline BracketArt bracket_art(const json::object& competition){
    const auto& rounds=array(competition,"rounds");if(rounds.empty())return {};
    static std::map<std::string,BracketArt> cache;const auto key=json::serialize(competition);if(auto found=cache.find(key);found!=cache.end())return found->second;
    const int count=std::max<int>(1,array(rounds.front().as_object(),"matches").size());BracketArt art;art.width=std::max(460,int(rounds.size())*230+40);art.height=std::max(300,count*116+80);
    std::map<std::string,std::string> names;for(const auto& entry:array(competition,"entries"))if(entry.is_object())names[text(entry.as_object(),"team_id")]=text(entry.as_object(),"team_name");
    art.image=emerald_chrome::raster(art.width,art.height,1,[&](cairo_t* c){
        cairo_set_source_rgb(c,.035,.13,.095);cairo_paint(c);cairo_set_line_width(c,2);cairo_select_font_face(c,"Lato",CAIRO_FONT_SLANT_NORMAL,CAIRO_FONT_WEIGHT_NORMAL);
        auto label=[&](std::string value,double x,double y,int size,int max_width){cairo_set_font_size(c,size);cairo_text_extents_t ext{};cairo_text_extents(c,value.c_str(),&ext);while(ext.width>max_width&&value.size()>4){auto last=value.size()-1;while(last>0&&(static_cast<unsigned char>(value[last])&0xc0)==0x80)--last;value.resize(last);cairo_text_extents(c,(value+"…").c_str(),&ext);}if(ext.width>max_width-15)value+="…";cairo_move_to(c,x,y);cairo_show_text(c,value.c_str());};
        for(std::size_t r=0;r<rounds.size();++r){if(!rounds[r].is_object())continue;const auto& round=rounds[r].as_object();const double x=20+r*230,spacing=116*std::pow(2.,r);cairo_set_source_rgb(c,.91,.76,.43);label("Round "+std::to_string(number(round,"number",r+1)),x,30,20,200);const auto& matches=array(round,"matches");
            for(std::size_t m=0;m<matches.size();++m){if(!matches[m].is_object())continue;const auto& match=matches[m].as_object();const double y=50+(m+.5)*spacing-40;
                cairo_set_source_rgb(c,.075,.22,.16);cairo_rectangle(c,x,y,196,80);cairo_fill_preserve(c);cairo_set_source_rgb(c,.78,.63,.35);cairo_stroke(c);
                const auto& ids=array(match,"team_ids");const auto winner=text(match,"winner_team_id");for(int side=0;side<2;++side){std::string id=side<int(ids.size())&&ids[side].is_string()?std::string(ids[side].as_string()):"";auto found=names.find(id);cairo_set_source_rgb(c,id==winner&&!id.empty()?.98:.90,id==winner&&!id.empty()?.83:.88,.66);label(found==names.end()?(flag(match,"bye")&&side==1?"Bye":"To be decided"):found->second,x+10,y+25+side*24,17,176);}
                cairo_set_source_rgb(c,.72,.79,.70);label(title(text(match,"state")),x+10,y+70,13,176);
                if(r+1<rounds.size()){const double next_y=50+(std::floor(m/2.)+.5)*spacing*2;const double middle=x+212;cairo_set_source_rgb(c,.72,.60,.34);cairo_move_to(c,x+196,y+40);cairo_line_to(c,middle,y+40);cairo_line_to(c,middle,next_y);cairo_line_to(c,x+230,next_y);cairo_stroke(c);}
            }
        }
    });if(cache.size()>=6)cache.erase(cache.begin());cache.emplace(key,art);return art;
}
}
