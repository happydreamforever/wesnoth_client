// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <map>
#include <optional>
#include <string>
namespace alliance_game::court_map {
struct Point {double x=0,y=0;};
struct Camera {
    double zoom=1, cx=.5,cy=.5,width=1,height=1,aspect=1.5;
    double scale() const {return std::min(width/aspect,height)*zoom;}
    Point project(Point p) const {const auto s=scale();return {width/2+(p.x-cx)*s*aspect,height/2+(p.y-cy)*s};}
    Point inverse(Point p) const {const auto s=std::max(.001,scale());return {cx+(p.x-width/2)/(s*aspect),cy+(p.y-height/2)/s};}
    void clamp(){const auto s=std::max(.001,scale());const double hx=std::min(.5,width/(2*s*aspect)),hy=std::min(.5,height/(2*s));cx=std::clamp(cx,hx,1-hx);cy=std::clamp(cy,hy,1-hy);}
    void resize(double w,double h,double a){width=std::max(1.,w);height=std::max(1.,h);aspect=a;clamp();}
    void pan(Point delta){const auto s=std::max(.001,scale());cx-=delta.x/(s*aspect);cy-=delta.y/s;clamp();}
    void zoom_at(double factor,Point anchor){const auto before=inverse(anchor);zoom=std::clamp(zoom*factor,1.,8.);const auto after=inverse(anchor);cx+=before.x-after.x;cy+=before.y-after.y;clamp();}
    void centre(){zoom=1;cx=cy=.5;}
};
// Raw finger events are handled independently of SDL's legacy multigesture
// event. A second finger permanently cancels that gesture's pending tap.
class Gesture {
    std::map<std::uint64_t,Point> fingers_;Point origin_{};bool moved_=false,multiple_=false;
    static double distance(Point a,Point b){return std::hypot(a.x-b.x,a.y-b.y);}
    std::pair<Point,double> pair()const{auto a=fingers_.begin(),b=std::next(a);return {{(a->second.x+b->second.x)/2,(a->second.y+b->second.y)/2},distance(a->second,b->second)};}
public:
    bool active()const{return !fingers_.empty();}
    void down(std::uint64_t id,Point p){if(fingers_.empty()){origin_=p;moved_=multiple_=false;}fingers_[id]=p;if(fingers_.size()>1)multiple_=true;}
    void move(std::uint64_t id,Point p,Camera& camera){auto it=fingers_.find(id);if(it==fingers_.end())return;
        if(fingers_.size()>1){const auto before=pair();it->second=p;const auto after=pair();camera.pan({after.first.x-before.first.x,after.first.y-before.first.y});if(before.second>4&&after.second>4)camera.zoom_at(after.second/before.second,after.first);moved_=true;}
        else{Point delta{p.x-it->second.x,p.y-it->second.y};it->second=p;if(distance(origin_,p)>8)moved_=true;if(moved_)camera.pan(delta);}}
    std::optional<Point> up(std::uint64_t id){const auto it=fingers_.find(id);if(it==fingers_.end())return {};const auto p=it->second;const bool tap=!moved_&&!multiple_&&fingers_.size()==1;fingers_.erase(it);return tap?std::optional<Point>(p):std::nullopt;}
    void cancel(){fingers_.clear();moved_=multiple_=true;}
};
}
#include "alliance_game/court_geometry.hpp"
namespace alliance_game::court_map {
inline bool contains(const Node& node,Point p){bool in=false;for(std::size_t i=0,j=5;i<6;j=i++){const auto a=node.polygon[i],b=node.polygon[j];if((a.y>p.y)!=(b.y>p.y)&&p.x<(b.x-a.x)*(p.y-a.y)/(b.y-a.y)+a.x)in=!in;}return in;}
inline std::string castle_id(const std::string& area,const Node& node){if(area.size()!=4||area[0]!='A')return {};return area+"-"+node.id;}
}
