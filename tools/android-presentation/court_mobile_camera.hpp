// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "alliance_game/court_map.hpp"

namespace alliance_game::court_mobile {
// Camera zoom is relative to the fitted image. Fit only on a map's first
// rendered frame, after its real viewport has been allocated.
inline void fit_width(court_map::Camera& camera) {
    const double fitted=std::min(camera.width/camera.aspect,camera.height);
    camera.zoom=std::clamp(camera.width/(fitted*camera.aspect),1.,8.);
    camera.cx=camera.cy=.5;camera.clamp();
}
// Opening details changes viewport height. Preserve the existing physical
// scale and focal point instead of making the map shrink behind the panel.
inline void resize_camera(court_map::Camera& camera,double width,double height,double aspect,bool preserve_scale) {
    if(camera.width==width&&camera.height==height&&camera.aspect==aspect)return;
    const double previous=camera.scale();
    camera.width=std::max(1.,width);camera.height=std::max(1.,height);camera.aspect=aspect;
    if(preserve_scale)camera.zoom=std::clamp(previous/std::min(camera.width/aspect,camera.height),1.,8.);
    camera.clamp();
}
}
