// SPDX-License-Identifier: GPL-2.0-or-later
// Included in the existing court_ui namespace by the guarded Android adapter.
inline void Workspace::map(){
    mobile_layout();
    if(form_&&form_->operation=="create_plan"){
        std::string map_id="weldyn-channel",name="Weldyn Channel";
        for(const auto& f:form_->fields)if(f.key=="map_id"){map_id=f.value;for(const auto& o:f.options)if(o.first==map_id)name=o.second;}
        const char* file=map_id=="isars-cross"?"multiplayer/maps/4p_Isars_Cross.map":map_id=="hexcake"?"multiplayer/maps/6p_Hexcake.map":map_id=="mokena-prairie"?"multiplayer/maps/8p_Mokena_Prairie.map":"multiplayer/maps/2p_Weldyn_Channel.map";
        const auto path=filesystem::get_wml_location(file);find_widget<gui2::minimap>("court_tactical").set_map_data(path?filesystem::read_file(*path):std::string{});
        find_widget<gui2::label>("court_tactical_name").set_label(name);find_widget<gui2::stacked_widget>("court_body").select_layer(2);return;
    }
    const auto* card=selected();const bool bracket=state_.view=="competitions"&&card&&!array(card->value,"rounds").empty();
    const bool shown=mobile_geo()||(state_.map&&bracket);
    auto& stack=find_widget<gui2::stacked_widget>("court_body");stack.select_layer(shown?0:1);
    auto& toggle=find_widget<gui2::button>("court_view");toggle.set_visible(((state_.view.empty()&&state_.depth<2)||bracket)?gui2::widget::visibility::visible:gui2::widget::visibility::invisible);toggle.set_label(shown?"List":bracket?"Bracket":"Map");
    if(!shown)return;
    auto& widget=find_widget<gui2::drawing>("court_map");mobile_->camera=&active_camera();
    if(!mobile_attached_){std::vector<std::unique_ptr<gui2::canvas::shape>> shapes;shapes.emplace_back(std::make_unique<court_mobile::MapShape>(mobile_));widget.get_drawing_canvas().set_shapes(std::move(shapes));mobile_attached_=true;}
    if(bracket){const auto art=bracket_art(card->value);if(mobile_->bracket!=art.image){mobile_->overview=mobile_->detail=image::get_texture(art.image);mobile_->bracket=art.image;mobile_->markers.clear();mobile_->fog={};mobile_->camera->aspect=double(art.width)/art.height;mobile_->invalidate();}mobile_tick();return;}
    const auto next_area=state_.depth?state_.area:std::string{};
    const bool entering=state_.depth==1&&mobile_->area!=next_area;
    if(!mobile_->bracket.empty())mobile_->overview={};
    mobile_->load(next_area);mobile_->camera->aspect=state_.depth?1672./941.:1.5;
    std::vector<court_mobile::Marker> markers;
    auto append=[&](const court_map::Node& node){std::string id=state_.depth?court_map::castle_id(state_.area,node):node.id;const auto row=std::find_if(cards_.begin(),cards_.end(),[&](const auto& c){return c.id==id;});
        markers.push_back({&node,id,row!=cards_.end()?row->name:node.name,row!=cards_.end()?text(row->value,"state"):"",row!=cards_.end()?text(row->value,"role",text(row->value,"kind")):"",row!=cards_.end()&&flag(row->value,"enabled")});};
    if(state_.depth)for(const auto& n:court_map::castle_nodes)append(n);else for(const auto& n:court_map::world_nodes)append(n);
    mobile_->set_markers(std::move(markers));mobile_->set_selection(state_.selected);
    if(entering&&prefs::get().animate_map())mobile_->fade_until=court_mobile::Clock::now()+std::chrono::milliseconds(150);
    // Native Enter already plays the single preference-aware button cue.
    mobile_tick();
}
inline void Workspace::click_map(court_map::Point p){
    if(!mobile_geo())return;mobile_gesture_.stop();auto ids=mobile_->hit(p);if(ids.empty())return;
    if(ids.size()==1){mobile_choose(ids.front());return;}
    mobile_candidates_=std::move(ids);std::vector<std::string> names{"Choose location"};
    for(const auto& id:mobile_candidates_){const auto i=std::find_if(cards_.begin(),cards_.end(),[&](const auto& c){return c.id==id;});names.push_back(i==cards_.end()?id:i->name);}
    auto& choices_widget=find_widget<gui2::menu_button>("court_choices");updating_=true;choices_widget.set_values(choices(names),0);updating_=false;
    choices_widget.set_visible(gui2::widget::visibility::visible);hint("Choose a location.");
}
inline void Workspace::handle_event(const SDL_Event& event){
    if(event.type==SDL_EVENT_FINGER_CANCELED||event.type==SDL_EVENT_WINDOW_FOCUS_LOST||event.type==SDL_EVENT_WILL_ENTER_BACKGROUND){mobile_cancel();return;}
    if(!visible_||!state_.map||form_||(!state_.view.empty()&&state_.view!="competitions")||(state_.depth>1&&state_.view!="competitions"))return;
    auto& camera=active_camera();const auto box=find_widget<gui2::drawing>("court_map").get_rectangle();
    auto inside=[&](double x,double y){return x>=box.x&&y>=box.y&&x<box.x+box.w&&y<box.y+box.h;};
    if(event.type==SDL_EVENT_FINGER_DOWN||event.type==SDL_EVENT_FINGER_MOTION||event.type==SDL_EVENT_FINGER_UP){
        const auto& f=event.tfinger;const court_map::Point p{f.x-box.x,f.y-box.y};
        if(event.type==SDL_EVENT_FINGER_DOWN){if(inside(f.x,f.y))mobile_gesture_.down(f.touchID,f.fingerID,p);}
        else if(event.type==SDL_EVENT_FINGER_MOTION){if(mobile_gesture_.move(f.touchID,f.fingerID,p,camera))mobile_->invalidate();}
        else if(auto tap=mobile_gesture_.up(f.touchID,f.fingerID);tap&&inside(f.x,f.y))click_map(*tap);
        return;
    }
    if((event.type==SDL_EVENT_MOUSE_MOTION&&event.motion.which==SDL_TOUCH_MOUSEID)||((event.type==SDL_EVENT_MOUSE_BUTTON_DOWN||event.type==SDL_EVENT_MOUSE_BUTTON_UP)&&event.button.which==SDL_TOUCH_MOUSEID))return;
    if(event.type==SDL_EVENT_MOUSE_BUTTON_DOWN&&event.button.button==SDL_BUTTON_LEFT&&inside(event.button.x,event.button.y)){mobile_pointer_=true;mobile_gesture_.down(0,0,{event.button.x-box.x,event.button.y-box.y});}
    else if(event.type==SDL_EVENT_MOUSE_MOTION&&mobile_pointer_){if(mobile_gesture_.move(0,0,{event.motion.x-box.x,event.motion.y-box.y},camera))mobile_->invalidate();}
    else if(event.type==SDL_EVENT_MOUSE_BUTTON_UP&&mobile_pointer_&&event.button.button==SDL_BUTTON_LEFT){mobile_pointer_=false;if(auto tap=mobile_gesture_.up(0,0);tap&&inside(event.button.x,event.button.y))click_map(*tap);}
}
