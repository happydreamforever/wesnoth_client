inline std::string workspace_window(){
    const std::string id="alliance_court_workspace";auto& windows=gui2::current_gui->second.window_types;if(windows.count(id))return id;
    config def;auto& grid=ui::window(def,id);
    auto& resolution=def.mandatory_child("resolution");resolution["width"]="(screen_width - 24)";resolution["x"]=12;
    auto& header=ui::row(ui::cell(ui::row(grid),"grid"));
    auto& back=ui::cell(header,"button","court_back",0);back["definition"]="alliance_close";back["label"]="‹";
    ui::cell(header,"label","court_heading")["definition"]="alliance_title";
    ui::cell(header,"button","court_inbox",0)["label"]="Inbox";
    ui::cell(header,"button","court_team",0)["label"]="My Team";
    ui::cell(header,"button","court_refresh",0)["label"]="Refresh";
    auto& content=ui::cell(ui::row(grid,1),"grid");content["id"]="court_content";
    auto& left=ui::cell(ui::row(content,1),"grid","",1,0);left["id"]="court_browser";
    auto& toolbar=ui::cell(ui::row(left),"grid","",1,0);toolbar["id"]="court_tools";auto& tools=ui::row(toolbar);
    ui::cell(tools,"text_box","court_search")["hint_text"]="Search";
    auto& filter=ui::cell(tools,"menu_button","court_filter",0);filter["definition"]="alliance_menu";filter["label"]="All heroes";
    ui::cell(tools,"button","court_view",0)["label"]="List";
    auto& zoom=ui::cell(tools,"grid","",0,0);zoom["id"]="court_zoom_controls";auto& controls=ui::row(zoom);
    ui::cell(controls,"button","court_zoom_out",0)["label"]="−";ui::cell(controls,"button","court_centre",0)["label"]="Centre";ui::cell(controls,"button","court_zoom_in",0)["label"]="+";
    auto& stack=ui::cell(ui::row(left,1),"stacked_widget","court_body",1,0);
    auto& map_layer=stack.add_child("layer");auto& drawing=ui::cell(ui::row(map_layer,1),"drawing","court_map",1,0);drawing["width"]=800;drawing["height"]=280;drawing.add_child("draw");
    list_definition(stack.add_child("layer"));auto& tactical=stack.add_child("layer");
    auto& preview=ui::cell(ui::row(tactical,1),"minimap","court_tactical");preview["definition"]="alliance_map";ui::text(tactical,"court_tactical_name","Battle map","alliance_caption");
    auto& right=ui::cell(ui::row(content),"grid","",1,0);right["id"]="court_bottom";
    auto& summary=ui::row(ui::cell(ui::row(right),"grid","",1,0));
    ui::cell(summary,"label","court_name")["definition"]="alliance_title";
    ui::cell(summary,"label","court_status")["definition"]="alliance_caption";
    ui::cell(summary,"button","court_collapse",0)["label"]="Hide details";
    auto& candidates=ui::cell(ui::row(right),"menu_button","court_choices");candidates["definition"]="alliance_menu";
    auto& panel=ui::cell(ui::row(right,1),"stacked_widget","court_panel",1,0);
    auto& details=panel.add_child("layer");auto& detail=ui::cell(ui::row(details,1),"scroll_label","court_detail");detail["wrap"]=true;detail["use_markup"]=false;
    /*COURT_FORM*/
    auto& footer=ui::row(ui::cell(ui::row(right),"grid","",1,0));
    auto& more=ui::cell(footer,"menu_button","court_actions");more["definition"]="alliance_menu";more["label"]="More actions";
    ui::cell(footer,"button","court_primary")["label"]="Open";ui::cell(footer,"button","court_secondary",0)["label"]="Back";
    ui::text(grid,"court_notice","","alliance_caption");windows.emplace(id,gui2::builder_window(def));return id;
}
