// Test-only full-engine audit. Never compiled into the shipped worker or APK.
static void native_world_command_probe(unit&)
{
    static bool checked=false;
    if(checked || !resources::controller || !resources::gameboard || resources::controller->current_side()<1)return;
    auto& units=resources::gameboard->units();
    auto own=units.end(),enemy=units.end();
    for(auto it=units.begin();it!=units.end();++it){
        if(it->side()==resources::controller->current_side())own=it;
        else enemy=it;
    }
    if(own==units.end() || enemy==units.end())return;
    checked=true;
    const auto source=enemy->get_location(),target=own->get_location();
    const auto count=units.size();const auto hp=own->hitpoints();
    int errors=0;
    action_spectator spectator([&](const std::string& text){
        if(text!="World command source does not belong to the current side.")throw std::runtime_error("Unexpected command guard error.");
        ++errors;
    });
    config attack{"weapon",0};
    attack.add_child("source",config{"x",source.wml_x(),"y",source.wml_y()});
    attack.add_child("destination",config{"x",target.wml_x(),"y",target.wml_y()});
    if(synced_command::registry().at("attack")(attack,spectator))throw std::runtime_error("Opposing attack was accepted.");
    const auto adjacent=source.get_direction(map_location::direction::south);
    config move{"x",std::to_string(source.wml_x())+","+std::to_string(adjacent.wml_x()),
        "y",std::to_string(source.wml_y())+","+std::to_string(adjacent.wml_y())};
    if(synced_command::registry().at("move")(move,spectator))throw std::runtime_error("Opposing movement was accepted.");
    if(errors!=2||units.size()!=count||units.find(source)==units.end()||units.find(target)==units.end()||units.find(target)->hitpoints()!=hp)
        throw std::runtime_error("Rejected commands changed the native board.");
    std::cerr<<"WORLD NATIVE COMMAND GUARD PASSED opposing_move=rejected opposing_attack=rejected unchanged_board=true no_settlement=true"<<std::endl;
    std::_Exit(0);
}
