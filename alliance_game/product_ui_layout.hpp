// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "config.hpp"
#include "alliance_game/product_theme.hpp"
#include "alliance_game/menu_icons.hpp"
#include "draw.hpp"
#include "picture.hpp"
#include "video.hpp"
#include <cmath>
#include "gui/core/gui_definition.hpp"
#include <array>
#include <string>
#include "serialization/base64.hpp"
#include <cstdlib>
#include <fstream>
#include <map>
#include <stdexcept>

namespace alliance_game::product_ui {
inline constexpr auto gold=product_theme::gold_rgba;
inline constexpr auto ivory=product_theme::ivory_rgba;
inline constexpr auto stone=product_theme::stone_rgba;
inline constexpr auto teal=product_theme::teal_shadow_rgba;
// APK overlay art lives outside the immutable gamedata tree. Native locators
// accept data URIs, while their ordinary binary lookup rejects absolute paths.
inline std::string artwork(const std::string& path) {
 if(path.compare(0,12,"alliance-ui/")!=0)return path;
 const auto relative=path.substr(12);const char* base=std::getenv("WESNOTH_UI_ASSETS");
 if(!base||!*base||relative.empty()||relative.front()=='/'||relative.find("..")!=std::string::npos||relative.find(':')!=std::string::npos)throw std::runtime_error("Interface artwork is unavailable.");
 const std::string file=std::string(base)+"/"+relative;static std::map<std::string,std::string> cache;
 const auto cached=cache.find(file);if(cached!=cache.end())return cached->second;
 std::ifstream stream(file,std::ios::binary|std::ios::ate);const auto size=stream.tellg();
 if(!stream||size<=0||size>8*1024*1024)throw std::runtime_error("Interface artwork is unavailable.");
 std::vector<std::uint8_t> bytes(static_cast<std::size_t>(size));stream.seekg(0);stream.read(reinterpret_cast<char*>(bytes.data()),bytes.size());
 if(!stream)throw std::runtime_error("Interface artwork could not be read.");
 const auto mime=relative.size()>5&&relative.substr(relative.size()-5)==".webp"?"image/webp":"image/png";
 return cache.emplace(file,std::string("data:")+mime+";base64,"+base64::encode(bytes)).first->second;
}
inline std::string background_art() { return artwork(product_theme::background_asset); }
inline void background_canvas(config& canvas,bool shaded=false) {
 auto& image=canvas.add_child("image");image["name"]=background_art();image["resize_mode"]="scale";
 image["w"]="(ceil(image_original_width * max(width * 1.0 / image_original_width, height * 1.0 / image_original_height)))";
 image["h"]="(ceil(image_original_height * max(width * 1.0 / image_original_width, height * 1.0 / image_original_height)))";
 image["x"]="((width - image_width) / 2)";image["y"]="((height - image_height) / 2)";
 if(shaded){auto& veil=canvas.add_child("rectangle");veil["w"]="(width)";veil["h"]="(height)";veil["fill_color"]="24,45,48,70";}
}
inline void draw_backdrop(const rect& region) {
 const auto screen=video::game_canvas();const auto clip=draw::reduce_clip(region);
 draw::fill(region,color_t{81,71,55,255});
 const auto image=image::get_texture(image::locator(background_art()),image::scale_quality::linear);
 if(!image||image.w()<=0||image.h()<=0)return;
 const double scale=std::max(double(screen.w)/image.w(),double(screen.h)/image.h());
 const int width=static_cast<int>(std::ceil(image.w()*scale)),height=static_cast<int>(std::ceil(image.h()*scale));
 draw::blit(image,rect{screen.x+(screen.w-width)/2,screen.y+(screen.h-height)/2,width,height});
 draw::fill(region,color_t{24,45,48,70});
}
inline config& row(config& grid, int grow=0) { auto& r=grid.add_child("row");r["grow_factor"]=grow;return r; }
inline config& cell(config& r,const std::string& type,const std::string& id="",int grow=1,int padding=6) {
 auto& c=r.add_child("column");c["grow_factor"]=grow;c["horizontal_grow"]=true;c["vertical_grow"]=true;
 c["border"]="all";c["border_size"]=padding;auto& w=c.add_child(type);
 if(type!="grid"){w["id"]=id;w["definition"]=type=="button"?"alliance_button":type=="text_box"?"alliance_input":type=="progress_bar"?"alliance_progress":type=="toggle_button"?"alliance_toggle":type=="minimap"?"alliance_map":type=="label"?"alliance_text":"default";}return w;
}
inline config& text(config& grid,const std::string& id,const std::string& value="",const char* style="alliance_text") {
 auto& w=cell(row(grid),"label",id);w["definition"]=style;w["label"]=value;w["wrap"]=true;w["use_markup"]=false;return w;
}
inline void rectangle(config& draw,const char* fill,const char* border="0,0,0,0",int inset=0) {
 auto& r=draw.add_child("rectangle");r["x"]=inset;r["y"]=inset;
 r["w"]="(width - "+std::to_string(inset*2)+")";r["h"]="(height - "+std::to_string(inset*2)+")";
 r["fill_color"]=fill;r["border_color"]=border;r["border_thickness"]=1;
}
inline void caption(config& draw,int size,const char* color,bool centered=false,bool tile=false) {
 auto& t=draw.add_child("text");t["x"]=centered?"((width - text_width) / 2)":"0";
 t["y"]=tile?"(height - 42)":centered?"((height - text_height) / 2)":"0";
 t["w"]=centered?"(text_width)":"(width)";t["h"]="(text_height)";t["maximum_width"]="(width)";
 t["font_size"]=size;if(size>=30)t["font_family"]="script";t["color"]=color;t["text"]="(text)";t["text_markup"]="(text_markup)";
}
inline void button_style(const std::string& id,const char* widget="button") {
 config c;c["id"]=id;c["description"]="Ancient fantasy action";auto& r=c.add_child("resolution");
 r["min_width"]=48;r["default_width"]=id=="alliance_close"?48:160;r["min_height"]=48;
 r["default_height"]=48;r["text_extra_width"]=28;r["text_extra_height"]=20;r["text_font_size"]=20;
 const std::array<const char*,4> states{{"state_enabled","state_disabled","state_pressed","state_focused"}};
 for(std::size_t i=0;i<states.size();++i){auto& d=r.add_child(states[i]).add_child("draw");
  rectangle(d,i==2?teal:i==3?"111,93,61,255":stone,i==3?ivory:gold);
  caption(d,20,i==1?"166,158,139,255":ivory,true);}
 gui2::add_single_widget_definition(widget,id,c);
}
inline void prepare_theme() {
 if(gui2::current_gui->second.widget_types["button"].count("alliance_button"))return;
 button_style("alliance_button");button_style("alliance_close");
 {
  config field;field["id"]="alliance_input";field["description"]="Readable touch input";auto& r=field.add_child("resolution");
  r["min_width"]=48;r["default_width"]=200;r["min_height"]=48;r["default_height"]=48;r["max_height"]=48;
  r["text_font_size"]=20;r["text_x_offset"]=8;r["text_y_offset"]="((height - text_font_height) / 2)";r["text_extra_width"]=16;
  for(const auto* state:{"state_enabled","state_disabled","state_focused","state_hovered"}) {
   auto& draw=r.add_child(state).add_child("draw");rectangle(draw,"58,57,47,255",gold);
   auto& selection=draw.add_child("rectangle");selection["x"]="(text_x_offset + selection_offset)";selection["y"]="(text_y_offset)";selection["w"]="(selection_width)";selection["h"]="(text_font_height)";selection["fill_color"]="91,116,110,255";
   auto& text=draw.add_child("text");text["x"]="(text_x_offset)";text["y"]="(text_y_offset)";text["w"]="(text_width)";text["h"]="(text_height)";text["maximum_width"]="(text_maximum_width)";text["font_size"]=20;text["color"]=ivory;text["text"]="(if(text = '' and hint_text != '', hint_text, text))";
   if(std::string(state)=="state_focused"){auto& cursor=draw.add_child("line");cursor["x1"]="(cursor_offset + 8)";cursor["x2"]="(cursor_offset + 8)";cursor["y1"]="(text_y_offset + 2)";cursor["y2"]="(text_y_offset + text_font_height - 2)";cursor["color"]="([255,255,255,cursor_alpha])";cursor["thickness"]=1;}
  }
  gui2::add_single_widget_definition("text_box","alliance_input",field);
 }
 {
  config c;c["id"]="alliance_progress";c["description"]="Hero experience";auto& r=c.add_child("resolution");r["min_width"]=30;r["default_width"]=100;r["min_height"]=8;r["default_height"]=10;r["max_height"]=10;
  auto& draw=r.add_child("state_enabled").add_child("draw");rectangle(draw,teal,gold);auto& fill=draw.add_child("rectangle");fill["x"]=1;fill["y"]=1;fill["w"]="(((width - 2) * percentage) / 100)";fill["h"]="(height - 2)";fill["fill_color"]=gold;gui2::add_single_widget_definition("progress_bar","alliance_progress",c);
 }
 {
  config c;c["id"]="alliance_toggle";c["description"]="Touch checkbox";auto& r=c.add_child("resolution");r["min_width"]=48;r["min_height"]=48;r["default_height"]=48;r["max_height"]=48;r["text_font_size"]=18;r["text_extra_width"]=32;
  for(int selected=0;selected<2;++selected){auto& state=r.add_child("state");for(const auto* mode:{"enabled","disabled","focused"}){auto& draw=state.add_child(mode).add_child("draw");
   auto& image=draw.add_child("image");image["x"]=4;image["y"]="((height - image_height) / 2)";image["name"]=std::string(selected?"buttons/checkbox-pressed.png":"buttons/checkbox.png")+(std::string(mode)=="disabled"?"~GS()~CS(12,6,-6)":"~GS()~CS(55,30,-15)");
   auto& text=draw.add_child("text");text["x"]=32;text["y"]="((height - text_height) / 2)";text["w"]="(width - 32)";text["h"]="(text_height)";text["font_size"]=18;text["text"]="(text)";text["color"]=ivory;
  }}gui2::add_single_widget_definition("toggle_button","alliance_toggle",c);
 }
 {
  config c;c["id"]="alliance_eye";c["description"]="Show password";auto& r=c.add_child("resolution");r["min_width"]=48;r["default_width"]=48;r["min_height"]=48;r["default_height"]=48;r["max_height"]=48;
  for(int selected=0;selected<2;++selected){auto& state=r.add_child("state");for(const auto* mode:{"enabled","disabled","focused"}){auto& draw=state.add_child(mode).add_child("draw");rectangle(draw,selected?teal:stone,gold);auto& image=draw.add_child("image");image["x"]="((width - 32) / 2)";image["y"]=8;image["w"]=32;image["h"]=32;image["name"]=menu_icons::image(selected?menu_icons::Symbol::visibility:menu_icons::Symbol::visibility_hidden,32,product_theme::gold_rgb);}}
  gui2::add_single_widget_definition("toggle_button","alliance_eye",c);
 }
 {
  config c;c["id"]="alliance_map";c["description"]="Battle map preview";auto& r=c.add_child("resolution");r["min_width"]=220;r["default_width"]=300;r["min_height"]=150;r["default_height"]=150;r.add_child("state_enabled").add_child("draw");gui2::add_single_widget_definition("minimap","alliance_map",c);
 }
 button_style("alliance_menu","menu_button");
 for(const auto& spec:std::array<std::pair<const char*,int>,3>{{{"alliance_text",20},{"alliance_caption",16},{"alliance_title",30}}}){
  config c;c["id"]=spec.first;c["description"]="Readable fantasy text";auto& r=c.add_child("resolution");r["text_font_size"]=spec.second;if(spec.second>=30)r["text_font_family"]="script";
  for(const auto* state:{"state_enabled","state_disabled"})caption(r.add_child(state).add_child("draw"),spec.second,ivory);
  gui2::add_single_widget_definition("label",spec.first,c);
 }
 config w;w["id"]="alliance_dark";w["description"]="Ancient fantasy workspace";auto& r=w.add_child("resolution");
 r["left_border"]=18;r["right_border"]=18;r["top_border"]=14;r["bottom_border"]=14;
 rectangle(r.add_child("background").add_child("draw"),stone,gold);
 r.add_child("foreground").add_child("draw");cell(row(r.add_child("grid"),1),"grid","_window_content_grid",1,0);
 // Grid IDs are needed by the window content substitution, unlike ordinary layout grids.
 r.mandatory_child("grid").mandatory_child("row").mandatory_child("column").mandatory_child("grid")["id"]="_window_content_grid";
 gui2::add_single_widget_definition("window","alliance_dark",w);
}
inline config& window(config& definition,const std::string& id,bool compact=false) {
 prepare_theme();definition["id"]=id;definition["description"]="Wesnoth";auto& r=definition.add_child("resolution");r["definition"]="alliance_dark";
 r["automatic_placement"]=false;r["width"]=compact?"(min(screen_width - 40, 620))":"(min(screen_width - 32, 1440))";
 r["height"]=compact?"(min(screen_height - 32, 650))":"(screen_height - 32)";
 r["x"]=compact?"((screen_width - min(screen_width - 40, 620)) / 2)":"((screen_width - min(screen_width - 32, 1440)) / 2)";
 r["y"]=compact?"((screen_height - min(screen_height - 32, 650)) / 2)":"16";
 r.add_child("tooltip")["id"]="tooltip";r.add_child("helptip")["id"]="tooltip";return r.add_child("grid");
}
}
