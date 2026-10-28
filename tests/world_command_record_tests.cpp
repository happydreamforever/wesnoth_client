// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/world_command_record.hpp"
#include <iostream>
#include <functional>
#include <fstream>
using namespace alliance_game;
namespace fs=std::filesystem;namespace j=boost::json;
const std::string actor="11111111-1111-4111-8111-111111111111",generation="22222222-2222-4222-8222-222222222222",command="33333333-3333-4333-8333-333333333333";
int checks=0;
void check(bool value){if(!value)throw std::runtime_error("Journal assertion failed");++checks;}
void rejects(const std::function<void()>& call){try{call();}catch(...){++checks;return;}throw std::runtime_error("Unsafe journal operation accepted");}
void write(const fs::path& path,const std::string& bytes){std::ofstream out(path);out<<bytes;out.close();}
int main(int argc,char** argv){try{
 if(argc!=2)return 2;const fs::path base=fs::absolute(argv[1]);fs::create_directories(base);const auto profile=base/"alliance-bundled-profile.json";
#ifdef _WIN32
 _putenv_s("WESNOTH_ALLIANCE_PROFILE",profile.string().c_str());
#else
 setenv("WESNOTH_ALLIANCE_PROFILE",profile.c_str(),1);
#endif
 using namespace world_command_record;
 const j::object payload{{"command_id",command},{"expected_revision",3},{"unit_type","Spearman"}};
 check(!read(actor,generation));save(actor,generation,"company_recruit",payload);const auto file=path(actor,generation,false);const auto before=world_read_file(file,65536);
 check(read(actor,generation)->at("operation")=="company_recruit");save(actor,generation,"company_recruit",payload);check(world_read_file(file,65536)==before);
 auto other=payload;other["unit_type"]="Mage";rejects([&]{save(actor,generation,"company_recruit",other);});check(world_read_file(file,65536)==before);
 check(!read("44444444-4444-4444-8444-444444444444",generation));rejects([&]{acknowledge(actor,generation,"44444444-4444-4444-8444-444444444444");});
 write(file,before.substr(0,before.size()-1)+",\"actor_id\":\""+actor+"\"}");rejects([&]{read(actor,generation);});write(file,before);
 auto doc=j::parse(before).as_object();doc["actor_id"]="44444444-4444-4444-8444-444444444444";write(file,j::serialize(doc));rejects([&]{read(actor,generation);});write(file,before);
 doc=j::parse(before).as_object();doc["extra"]=true;write(file,j::serialize(doc));rejects([&]{read(actor,generation);});write(file,before);
 write(file,std::string(65537,'x'));rejects([&]{read(actor,generation);});write(file,before);
 const auto archive=file.parent_path()/(command+".json");
#ifndef _WIN32
 fs::create_hard_link(file,archive);check(read(actor,generation).has_value());
#endif
 acknowledge(actor,generation,command);check(!read(actor,generation));check(world_read_file(archive,65536)==before);acknowledge(actor,generation,command);
 const auto symlink=base/"alias";fs::create_directory_symlink(file.parent_path(),symlink);rejects([&]{ordinary(symlink/"other.json");});
 auto malicious=payload;malicious["password"]="not-a-real-password";rejects([&]{save(actor,generation,"company_recruit",malicious);});
 check(world_read_file(archive,65536).find("password")==std::string::npos);
 std::cout<<"WORLD COMMAND JOURNAL PASS: "<<checks<<" assertions\n";return 0;
 }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
