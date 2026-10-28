// SPDX-License-Identifier: GPL-2.0-or-later
namespace {
namespace ui = product_ui;
std::vector<config> plan_menu_values(const std::vector<std::string>& labels) {
    std::vector<config> values;for(const auto& label:labels){config row;row["label"]=label;values.push_back(std::move(row));}return values;
}
const std::array<std::pair<const char*,const char*>,43> ai_choices{{
    {"Loyalists","Lieutenant"},
    {"Loyalists","Swordsman"},
    {"Loyalists","Pikeman"},
    {"Loyalists","Javelineer"},
    {"Loyalists","Shock Trooper"},
    {"Loyalists","Longbowman"},
    {"Loyalists","White Mage"},
    {"Loyalists","Red Mage"},
    {"Rebels","Elvish Captain"},
    {"Rebels","Elvish Hero"},
    {"Rebels","Elvish Ranger"},
    {"Rebels","Elvish Marksman"},
    {"Rebels","Elvish Druid"},
    {"Rebels","Elvish Sorceress"},
    {"Rebels","White Mage"},
    {"Rebels","Red Mage"},
    {"Rebels","Elder Wose"},
    {"Northerners","Orcish Warrior"},
    {"Northerners","Troll"},
    {"Northerners","Troll Rocklobber"},
    {"Northerners","Orcish Crossbowman"},
    {"Northerners","Orcish Slayer"},
    {"Knalgan Alliance","Dwarvish Steelclad"},
    {"Knalgan Alliance","Dwarvish Thunderguard"},
    {"Knalgan Alliance","Dwarvish Stalwart"},
    {"Knalgan Alliance","Rogue"},
    {"Knalgan Alliance","Trapper"},
    {"Undead","Dark Sorcerer"},
    {"Undead","Revenant"},
    {"Undead","Deathblade"},
    {"Undead","Bone Shooter"},
    {"Undead","Necrophage"},
    {"Drakes","Drake Flare"},
    {"Drakes","Fire Drake"},
    {"Drakes","Drake Arbiter"},
    {"Drakes","Drake Thrasher"},
    {"Drakes","Drake Warrior"},
    {"Drakes","Saurian Oracle"},
    {"Dunefolk","Dune Explorer"},
    {"Dunefolk","Dune Swordsman"},
    {"Dunefolk","Dune Spearguard"},
    {"Dunefolk","Dune Scorcher"},
    {"Dunefolk","Dune Apothecary"},
}};
std::string plan_window(bool editor) {
    const std::string id=editor?"alliance_plan_editor":"alliance_plan_workspace";
    auto& windows=gui2::current_gui->second.window_types;if(windows.count(id))return id;
    config def;auto& grid=ui::window(def,id);ui::text(grid,"plan_title","Team battle","alliance_title");
    ui::text(grid,"plan_status","","alliance_caption");auto& body=ui::cell(ui::row(grid,1),"grid");auto& columns=ui::row(body,1);
    auto& left=ui::cell(columns,"grid","",2);
    if(editor){auto& map=ui::cell(ui::row(left),"menu_button","plan_map");map["definition"]="alliance_menu";}
    else ui::text(left,"plan_map_name","","alliance_text");
    // Native minimap uses the actual approved scenario, never a decorative substitute.
    ui::cell(ui::row(left),"minimap","plan_minimap");
    if(editor){
        auto& settings=ui::cell(ui::row(left),"grid");
        for(const auto& item:std::array<std::pair<const char*,const char*>,3>{{{"gold","Gold"},{"base_income","Base income"},{"turns","Turns"}}}){
            auto& r=ui::row(settings);auto& l=ui::cell(r,"label");l["label"]=item.second;ui::cell(r,"text_box",item.first);
        }
        auto& toggles=ui::cell(ui::row(left),"grid");auto& toggle_row=ui::row(toggles);
        for(const auto& item:std::array<std::pair<const char*,const char*>,3>{{{"fog","Fog"},{"shroud","Shroud"},{"observers","Observers"}}})ui::cell(toggle_row,"toggle_button",item.first,1,1)["label"]=item.second;
    } else {
        ui::text(left,"plan_rules","","alliance_text");ui::text(left,"plan_consent","","alliance_caption");
    }
    auto& right=ui::cell(columns,"grid","",3);
    for(int side=1;side<=8;++side){
        auto& r=ui::row(right);auto& seat=ui::cell(r,"grid","",1,0);seat["id"]="seat_"+std::to_string(side);
        auto& line=ui::row(seat);auto& label=ui::cell(line,"label","seat_label_"+std::to_string(side),2,1);label["definition"]="alliance_caption";label["wrap"]=true;
        if(editor){for(const auto* type:{"kind_","occupant_"}){auto& menu=ui::cell(line,"menu_button",type+std::to_string(side),2,1);menu["definition"]="alliance_menu";}}
        else {auto& portrait=ui::cell(line,"image","seat_image_"+std::to_string(side),0,1);portrait["definition"]="centered";
            auto& action=ui::cell(line,"button","join_"+std::to_string(side),1,1);action["label"]="Join";}
    }
    auto& footer=ui::cell(ui::row(grid),"grid");
    if(editor){auto& r=ui::row(footer);ui::cell(r,"button","save")["label"]="Save plan";ui::cell(r,"button","cancel")["label"]="Back";}
    else {const std::array<const char*,9> ids{{"use_companies","hero","ready","approve","edit","leave","refresh","cancel_plan","cancel"}};
        const std::array<const char*,9> labels{{"Use personal companies","Choose hero","Ready","Approve","Settings","Leave seat","Refresh","Cancel plan","Back"}};
        for(int y=0;y<2;++y){auto& r=ui::row(footer);for(int x=0;x<5;++x){const int n=y*5+x;if(n<9)ui::cell(r,"button",ids[n])["label"]=labels[n];else {auto& gap=ui::cell(r,"spacer");gap["width"]=1;gap["height"]=1;}}}}
    windows.emplace(id,gui2::builder_window(def));return id;
}
class PlanEditor final:public gui2::dialogs::modal_dialog {
public:
    PlanEditor(const std::vector<alliance::account::BattleMap>& maps,const std::vector<alliance::account::Team>& teams,const std::string& actor,TeamPlanDraft& draft)
        :modal_dialog(plan_window(true)),maps_(maps),teams_(teams),actor_(actor),draft_(draft){set_allow_plugin_skip(false);}
private:
    const std::string& window_id()const override{static const std::string id="alliance_plan_editor";return id;}
    std::size_t index_=0;const std::vector<alliance::account::BattleMap>& maps_;const std::vector<alliance::account::Team>& teams_;std::string actor_;TeamPlanDraft& draft_;
    std::vector<alliance::account::BattleSlot> slots_;
    const alliance::account::Team* team_for(int side)const {
        const auto& map=maps_.at(index_);for(std::size_t i=0;i<map.sides.size()&&i<teams_.size();++i)
            if(std::find(map.sides[i].begin(),map.sides[i].end(),side)!=map.sides[i].end())return &teams_[i];return nullptr;
    }
    alliance::account::BattleSlot& slot(int side) {auto it=std::find_if(slots_.begin(),slots_.end(),[&](const auto& s){return s.side==side;});if(it==slots_.end()){slots_.push_back({side,"open",{},{},{}});return slots_.back();}return *it;}
    void occupant(int side) {
        auto& s=slot(side);auto& menu=find_widget<gui2::menu_button>("occupant_"+std::to_string(side));const auto* team=team_for(side);if(!team)return;
        std::vector<std::string> names;unsigned selected=0;
        if(s.kind=="reserved")for(const auto& member:team->members){if(member.id==s.account_id)selected=names.size();names.push_back(member.username);}
        else if(s.kind=="ai")for(std::size_t i=0;i<ai_choices.size();++i){if(s.ai_faction==ai_choices[i].first&&s.ai_leader==ai_choices[i].second)selected=i;names.push_back(std::string(ai_choices[i].first)+" · "+ai_choices[i].second);}
        else names.push_back(s.kind=="closed"?"Closed":"Any team member");
        if(names.empty())names.push_back("No members");menu.set_values(plan_menu_values(names),selected);
        if(s.kind=="ai"){menu.set_label(ai_choices.at(selected).second);menu.set_tooltip(ai_choices.at(selected).first);}
        menu.set_active(team->captain_id==actor_&&(s.kind=="reserved"||s.kind=="ai"));
    }
    void layout_seats(bool reset) {
        if(reset)slots_.clear();const auto& map=maps_.at(index_);
        find_widget<gui2::minimap>("plan_minimap").set_map_data(battle_map_preview(map.scenario_id));
        for(int side=1;side<=8;++side){const auto* team=team_for(side);find_widget<gui2::grid>("seat_"+std::to_string(side)).set_visible(team?gui2::widget::visibility::visible:gui2::widget::visibility::invisible);if(!team)continue;
            auto& s=slot(side);find_widget<gui2::label>("seat_label_"+std::to_string(side)).set_label(std::to_string(side)+"  "+team->name);
            auto& kind=find_widget<gui2::menu_button>("kind_"+std::to_string(side));const std::array<std::string,4> kinds{{"open","reserved","ai","closed"}};
            const auto i=std::find(kinds.begin(),kinds.end(),s.kind);kind.set_values(plan_menu_values({"Open","Reserved","AI","Closed"}),i==kinds.end()?0:i-kinds.begin());kind.set_active(team->captain_id==actor_);occupant(side);
        }
        invalidate_layout();
    }
    void pre_show()override {
        if(maps_.empty()||teams_.size()!=2)throw std::runtime_error("A plan requires an approved map and two teams.");
        find_widget<gui2::label>("plan_title").set_label(teams_[0].name+"  vs  "+teams_[1].name);
        find_widget<gui2::label>("plan_status").set_label("Map and common settings · Captains manage their own seats");
        std::vector<std::string> names;for(const auto& map:maps_){if(map.id==draft_.map_id)index_=names.size();names.push_back(map.name);}
        auto& map=find_widget<gui2::menu_button>("plan_map");map.set_values(plan_menu_values(names),index_);
        gui2::event::connect_signal_notify_modified(map,[this](auto&&...){index_=find_widget<gui2::menu_button>("plan_map").get_value();layout_seats(true);});
        for(const auto& p:std::array<std::pair<const char*,int>,3>{{{"gold",draft_.settings.gold},{"base_income",draft_.settings.base_income},{"turns",draft_.settings.turns}}})find_widget<gui2::text_box>(p.first).set_value(std::string(p.first)=="turns"&&p.second<0?"Unlimited":std::to_string(p.second));
        find_widget<gui2::toggle_button>("fog").set_value(draft_.settings.fog);find_widget<gui2::toggle_button>("shroud").set_value(draft_.settings.shroud);find_widget<gui2::toggle_button>("observers").set_value(draft_.settings.observers);
        slots_=draft_.slots;layout_seats(false);
        for(int side=1;side<=8;++side)gui2::event::connect_signal_notify_modified(find_widget<gui2::menu_button>("kind_"+std::to_string(side)),[this,side](auto&&...){
            auto& s=slot(side);const std::array<const char*,4> kinds{{"open","reserved","ai","closed"}};s.kind=kinds.at(find_widget<gui2::menu_button>("kind_"+std::to_string(side)).get_value());s.account_id.clear();s.ai_faction.clear();s.ai_leader.clear();occupant(side);
        });
        for(int side=1;side<=8;++side)gui2::event::connect_signal_notify_modified(find_widget<gui2::menu_button>("occupant_"+std::to_string(side)),[this,side](auto&&...){
            if(slot(side).kind!="ai")return;auto& menu=find_widget<gui2::menu_button>("occupant_"+std::to_string(side));const auto& choice=ai_choices.at(menu.get_value());menu.set_label(choice.second);menu.set_tooltip(choice.first);
        });
        gui2::event::connect_signal_mouse_left_click(find_widget<gui2::button>("save"),[this](auto&&...){
            try {
                auto integer=[this](const char* id,int low,int high){const auto value=find_widget<gui2::text_box>(id).get_value();if(std::string(id)=="turns"&&(value=="Unlimited"||value=="unlimited"))return -1;std::size_t end=0;const int n=std::stoi(value,&end);if(end!=value.size()||n<low||n>high)throw std::runtime_error("range");return n;};
                auto settings=draft_.settings;settings.gold=integer("gold",0,1000);settings.base_income=integer("base_income",0,20);settings.turns=integer("turns",-1,1000);if(settings.turns==0)throw std::runtime_error("turns");
                settings.fog=find_widget<gui2::toggle_button>("fog").get_value_bool();settings.shroud=find_widget<gui2::toggle_button>("shroud").get_value_bool();settings.observers=find_widget<gui2::toggle_button>("observers").get_value_bool();
                std::vector<alliance::account::BattleSlot> result;
                for(int side=1;side<=8;++side){const auto* team=team_for(side);if(!team)continue;auto s=slot(side);if(team->captain_id==actor_){
                    const auto selected=find_widget<gui2::menu_button>("occupant_"+std::to_string(side)).get_value();
                    if(s.kind=="reserved"){s.account_id=team->members.at(selected).id;s.ai_faction.clear();s.ai_leader.clear();}
                    if(s.kind=="ai"){s.account_id.clear();s.ai_faction=ai_choices.at(selected).first;s.ai_leader=ai_choices.at(selected).second;}
                }result.push_back(std::move(s));}
                draft_.map_id=maps_[index_].id;draft_.settings=settings;draft_.slots=std::move(result);set_retval(gui2::retval::OK);
            }catch(...){find_widget<gui2::label>("plan_status").set_label("Gold 0–1000 · Base income 0–20 · Turns 1–1000 or Unlimited");}
        });
    }
};
class PlanWorkspace final:public gui2::dialogs::modal_dialog {
public:
    PlanWorkspace(const alliance::account::Battle& battle,const std::string& actor,const std::string& team,const std::vector<alliance::account::Team>& rosters,bool terminal_result,bool pending_result):modal_dialog(plan_window(false)),battle_(battle),actor_(actor),team_(team),rosters_(rosters),terminal_result_(terminal_result),pending_result_(pending_result){set_allow_plugin_skip(false);}
    TeamPlanActionResult result;
private:
    const alliance::account::Battle& battle_;std::string actor_,team_;const std::vector<alliance::account::Team>& rosters_;bool terminal_result_,pending_result_;
    const std::string& window_id()const override{static const std::string id="alliance_plan_workspace";return id;}
    void action(const char* id,TeamPlanAction value,bool enabled=true){auto& b=find_widget<gui2::button>(id);b.set_active(enabled);gui2::event::connect_signal_mouse_left_click(b,[this,value](auto&&...){result.action=value;set_retval(gui2::retval::OK);});}
    void pre_show()override{
        find_widget<gui2::label>("plan_title").set_label(battle_.teams.at(0).name+"  vs  "+battle_.teams.at(1).name);
        const bool draft=battle_.state=="draft";bool captain=false,approved=false,joined=false,ready=false,hero=false;
        for(const auto& team:battle_.teams){if(team.captain_id==actor_){captain=true;approved=team.approved;}for(const auto& p:team.players)if(p.id==actor_){joined=true;ready=p.ready;hero=p.selection_present;}}
        find_widget<gui2::label>("plan_status").set_label(state_label(battle_.state)+" · "+(battle_.ranked?"Ranked":"Unranked")+(battle_.validity=="unbalanced_sides"?" · Both teams need equal enabled seats":""));
        find_widget<gui2::label>("plan_map_name").set_label(battle_.map.name);find_widget<gui2::minimap>("plan_minimap").set_map_data(battle_map_preview(battle_.map.scenario_id));
        const auto& s=battle_.settings;find_widget<gui2::label>("plan_rules").set_label("Gold  "+std::to_string(s.gold)+"\nBase income  "+std::to_string(s.base_income)+"\nTurns  "+(s.turns<0?"Unlimited":std::to_string(s.turns))+"\nFog  "+(s.fog?"On":"Off")+"    Shroud  "+(s.shroud?"On":"Off"));
        find_widget<gui2::label>("plan_consent").set_label(battle_.teams[0].name+": "+(battle_.teams[0].approved?"Approved":"Awaiting captain")+"\n"+battle_.teams[1].name+": "+(battle_.teams[1].approved?"Approved":"Awaiting captain"));
        for(int side=1;side<=8;++side){const alliance::account::BattleTeam* owner=nullptr;const alliance::account::BattleSlot* slot=nullptr;const alliance::account::BattlePlayer* player=nullptr;
            for(const auto& t:battle_.teams){for(const auto& v:t.slots)if(v.side==side){owner=&t;slot=&v;}for(const auto& p:t.players)if(p.side==side)player=&p;}
            find_widget<gui2::grid>("seat_"+std::to_string(side)).set_visible(slot?gui2::widget::visibility::visible:gui2::widget::visibility::invisible);if(!slot)continue;
            const auto* form=player&&player->selection_present?find_hero_form(player->hero_id,player->form_id):nullptr;
            std::string reserved="Reserved";for(const auto& roster:rosters_)for(const auto& member:roster.members)if(member.id==slot->account_id)reserved="Reserved · "+member.username;
            const std::string who=player?player->username:slot->kind=="ai"?slot->ai_faction+" AI":slot->kind=="reserved"?reserved:slot->kind=="closed"?"Closed":"Open";
            find_widget<gui2::label>("seat_label_"+std::to_string(side)).set_label(std::to_string(side)+"  "+owner->name+" · "+who+"\n"+(form?hero_name(player->hero_id):slot->kind=="ai"?slot->ai_leader:player?"Choose a hero":"")+(player?player->ready?" · Ready":" · Waiting":""));
            find_widget<gui2::image>("seat_image_"+std::to_string(side)).set_label((form?battle_unit_image(form->unit_type,false):slot->kind=="ai"?battle_unit_image(slot->ai_leader,false):"misc/blank-hex.png")+"~SCALE_INTO(40,40)");
            auto& join=find_widget<gui2::button>("join_"+std::to_string(side));const bool permitted=draft&&!joined&&!player&&owner->id==team_&&(slot->kind=="open"||(slot->kind=="reserved"&&slot->account_id==actor_));join.set_label(player&&player->id==actor_?"You":slot->kind=="closed"?"—":"Join");join.set_active(permitted);
            gui2::event::connect_signal_mouse_left_click(join,[this,side](auto&&...){result={TeamPlanAction::join,side};set_retval(gui2::retval::OK);});
        }
        const bool result=battle_.state=="running"||battle_.state=="completed";
        action("hero",result?TeamPlanAction::result:TeamPlanAction::hero,result||(draft&&joined));
        if(result)find_widget<gui2::button>("hero").set_label("Results");
        action("ready",result?TeamPlanAction::upload:TeamPlanAction::ready,result?pending_result_:draft&&joined&&hero);
        find_widget<gui2::button>("ready").set_label(result?"Send result":ready?"Not ready":"Ready");
        if(terminal_result_&&battle_.state=="running")find_widget<gui2::label>("plan_status").set_label(pending_result_?"Result saved · Upload pending":"Result sent · Captain review");
        find_widget<gui2::button>("use_companies").set_visible(draft&&captain&&battle_.room_state!="preparing"?gui2::widget::visibility::visible:gui2::widget::visibility::invisible);action("use_companies",TeamPlanAction::companies,draft&&captain&&battle_.room_state!="preparing");
        action("approve",TeamPlanAction::approve,draft&&captain&&!approved);action("edit",TeamPlanAction::edit,draft&&captain);
        action("leave",TeamPlanAction::leave,draft&&joined);
        const bool member=std::any_of(battle_.teams.begin(),battle_.teams.end(),[this](const auto& t){return t.id==team_;});
        const bool watch=battle_.state=="running"&&!joined&&member&&battle_.settings.observers;
        action("refresh",watch?TeamPlanAction::watch:battle_.state=="running"&&joined&&!terminal_result_?TeamPlanAction::reconnect:TeamPlanAction::refresh);
        if(watch)find_widget<gui2::button>("refresh").set_label("Watch");
        if(battle_.state=="running"&&joined&&!terminal_result_)find_widget<gui2::button>("refresh").set_label("Reconnect");
        action("cancel_plan",battle_.state=="completed"?TeamPlanAction::replay:TeamPlanAction::cancel,battle_.state=="completed"||(draft&&captain));
        if(battle_.state=="completed")find_widget<gui2::button>("cancel_plan").set_label("Replay");
    }
};
}
bool edit_team_plan(const std::vector<alliance::account::BattleMap>& maps,const std::vector<alliance::account::Team>& teams,const std::string& actor,TeamPlanDraft& draft){PlanEditor dialog(maps,teams,actor,draft);return dialog.show();}
TeamPlanActionResult show_team_plan_workspace(const alliance::account::Battle& battle,const std::string& actor,const std::string& actor_team,const std::vector<alliance::account::Team>& rosters,bool terminal_result,bool pending_result){PlanWorkspace dialog(battle,actor,actor_team,rosters,terminal_result,pending_result);dialog.show();return dialog.result;}
