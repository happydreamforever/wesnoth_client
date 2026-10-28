// Test-only inclusion inside world_result.cpp, never packaged in the game.
// Runs after native map/unit initialization; synthetic XP is deliberately not
// submitted as a battle result and the fixture process exits without evidence.
void native_world_units_probe(unit& first)
{
    static bool done = false;
    if(done || !active || !resources::gameboard) return;
    done = true;
    try {
        auto require=[](bool ok,const char* why){if(!ok)throw std::runtime_error(why);};
        std::size_t forms=0, ability_forms=0, variants=0;
        for(const auto& form:hero_forms()) {
            for(const int level:{1,5,15,30}) {
                config cfg{"id","audit-hero","type",form.unit_type,"side",1,"random_traits",false};
                apply_hero_level(cfg,form.hero_id,form.id,level);
                auto native=unit::create(cfg,false);
                require(native->max_hitpoints()==scaled_hero_stat(form.base_hitpoints,level,2),"native hero HP");
                for(const auto& attack:form.attacks) {
                    bool found=false;
                    for(const auto& actual:native->attacks())
                        if(actual.id()==attack.name && actual.type()==attack.type && actual.range()==attack.range && actual.num_attacks()==attack.number) {
                            require(actual.damage()==scaled_hero_stat(attack.damage,level,1),"native hero attack");found=true;
                        }
                    require(found,"native hero weapon missing");
                }
                if(level==1 && !native->abilities().empty()) ++ability_forms;
                ++variants;
            }
            ++forms;
        }
        require(forms>=117 && ability_forms>0,"hero catalogue coverage");
        require(first.variables()["world_kind"].str()=="hero" && first.max_experience()==1000000000,"native hero advancement lock");
        unit_ptr soldier;
        for(const auto& side:resources::gameboard->teams())
            for(const auto& candidate:side.recall_list())
                if(candidate->variables()["world_kind"].str()=="soldier") { soldier=candidate->clone();break; }
        require(soldier && soldier->type_id()=="Spearman" && soldier->experience()==41,"synthetic audit starting soldier");
        const auto id=soldier->variables()["world_instance_id"].str();
        record_world_experience(*soldier,400); // Explicit synthetic fixture grant.
        soldier->set_experience(soldier->experience()+400);
        soldier=get_advanced_unit(*soldier,"Swordsman");record_world_form(*soldier);
        require(soldier->type_id()=="Swordsman" && soldier->experience()==399,"first native advancement overflow");
        soldier=get_advanced_unit(*soldier,"Royal Guard");record_world_form(*soldier);
        require(soldier->type_id()=="Royal Guard" && soldier->experience()==319,"second native advancement overflow");
        require(soldier->max_experience()==1000000000 && !soldier->advances(),"terminal native AMLA blocked");
        const auto& tracked=active->units.at(id);
        require(active->valid && tracked.earned==400 && tracked.type=="Royal Guard" && tracked.xp==149,"native ledger terminal clamp");
        std::cout<<"WORLD NATIVE UNIT AUDIT PASSED forms="<<forms<<" scaled_variants="<<variants
            <<" ability_forms="<<ability_forms<<" synthetic_xp=400 advancements=2 terminal_xp=149 no_settlement=true"<<std::endl;
        std::exit(0);
    } catch(const std::exception& error) {
        std::cerr<<"WORLD NATIVE UNIT AUDIT FAILED: "<<error.what()<<std::endl;
        std::exit(3);
    }
}
