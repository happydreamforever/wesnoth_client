// SPDX-License-Identifier: GPL-2.0-or-later
#include "tools/android-presentation/court_mobile_gesture.hpp"
#include <cassert>
#include <iostream>
using namespace alliance_game::court_mobile;
static bool near(double a,double b){return std::abs(a-b)<1e-8;}
int main(){
    Camera camera;camera.resize(1200,600,1.5);camera.zoom_at(3,{600,300});
    Gesture gesture;auto now=Clock::now();auto advance=[&]{now+=std::chrono::milliseconds(16);};
    gesture.down(1,10,{500,250},now);gesture.down(1,20,{700,350},now);
    const auto anchor=camera.inverse({600,300});advance();gesture.move(1,10,{460,230},camera,now);advance();gesture.move(1,20,{740,370},camera,now);
    auto after=camera.inverse({600,300});assert(near(anchor.x,after.x)&&near(anchor.y,after.y));assert(near(camera.zoom,4.2));
    // An extra pointer must not replace either member of the tracked pair.
    auto before=camera;gesture.down(1,1,{200,200},now);advance();assert(!gesture.move(1,1,{300,300},camera,now));assert(near(before.cx,camera.cx));
    assert(!gesture.up(1,1,now));assert(!gesture.up(1,10,now));
    before=camera;advance();assert(gesture.move(1,20,{750,380},camera,now));auto p=before.inverse({740,370}),q=camera.inverse({750,380});assert(near(p.x,q.x)&&near(p.y,q.y));
    assert(!gesture.up(1,20,now));advance();assert(gesture.tick(camera,now));
    gesture.down(2,1,{200,200},now);before=camera;advance();assert(!gesture.tick(camera,now));assert(near(camera.cx,before.cx));
    gesture.cancel();advance();assert(!gesture.move(2,1,{500,500},camera,now));assert(!gesture.up(2,1,now));assert(!gesture.tick(camera,now));
    // Same finger ID on different devices cannot collide.
    gesture.down(1,1,{500,250},now);gesture.down(2,1,{700,350},now);assert(!gesture.up(1,1,now));assert(gesture.active());assert(!gesture.up(2,1,now));
    gesture.down(1,5,{400,200},now);advance();gesture.move(1,5,{402,201},camera,now);assert(gesture.up(1,5,now));
    gesture.down(1,5,{400,200},now);advance();gesture.move(1,5,{450,200},camera,now);gesture.up(1,5,now);now+=std::chrono::seconds(1);assert(!gesture.tick(camera,now));
    for(int n=0;n<500;++n){gesture.cancel();gesture.down(1,5,{600,300},now);advance();gesture.move(1,5,{n%2?100000.:-100000.,300},camera,now);gesture.up(1,5,now);assert(camera.cx>=0&&camera.cx<=1&&camera.cy>=0&&camera.cy<=1);}
    std::cout<<"MOBILE GESTURE CHECK PASSED\n";
}
