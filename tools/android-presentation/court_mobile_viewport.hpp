// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "court_mobile_gesture.hpp"
#include "court_mobile_camera.hpp"
#include "alliance_game/product_ui_layout.hpp"
#include "alliance_game/menu_icons.hpp"
#include "gui/widgets/drawing.hpp"
#include "font/text.hpp"
#include "picture.hpp"
#include "draw.hpp"
#include "color.hpp"
#include "sdl/surface.hpp"
#include "preferences/preferences.hpp"
#include <array>
#include <functional>
#include <memory>

namespace alliance_game::court_mobile {
inline double scale(){auto s=video::game_canvas_size();return std::clamp(std::min(s.x/1280.,s.y/720.),1.,2.);}
// Owned Cairo -> SDL conversion. This never serializes a PNG or inserts a
// unique data URI into the engine's global texture cache.
inline texture raster(int width,int height,const std::function<void(cairo_t*)>& paint){
    std::unique_ptr<cairo_surface_t,decltype(&cairo_surface_destroy)> pixels(cairo_image_surface_create(CAIRO_FORMAT_ARGB32,width,height),cairo_surface_destroy);
    std::unique_ptr<cairo_t,decltype(&cairo_destroy)> c(cairo_create(pixels.get()),cairo_destroy);paint(c.get());cairo_surface_flush(pixels.get());
    surface out(width,height);auto* bytes=static_cast<std::uint8_t*>(out->pixels);auto* input=cairo_image_surface_get_data(pixels.get());int stride=cairo_image_surface_get_stride(pixels.get());
    for(int y=0;y<height;++y)for(int x=0;x<width;++x){std::uint32_t v;std::memcpy(&v,input+y*stride+x*4,4);unsigned a=v>>24;auto straight=[a](unsigned n){return a?std::min(255u,(n*255+a/2)/a):0u;};
        v=(a<<24)|(straight((v>>16)&255)<<16)|(straight((v>>8)&255)<<8)|straight(v&255);std::memcpy(bytes+y*out->pitch+x*4,&v,4);}
    return texture(out,true);
}
struct Marker {
    const court_map::Node* node=nullptr;std::string id,name,status,role;bool enabled=false;
    bool operator==(const Marker& b)const{return node==b.node&&id==b.id&&name==b.name&&status==b.status&&role==b.role&&enabled==b.enabled;}
};
struct Viewport;
#ifdef COURT_MOBILE_FRAME_PROBE
void court_mobile_frame_probe(const Viewport&);
#endif
struct Viewport {
    Camera* camera=nullptr;bool* camera_initialized=nullptr;std::vector<Marker> markers;std::string selected,area,bracket;
    bool dirty=true;std::uint64_t frames=0,texture_builds=0;int w=0,h=0;
    texture overview,detail,fog,label;std::array<texture,6> symbols;
    std::string label_text;double label_scale=0;
    Clock::time_point fade_until{};
    void invalidate(){dirty=true;}
    void load(const std::string& key){
        if(area==key&&overview)return;area=key;bracket.clear();fog={};markers.clear();selected.clear();label={};label_text.clear();
        const auto name=key.empty()?"world":"area";
        overview=image::get_texture(product_ui::artwork(std::string("alliance-ui/court/")+name+"-overview.png"));
        detail=image::get_texture(product_ui::artwork(std::string("alliance-ui/court/")+name+".png"));
        if(!overview)overview=detail;dirty=true;
    }
    void set_markers(std::vector<Marker> next){
        if(markers==next)return;
        bool fog_changed=markers.size()!=next.size();
        for(std::size_t i=0;!fog_changed&&i<next.size();++i)fog_changed=markers[i].id!=next[i].id||markers[i].enabled!=next[i].enabled;
        markers=std::move(next);dirty=true;if(!fog_changed&&fog)return;
        const int width=1024,height=area.empty()?683:576;
        fog=raster(width,height,[&](cairo_t* c){
            // One static mist texture covers the inactive geography, including
            // the spaces between its sparse authored hex polygons.
            cairo_set_source_rgba(c,.32,.43,.39,.68);cairo_paint(c);
            for(int n=0;n<12;++n){double x=width*(.07+n*.083),y=height*(.25+.45*std::sin(n*1.73));
                auto* cloud=cairo_pattern_create_radial(x,y,1,x,y,width*.23);
                cairo_pattern_add_color_stop_rgba(cloud,0,.72,.79,.73,.18);cairo_pattern_add_color_stop_rgba(cloud,1,.72,.79,.73,0);
                cairo_set_source(c,cloud);cairo_paint(c);cairo_pattern_destroy(cloud);}
            cairo_set_operator(c,CAIRO_OPERATOR_DEST_OUT);
            for(const auto& m:markers)if(m.enabled){
                double radius=0;for(const auto& p:m.node->polygon)radius=std::max(radius,std::hypot((p.x-m.node->x)*width,(p.y-m.node->y)*height));
                auto* clear=cairo_pattern_create_radial(m.node->x*width,m.node->y*height,radius*.65,m.node->x*width,m.node->y*height,radius*1.65);
                cairo_pattern_add_color_stop_rgba(clear,0,0,0,0,1);cairo_pattern_add_color_stop_rgba(clear,1,0,0,0,0);
                cairo_set_source(c,clear);cairo_paint(c);cairo_pattern_destroy(clear);
            }});++texture_builds;
    }
    void set_selection(std::string id){if(selected!=id){selected=std::move(id);dirty=true;}}
    void prepare_symbols(){if(symbols[0])return;
        for(int n=0;n<6;++n){symbols[n]=raster(96,96,[n](cairo_t* c){
            cairo_set_source_rgba(c,.025,.15,.11,.96);cairo_arc(c,48,48,43,0,6.2831853);cairo_fill_preserve(c);
            cairo_set_source_rgb(c,.88,.73,.42);cairo_set_line_width(c,3);cairo_stroke(c);
            cairo_translate(c,22,22);cairo_scale(c,.52,.52);cairo_set_line_width(c,5);cairo_set_line_cap(c,CAIRO_LINE_CAP_ROUND);cairo_set_line_join(c,CAIRO_LINE_JOIN_ROUND);
            if(n==0)menu_icons::path(c,menu_icons::Symbol::team);
            else if(n==1)menu_icons::path(c,menu_icons::Symbol::achievements);
            else if(n==2)menu_icons::path(c,menu_icons::Symbol::room);
            else if(n==3){cairo_move_to(c,18,15);cairo_line_to(c,82,15);cairo_line_to(c,76,60);cairo_line_to(c,50,89);cairo_line_to(c,24,60);cairo_close_path(c);}
            else if(n==4){cairo_rectangle(c,22,42,56,43);cairo_move_to(c,33,42);cairo_line_to(c,33,29);cairo_arc(c,50,29,17,3.14159265,6.2831853);cairo_line_to(c,67,42);}
            else {cairo_move_to(c,12,85);cairo_line_to(c,12,20);cairo_line_to(c,32,20);cairo_line_to(c,32,40);cairo_line_to(c,68,40);cairo_line_to(c,68,20);cairo_line_to(c,88,20);cairo_line_to(c,88,85);cairo_close_path(c);cairo_rectangle(c,42,60,16,25);}
            cairo_stroke(c);
        });++texture_builds;}
    }
    void prepare_label(){
        const auto it=std::find_if(markers.begin(),markers.end(),[&](const auto& m){return m.id==selected;});
        const auto name=it==markers.end()?std::string{}:it->name;const auto s=scale();
        if(name==label_text&&label_scale==s)return;label_text=name;label_scale=s;label={};
        if(!name.empty()){font::pango_text text;text.set_text(name,false);text.set_font_size(int(19*s));text.set_foreground_color(color_t{248,232,196});text.set_maximum_width(int(290*s));text.set_ellipse_mode(PANGO_ELLIPSIZE_END);label=text.render_and_get_texture();++texture_builds;}
    }
    // Returns candidate IDs, ordered by distance. No mandatory zoom or action.
    std::vector<std::string> hit(Point p)const{
        std::vector<std::pair<double,std::string>> hits;const double radius=24*scale();
        for(const auto& m:markers){auto q=camera->project({m.node->x,m.node->y});double d=std::hypot(p.x-q.x,p.y-q.y);if(d<=radius)hits.emplace_back(d,m.id);}
        std::sort(hits.begin(),hits.end());std::vector<std::string> ids;
        if(!hits.empty()){for(const auto& hit:hits)if(hit.first<=hits.front().first+4*scale())ids.push_back(hit.second);return ids;}
        const auto q=camera->inverse(p);for(const auto& m:markers)if(court_map::contains(*m.node,q))return {m.id};return {};
    }
    void draw(int width,int height){
        if(!camera||width<1||height<1)return;w=width;h=height;
        resize_camera(*camera,w,h,bracket.empty()?(area.empty()?1.5:1672./941.):camera->aspect,camera_initialized&&*camera_initialized);
        if(camera_initialized&&!*camera_initialized){fit_width(*camera);*camera_initialized=true;}
        auto clip=::draw::reduce_clip({0,0,w,h});::draw::fill(rect{0,0,w,h},color_t{18,43,33});auto a=camera->project({0,0}),b=camera->project({1,1});rect dest{int(a.x),int(a.y),int(b.x-a.x),int(b.y-a.y)};
        const texture& art=dest.w<=1024&&dest.h<=1024?overview:detail;if(art)::draw::blit(art,dest);if(fog&&bracket.empty())::draw::blit(fog,dest);
        const int size=int(40*scale());std::optional<Point> selection_position;
        for(const auto& m:markers){auto p=camera->project({m.node->x,m.node->y});if(p.x<-size||p.y<-size||p.x>w+size||p.y>h+size)continue;
            if(!m.enabled&&m.id!=selected){const int dim=int(18*scale());::draw::blit(symbols[4],{int(p.x)-dim/2,int(p.y)-dim/2,dim,dim});}
            if(m.enabled||m.id==selected){int n=!m.enabled?4:m.status=="fighting"||m.status=="response"||m.status=="queued"||m.status=="preparing"||m.status=="paused"?2:m.status=="protected"?3:m.role=="competition"?1:m.role=="plan"||area.empty()?0:5;
                ::draw::blit(symbols[n],{int(p.x)-size/2,int(p.y)-size/2,size,size});}
            if(m.id==selected)selection_position=p;
        }
        // Selection stays above every marker, including dense inactive clusters.
        if(selection_position){const auto p=*selection_position;
            ::draw::circle(int(p.x),int(p.y),size/2+3,color_t{255,242,192});::draw::circle(int(p.x),int(p.y),size/2+4,color_t{255,242,192});
            if(label){int x=std::clamp(int(p.x)-label.w()/2,4,std::max(4,w-label.w()-4));int y=std::clamp(int(p.y)+size/2+6,4,std::max(4,h-label.h()-4));::draw::fill(rect{x-4,y-2,label.w()+8,label.h()+4},color_t{8,34,23,240});::draw::blit(label,{x,y,label.w(),label.h()});}
        }
        const auto now=Clock::now();if(now<fade_until){auto opacity=int(90*std::chrono::duration<double>(fade_until-now).count()/.15);::draw::fill(rect{0,0,w,h},color_t{15,42,31,std::uint8_t(std::clamp(opacity,0,90))});}
        ++frames;
#ifdef COURT_MOBILE_FRAME_PROBE
        court_mobile_frame_probe(*this);
#endif
    }
};
class MapShape final:public gui2::canvas::shape {
    std::shared_ptr<Viewport> view_;
public:explicit MapShape(std::shared_ptr<Viewport> view):view_(std::move(view)){}
    void draw(wfl::map_formula_callable& vars)override{view_->draw(vars.query_value("width").as_int(),vars.query_value("height").as_int());}
};
}
