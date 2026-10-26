// SPDX-License-Identifier: GPL-2.0-or-later
namespace {
struct RoomMap {std::string id,name,data;int sides=0;};
std::string room_creation_window() {
    using namespace product_ui;const std::string id="alliance_room_create";
    auto& windows=gui2::current_gui->second.window_types;if(windows.count(id))return id;
    config def;auto& grid=product_ui::window(def,id);product_ui::text(grid,"room_title","Create Room","alliance_title");product_ui::text(grid,"room_error","Every seat is a human player.","alliance_caption");
    auto& body=product_ui::cell(product_ui::row(grid,1),"grid");auto& cols=product_ui::row(body,1);auto& left=product_ui::cell(cols,"grid","",3);auto& right=product_ui::cell(cols,"grid","",2);
    auto& search=product_ui::cell(product_ui::row(left),"text_box","map_search");search["hint_text"]="Find a map";
    auto& list=product_ui::cell(product_ui::row(left,1),"listbox","maps");list["horizontal_scrollbar_mode"]="never";list["vertical_scrollbar_mode"]="auto";
    auto& item=product_ui::cell(product_ui::row(list.add_child("list_definition")),"toggle_panel");auto& content=item.add_child("grid");product_ui::text(content,"map_name");product_ui::text(content,"map_players","","alliance_caption");
    product_ui::cell(product_ui::row(right),"minimap","room_map");
    auto& fields=product_ui::cell(product_ui::row(right),"grid");
    for(const auto& field:std::array<std::pair<const char*,const char*>,4>{{{"room_name","Room name"},{"gold","Gold"},{"income","Base income"},{"turns","Turns"}}}){
        auto& r=product_ui::row(fields);product_ui::cell(r,"label")["label"]=field.second;product_ui::cell(r,"text_box",field.first);}
    auto& toggles=product_ui::cell(product_ui::row(right),"grid");auto& toggle_row=product_ui::row(toggles);
    for(const auto& field:std::array<std::pair<const char*,const char*>,3>{{{"fog","Fog"},{"shroud","Shroud"},{"observers","Observers"}}})product_ui::cell(toggle_row,"toggle_button",field.first,1,1)["label"]=field.second;
    auto& footer_grid=product_ui::cell(product_ui::row(grid),"grid");auto& footer=product_ui::row(footer_grid);product_ui::cell(footer,"button","publish")["label"]="Create room";product_ui::cell(footer,"button","cancel")["label"]="Back";
    windows.emplace(id,gui2::builder_window(def));return id;
}
class RoomCreationDialog final:public gui2::dialogs::modal_dialog {
public:
    RoomCreationDialog(const std::vector<RoomMap>& maps):modal_dialog(room_creation_window()),maps_(maps){set_allow_plugin_skip(false);}
    int selected=-1,gold=100,income=2,turn_limit=-1;bool fog=true,shroud=false,observers=false;std::string name;
private:
    const std::vector<RoomMap>& maps_;
    const std::string& window_id()const override{static const std::string id="alliance_room_create";return id;}
    void preview(){auto& list=find_widget<gui2::listbox>("maps");const int i=list.get_selected_row();find_widget<gui2::minimap>("room_map").set_map_data(i>=0?maps_.at(i).data:"");find_widget<gui2::button>("publish").set_active(i>=0);}
    void pre_show()override {
        auto& list=find_widget<gui2::listbox>("maps");
        for(const auto& map:maps_)list.add_row(gui2::widget_data{{"map_name",{{"label",map.name}}},{"map_players",{{"label",std::to_string(map.sides)+" players"}}}});
        gui2::event::connect_signal_notify_modified(list,[this](auto&&...){preview();});
        auto& search=find_widget<gui2::text_box>("map_search");gui2::event::connect_signal_notify_modified(search,[this](auto&&...){
            std::string query=find_widget<gui2::text_box>("map_search").get_value();std::transform(query.begin(),query.end(),query.begin(),[](unsigned char c){return std::tolower(c);});
            boost::dynamic_bitset<> visible;for(const auto& map:maps_){auto name=map.name;std::transform(name.begin(),name.end(),name.begin(),[](unsigned char c){return std::tolower(c);});visible.push_back(name.find(query)!=std::string::npos);}
            find_widget<gui2::listbox>("maps").set_row_shown(visible);preview();
        });
        find_widget<gui2::text_box>("room_name").set_value("Battle room");find_widget<gui2::text_box>("room_name").set_max_input_length(60);
        find_widget<gui2::text_box>("gold").set_value("100");find_widget<gui2::text_box>("income").set_value("2");find_widget<gui2::text_box>("turns").set_value("Unlimited");
        find_widget<gui2::toggle_button>("fog").set_value(true);find_widget<gui2::toggle_button>("shroud").set_value(false);find_widget<gui2::toggle_button>("observers").set_value(false);
        gui2::event::connect_signal_mouse_left_click(find_widget<gui2::button>("publish"),[this](auto&&...){
            try{auto number=[this](const char* id,int minimum,int maximum){const auto text=find_widget<gui2::text_box>(id).get_value();if(std::string(id)=="turns"&&(text=="Unlimited"||text=="unlimited"))return -1;std::size_t pos=0;const int value=std::stoi(text,&pos);if(pos!=text.size()||value<minimum||value>maximum)throw std::runtime_error("range");return value;};
                gold=number("gold",0,1000);income=number("income",0,20);turn_limit=number("turns",-1,1000);if(!turn_limit)throw std::runtime_error("turns");
                name=find_widget<gui2::text_box>("room_name").get_value();if(name.empty())throw std::runtime_error("name");
                selected=find_widget<gui2::listbox>("maps").get_selected_row();if(selected<0)throw std::runtime_error("map");
                fog=find_widget<gui2::toggle_button>("fog").get_value_bool();shroud=find_widget<gui2::toggle_button>("shroud").get_value_bool();observers=find_widget<gui2::toggle_button>("observers").get_value_bool();set_retval(gui2::retval::OK);
            }catch(...){find_widget<gui2::label>("room_error").set_label("Enter a name · Gold 0–1000 · Base income 0–20 · Turns 1–1000 or Unlimited");}
        });preview();set_enter_disabled(true);
    }
};
std::vector<RoomMap> room_creation_maps(saved_game& state) {
    std::vector<RoomMap> maps;ng::create_engine engine(state);
    for(const auto& level:engine.get_levels_by_type_unfiltered(level_type::type::scenario)) {
        config cfg=level->data();saved_game::expand_map_file(cfg);
        ng::scenario scenario(cfg);const auto n=scenario.num_players();
        if(!scenario.can_launch_game()||n<2||n>8||cfg["map_data"].str().empty())continue;
        const auto& normalized=scenario.data();
        if(std::any_of(normalized.child_range("side").begin(),normalized.child_range("side").end(),[](const config& side){return !side["allow_player"].to_bool(true);}))continue;
        maps.push_back({scenario.id(),scenario.name(),cfg["map_data"].str(),n});
    }
    return maps;
}
}
bool create_individual_room(saved_game& state) {
    if(!individual_rooms_supported()){gui2::show_message("Room","The server does not support individual rooms.",gui2::dialogs::message::ok_button,false,false);return false;}
    const auto maps=room_creation_maps(state);
    RoomCreationDialog dialog(maps);if(!dialog.show()||dialog.selected<0)return false;
    config preset;preset["individual_room"]=true;preset["scenario"]=maps.at(dialog.selected).id;preset["era"]="era_default";preset["turn_count"]=dialog.turn_limit;
    preset["village_gold"]=2;preset["village_support"]=1;preset["experience_modifier"]=100;preset["fog"]=dialog.fog;preset["shroud"]=dialog.shroud;
    preset["countdown"]=false;preset["shuffle_sides"]=false;preset["random_start_time"]=false;
    gui2::dialogs::mp_create_game::quick_mp_setup(state,preset);
    auto& scenario=state.get_starting_point();scenario["individual_room"]=true;scenario["room_base_income"]=dialog.income;
    state.mp_settings().name=dialog.name;state.mp_settings().allow_observers=dialog.observers;state.mp_settings().password.clear();state.mp_settings().shuffle_sides=false;
    for(auto& side:scenario.child_range("side")){
        side["controller"]="human";side["allow_player"]=true;side["controller_lock"]=true;side["gold"]=dialog.gold;
        side["income"]=dialog.income-game_config::base_income;side["gold_lock"]=true;side["income_lock"]=true;side["team_lock"]=true;
        side.remove_attribute("current_player");side.remove_attribute("player_id");
    }
    return configure_individual_creation(scenario,true);
}
