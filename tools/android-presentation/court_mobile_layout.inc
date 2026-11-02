// Compact native controls keep map navigation and actions on one touch row.
inline void mobile_toolbar_style(const std::string& id,const char* widget="button",int symbol=0){
    if(gui2::current_gui->second.widget_types[widget].count(id))return;
    const double density=court_mobile::scale();const int size=int(48*density),font=int(16*density);
    config def;def["id"]=id;def["description"]="Emerald Court map toolbar";auto& r=def.add_child("resolution");
    r["min_width"]=size;r["default_width"]=symbol?size:int(64*density);r["min_height"]=size;r["default_height"]=size;r["max_height"]=size;
    r["text_font_size"]=font;r["text_extra_width"]=int(16*density);r["text_extra_height"]=int(12*density);
    for(int state=0;state<4;++state){const char* names[]={"state_enabled","state_disabled","state_pressed","state_focused"};auto& d=r.add_child(names[state]).add_child("draw");
        emerald_chrome::frame_canvas(d,emerald_chrome::Kind::button,state>=2,state==1,int(12*density));
        if(!symbol){ui::caption(d,font,state==1?"150,158,139,255":ui::ivory,true);continue;}
        const int pixels=int(24*density);auto& icon=d.add_child("image");icon["x"]="((width - "+std::to_string(pixels)+") / 2)";icon["y"]="((height - "+std::to_string(pixels)+") / 2)";icon["w"]=pixels;icon["h"]=pixels;
        icon["name"]=emerald_chrome::raster(pixels,pixels,2,[=](cairo_t* c){cairo_scale(c,density,density);cairo_set_line_width(c,3);cairo_set_line_cap(c,CAIRO_LINE_CAP_ROUND);
            if(state==1)cairo_set_source_rgb(c,.59,.62,.55);else cairo_set_source_rgb(c,.96,.88,.67);
            cairo_move_to(c,4,12);cairo_line_to(c,20,12);if(symbol>0){cairo_move_to(c,12,4);cairo_line_to(c,12,20);}cairo_stroke(c);});
    }
    gui2::add_single_widget_definition(widget,id,def);
}
inline config& mobile_toolbar_button(config& row,const char* id,const char* label,const char* definition="court_toolbar"){
    auto& button=ui::cell(row,"button",id,0,2);button["definition"]=definition;button["label"]=label;return button;
}
inline std::string workspace_window(){
    const std::string id="alliance_court_workspace";auto& windows=gui2::current_gui->second.window_types;if(windows.count(id))return id;
    mobile_toolbar_style("court_toolbar");mobile_toolbar_style("court_toolbar_menu","menu_button");mobile_toolbar_style("court_toolbar_minus","button",-1);mobile_toolbar_style("court_toolbar_plus","button",1);
    config def;auto& grid=ui::window(def,id);
    auto& resolution=def.mandatory_child("resolution");resolution["width"]="(screen_width - 24)";resolution["x"]=12;
    auto& header=ui::row(ui::cell(ui::row(grid),"grid","",1,0));
    mobile_toolbar_button(header,"court_back","Back");
    ui::cell(header,"label","court_heading",1,2)["definition"]="alliance_title";
    mobile_toolbar_button(header,"court_team","My Team");mobile_toolbar_button(header,"court_inbox","Inbox");mobile_toolbar_button(header,"court_refresh","Refresh");
    auto& toolbar=ui::cell(header,"grid","",0,0);toolbar["id"]="court_tools";auto& tools=ui::row(toolbar);
    mobile_toolbar_button(tools,"court_search_toggle","Search");
    ui::cell(tools,"text_box","court_search",1,2)["hint_text"]="Search";
    auto& filter=ui::cell(tools,"menu_button","court_filter",0,2);filter["definition"]="court_toolbar_menu";filter["label"]="All heroes";
    mobile_toolbar_button(tools,"court_view","List");
    auto& zoom=ui::cell(tools,"grid","",0,0);zoom["id"]="court_zoom_controls";auto& controls=ui::row(zoom);
    mobile_toolbar_button(controls,"court_zoom_out","","court_toolbar_minus")["tooltip"]="Zoom out";
    mobile_toolbar_button(controls,"court_centre","Centre");mobile_toolbar_button(controls,"court_zoom_in","","court_toolbar_plus")["tooltip"]="Zoom in";
    mobile_toolbar_button(tools,"court_collapse","Details");
    auto& top_actions=ui::cell(header,"grid","court_top_actions",0,0);top_actions["id"]="court_top_actions";
    ui::cell(ui::row(top_actions),"grid","court_actions_placeholder",0,0)["id"]="court_actions_placeholder";
    auto& content=ui::cell(ui::row(grid,1),"grid");content["id"]="court_content";
    auto& left=ui::cell(ui::row(content,1),"grid","",1,0);left["id"]="court_browser";
    auto& stack=ui::cell(ui::row(left,1),"stacked_widget","court_body",1,0);
    auto& map_layer=stack.add_child("layer");auto& drawing=ui::cell(ui::row(map_layer,1),"drawing","court_map",1,0);drawing["width"]=800;drawing["height"]=280;drawing.add_child("draw");
    list_definition(stack.add_child("layer"));auto& tactical=stack.add_child("layer");
    auto& preview=ui::cell(ui::row(tactical,1),"minimap","court_tactical");preview["definition"]="alliance_map";ui::text(tactical,"court_tactical_name","Battle map","alliance_caption");
    auto& right=ui::cell(ui::row(content),"grid","",1,0);right["id"]="court_bottom";
    auto& summary=ui::row(ui::cell(ui::row(right),"grid","",1,0));
    ui::cell(summary,"label","court_name")["definition"]="alliance_title";
    ui::cell(summary,"label","court_status")["definition"]="alliance_caption";
    auto& candidates=ui::cell(ui::row(right),"menu_button","court_choices");candidates["definition"]="alliance_menu";
    auto& panel=ui::cell(ui::row(right,1),"stacked_widget","court_panel",1,0);
    auto& details=panel.add_child("layer");auto& detail=ui::cell(ui::row(details,1),"scroll_label","court_detail");detail["wrap"]=true;detail["use_markup"]=false;
    /*COURT_FORM*/
    auto& footer=ui::cell(ui::row(right),"grid","court_bottom_actions",1,0);footer["id"]="court_bottom_actions";
    auto& group=ui::cell(ui::row(footer),"grid","court_action_controls",1,0);group["id"]="court_action_controls";auto& actions=ui::row(group);
    mobile_toolbar_button(actions,"court_primary","Open");
    auto& more=ui::cell(actions,"menu_button","court_actions",0,2);more["definition"]="court_toolbar_menu";more["label"]="More";
    mobile_toolbar_button(actions,"court_secondary","Back");
    ui::text(grid,"court_notice","","alliance_caption");windows.emplace(id,gui2::builder_window(def));return id;
}
