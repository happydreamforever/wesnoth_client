// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "alliance_game/court_map.hpp"
#include <chrono>
#include <map>
#include <optional>
#include <vector>

namespace alliance_game::court_mobile {
using Point = court_map::Point;
using Camera = court_map::Camera;
using Clock = std::chrono::steady_clock;
// Device and pointer IDs are independent. No event coordinates are converted
// here: the engine pump has already converted SDL touch to renderer space.
class Gesture {
    using Key=std::pair<std::uint64_t,std::uint64_t>;
    struct Finger {Point position,origin;};
    std::map<Key,Finger> fingers_;
    std::vector<Key> order_;
    bool moved_=false,multiple_=false;
    double slop_=8;
    Point velocity_{};
    Clock::time_point last_{},released_{};
    static double length(Point p){return std::hypot(p.x,p.y);}
    std::pair<Point,double> pair() const {
        auto a=fingers_.find(order_[0]),b=fingers_.find(order_[1]);
        return {{(a->second.position.x+b->second.position.x)/2,(a->second.position.y+b->second.position.y)/2},
            length({a->second.position.x-b->second.position.x,a->second.position.y-b->second.position.y})};
    }
public:
    bool active()const{return !fingers_.empty();}
    void density(double scale){slop_=8*std::max(1.,scale);}
    void stop(){velocity_={};}
    void cancel(){fingers_.clear();order_.clear();moved_=multiple_=true;stop();}
    void down(std::uint64_t device,std::uint64_t id,Point p,Clock::time_point now=Clock::now()){
        stop();if(fingers_.empty()){moved_=multiple_=false;last_=now;}
        const Key key{device,id};if(fingers_.emplace(key,Finger{p,p}).second)order_.push_back(key);if(fingers_.size()>1)multiple_=true;last_=now;
    }
    bool move(std::uint64_t device,std::uint64_t id,Point p,Camera& camera,Clock::time_point now=Clock::now()){
        auto it=fingers_.find({device,id});if(it==fingers_.end())return false;
        const auto old=camera;const auto dt=std::chrono::duration<double>(now-last_).count();
        if(fingers_.size()>1){auto before=pair();it->second.position=p;auto after=pair();
            const auto anchor=camera.inverse(before.first);
            if(before.second>slop_/2&&after.second>slop_/2)camera.zoom=std::clamp(camera.zoom*after.second/before.second,1.,8.);
            const auto next=camera.inverse(after.first);camera.cx+=anchor.x-next.x;camera.cy+=anchor.y-next.y;camera.clamp();
            moved_=true;stop();
        }else{auto& f=it->second;Point delta{p.x-f.position.x,p.y-f.position.y};
            if(!moved_&&length({p.x-f.origin.x,p.y-f.origin.y})>slop_)moved_=true;
            f.position=p;if(moved_){camera.pan(delta);if(dt>.001&&dt<.12){const double speed=std::max(1.,length(delta)/dt/2200.);velocity_={delta.x/dt/speed,delta.y/dt/speed};}else stop();}}
        last_=now;return old.cx!=camera.cx||old.cy!=camera.cy||old.zoom!=camera.zoom;
    }
    std::optional<Point> up(std::uint64_t device,std::uint64_t id,Clock::time_point now=Clock::now()){
        auto it=fingers_.find({device,id});if(it==fingers_.end())return {};
        const auto p=it->second.position;bool tap=!moved_&&!multiple_&&fingers_.size()==1;
        order_.erase(std::remove(order_.begin(),order_.end(),it->first),order_.end());fingers_.erase(it);if(!fingers_.empty()){last_=now;stop();}
        else{released_=now;if(tap||std::chrono::duration<double>(now-last_).count()>.08)stop();last_=now;}
        return tap?std::optional<Point>(p):std::nullopt;
    }
    bool tick(Camera& camera,Clock::time_point now=Clock::now()){
        if(active()||length(velocity_)<40||now-released_>std::chrono::milliseconds(350)){stop();return false;}
        double dt=std::clamp(std::chrono::duration<double>(now-last_).count(),0.,.05);last_=now;
        auto old=camera;camera.pan({velocity_.x*dt,velocity_.y*dt});double decay=std::exp(-dt/.11);
        velocity_.x*=decay;velocity_.y*=decay;if(old.cx==camera.cx&&old.cy==camera.cy)stop();
        return old.cx!=camera.cx||old.cy!=camera.cy;
    }
};
}
