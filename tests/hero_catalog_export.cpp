// SPDX-License-Identifier: GPL-2.0-or-later
// Developer-only exporter: resolve the pinned engine's inherited unit types.
#include "config.hpp"
#include "filesystem.hpp"
#include "game_config.hpp"
#include "game_config_view.hpp"
#include "serialization/parser.hpp"
#include "units/types.hpp"
#include <fstream>
#include <iostream>
#include <stdexcept>
config portable_cfg(const unit_type& type) {
 auto cfg=type.get_cfg();
 auto abilities=type.abilities_cfg();cfg.clear_children("abilities");cfg.remove_attribute("abilities_list");if(!abilities.empty())cfg.add_child("abilities",abilities);
 for(auto& a:cfg.child_range("attack")){auto specials=unit_type_data::add_registry_entries(a,"specials",unit_types.specials());a.clear_children("specials");a.remove_attribute("specials_list");if(!specials.empty())a.add_child("specials",specials);}
 for(const auto* key:{"movement_costs","vision_costs","jamming_costs","defense","resistance"})cfg.clear_children(key);
 type.movement_type().write(cfg,true);cfg["movement_type"]="smallfoot";
 return cfg;
}
extern "C" int __real_main(int,char**);
extern "C" int __wrap_main(int argc,char** argv) {
 if(argc!=4 || std::string(argv[1])!="--export-hero-types") return __real_main(argc,argv);
 try {
  filesystem::set_user_data_dir("/out/export-userdata"); game_config::path="/gamedata";
  std::ifstream input(argv[2]); config root=io::read(input);
  unit_types.set_config(game_config_view::wrap(root).merged_children_view("units"));
  config output;
  for(const auto& entry:unit_types.types()) {
   const auto* type=unit_types.find(entry.first);
   if(!type)throw std::runtime_error("Unit did not resolve.");
   output.add_child("unit_type",portable_cfg(*type));
   for(const auto& variant:type->variation_types()){ auto cfg=portable_cfg(variant.second);cfg["parent_type"]=entry.first;cfg["export_variation"]=variant.first; output.add_child("resolved_variation",cfg); }
  }
  std::ofstream file(argv[3]);io::write(file,output);
  if(!file)throw std::runtime_error("Export write failed.");
  std::cout<<"HERO SOURCE TYPES RESOLVED "<<output.child_count("unit_type")<<'\n';
  return 0;
 }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
