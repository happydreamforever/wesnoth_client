// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/hero_catalog.hpp"
#include "filesystem.hpp"
#include "game_board.hpp"
#include "resources.hpp"
#include "game_config.hpp"
#include "game_config_view.hpp"
#include "serialization/parser.hpp"
#include "units/types.hpp"
#include "units/unit.hpp"
#include "units/attack_type.hpp"
#include <fstream>
#include <iostream>
#include <stdexcept>
extern "C" int __real_main(int,char**);
static void check(bool c,const char* m){if(!c)throw std::runtime_error(m);}
extern "C" int __wrap_main(int argc,char**argv){if(argc!=3||std::string(argv[1])!="--verify-hero-units")return __real_main(argc,argv);try{
 filesystem::set_user_data_dir("/out/hero-unit-userdata");game_config::path="/gamedata";std::ifstream in(argv[2]);const auto core=io::read(in);auto view=alliance_game::append_hero_unit_config(game_config_view::wrap(core).merged_children_view("units"));unit_types.set_config(view);
 const auto terrain_view=game_config_view::wrap(core);terrain_type_data terrain(terrain_view);game_board board(config{});resources::gameboard=&board;board.teams().emplace_back();
 int count=0;for(const auto& f:alliance_game::hero_forms()){const auto* type=unit_types.find(f.unit_type);check(type&&type->level()==f.native_level,"Missing hero/native tier");
  for(const auto level:{1,2,26,51}){config c{"type",f.unit_type,"side",1,"id","fixture_hero","random_traits",false,"generate_name",false};alliance_game::apply_hero_level(c,f.hero_id,f.id,level);auto u=unit::create(c,false);
   check(u->max_hitpoints()==alliance_game::scaled_hero_stat(f.base_hitpoints,level,2),"Scaled HP differs");check(u->level()==f.native_level,"Hero level replaced native tier");check(u->attacks().size()==f.attacks.size(),"Attack count changed");
   for(std::size_t i=0;i<f.attacks.size();++i)check(u->attacks()[i].damage()==alliance_game::scaled_hero_stat(f.attacks[i].damage,level,1),"Scaled attack differs");
   config saved;u->write(saved);auto loaded=unit::create(saved,false);check(loaded->max_hitpoints()==u->max_hitpoints(),"Save/load compounded HP");for(std::size_t i=0;i<f.attacks.size();++i)check(loaded->attacks()[i].damage()==u->attacks()[i].damage(),"Save/load compounded attack");
   bool twice=false;try{alliance_game::apply_hero_level(c,f.hero_id,f.id,level);}catch(...){twice=true;}check(twice,"Duplicate scaling accepted");++count;
  }
 }
 check(alliance_game::scaled_hero_stat(5,11,1)==6,"Half-up damage differs");bool overflow=false;try{alliance_game::scaled_hero_stat(100,INT64_MAX,2);}catch(...){overflow=true;}check(overflow,"Overflow accepted");
 std::cout<<"ACTUAL ENGINE HERO FORMS VERIFIED forms="<<alliance_game::hero_forms().size()<<" level/save cases="<<count<<"\n";return 0;
 }catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}}
