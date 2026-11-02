// Included in the generated Workspace private section only.
    std::shared_ptr<court_mobile::Viewport> mobile_=std::make_shared<court_mobile::Viewport>();
    court_mobile::Gesture mobile_gesture_;
    bool mobile_attached_=false,mobile_expanded_=false,mobile_pointer_=false;
    bool mobile_list_dirty_=true,mobile_actions_top_=false;
    std::string mobile_layout_key_,mobile_scene_;
    std::vector<std::string> mobile_candidates_;
    bool mobile_geo()const{return state_.map&&state_.view.empty()&&state_.depth<2&&!form_;}
    bool mobile_can_run(std::size_t n)const{return n<actions_.size()&&(!job_||(!mutation_&&actions_[n].navigation));}
    void mobile_cancel(){mobile_gesture_.cancel();mobile_pointer_=false;mobile_candidates_.clear();if(visible_)find_widget<gui2::menu_button>("court_choices").set_visible(gui2::widget::visibility::invisible);}
    void mobile_layout(){
        const bool geographic=mobile_geo();
        const bool browser=!form_||form_->operation=="create_plan";
        const std::string key=std::to_string(browser)+":"+std::to_string(geographic)+":"+std::to_string(mobile_expanded_)+":"+state_.view+":"+(form_?form_->operation:"")+":"+team_id();
        if(key==mobile_layout_key_)return;mobile_layout_key_=key;
        auto visibility=[](bool show){return show?gui2::widget::visibility::visible:gui2::widget::visibility::invisible;};
        // Move the original controls, retaining their exact action callbacks
        // and guards. Forms keep their existing footer; maps use the top row.
        if(mobile_actions_top_!=geographic){
            auto& from=find_widget<gui2::grid>(mobile_actions_top_?"court_top_actions":"court_bottom_actions");
            auto& to=find_widget<gui2::grid>(geographic?"court_top_actions":"court_bottom_actions");
            auto placeholder=std::make_unique<gui2::grid>();placeholder->set_id("court_actions_placeholder");
            auto controls=from.swap_child("court_action_controls",std::move(placeholder),false);
            to.swap_child("court_actions_placeholder",std::move(controls),false);mobile_actions_top_=geographic;
        }
        find_widget<gui2::grid>("court_top_actions").set_visible(visibility(geographic));
        find_widget<gui2::grid>("court_bottom_actions").set_visible(visibility(!geographic));
        find_widget<gui2::label>("court_heading").set_visible(visibility(!geographic));
        find_widget<gui2::button>("court_team").set_visible(visibility(!(geographic&&state_.depth==0&&team_id().empty())));
        find_widget<gui2::text_box>("court_search").set_visible(visibility(!geographic));
        find_widget<gui2::button>("court_search_toggle").set_visible(visibility(geographic));
        find_widget<gui2::button>("court_secondary").set_visible(visibility(!geographic||(state_.depth==0&&team_id().empty())));
        find_widget<gui2::grid>("court_browser").set_visible(visibility(browser));
        find_widget<gui2::grid>("court_tools").set_visible(visibility(!form_));
        find_widget<gui2::grid>("court_zoom_controls").set_visible(visibility(geographic||state_.view=="competitions"));
        find_widget<gui2::button>("court_collapse").set_visible(visibility(geographic));
        find_widget<gui2::button>("court_collapse").set_label(mobile_expanded_?"Hide":"Details");
        find_widget<gui2::stacked_widget>("court_panel").set_visible(visibility(!geographic||mobile_expanded_));
        auto& layout=find_widget<gui2::grid>("court_content");layout.set_row_grow_factor(0,browser?1:0);layout.set_row_grow_factor(1,browser?0:1);
        invalidate_layout();
    }
    void mobile_tick(){
        if(!visible_)return;mobile_layout();
        const std::string scene=std::to_string(state_.depth)+":"+state_.area+":"+state_.view+":"+std::to_string(state_.map)+":"+std::to_string(bool(form_));
        if(scene!=mobile_scene_){mobile_cancel();mobile_scene_=scene;}
        if(!mobile_geo()&&state_.view!="competitions")return;
        auto& widget=find_widget<gui2::drawing>("court_map");auto box=widget.get_rectangle();
        if(box.w<1||box.h<1)return;
        auto& camera=active_camera();
        if(camera.width!=box.w||camera.height!=box.h){court_mobile::resize_camera(camera,box.w,box.h,camera.aspect,mobile_->camera_initialized&&*mobile_->camera_initialized);mobile_->invalidate();}
        mobile_gesture_.density(court_mobile::scale());
        if(mobile_gesture_.tick(camera))mobile_->invalidate();
        if(mobile_->fade_until!=court_mobile::Clock::time_point{}){mobile_->invalidate();if(court_mobile::Clock::now()>=mobile_->fade_until)mobile_->fade_until={};}
        if(mobile_->dirty){mobile_->prepare_symbols();mobile_->prepare_label();widget.queue_redraw();mobile_->dirty=false;}
    }
    void mobile_choose(const std::string& id){
        mobile_candidates_.clear();find_widget<gui2::menu_button>("court_choices").set_visible(gui2::widget::visibility::invisible);
        const auto i=std::find_if(cards_.begin(),cards_.end(),[&](const auto& c){return c.id==id;});if(i==cards_.end())return;
        state_.selected=id;mobile_expanded_=true;mobile_->set_selection(id);
        if(!flag(i->value,"enabled"))hint(state_.depth?"This Castle is inactive.":"This Area is inactive.");
        else if(state_.depth==0&&team_id().empty())hint("Join a Team first.");else hint("");
        detail();actions();mobile_layout();mobile_tick();
    }
