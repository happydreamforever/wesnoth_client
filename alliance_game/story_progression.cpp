// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/story_progression.hpp"
#include "alliance_game/progression_result.hpp"
#include "alliance_game/account_ui.hpp"
#include "alliance_game/hero_catalog.hpp"
#include "alliance_game/story_script_patches.hpp"
#include "alliance_native/progression_protocol.hpp"
#include "game_data.hpp"
#include "game_board.hpp"
#include "map/map.hpp"
#include "resources.hpp"
#include "saved_game.hpp"
#include "savegame.hpp"
#include "filesystem.hpp"
#include "serialization/parser.hpp"
#include "gui/dialogs/message.hpp"
#include "units/unit.hpp"
#include "units/map.hpp"
#include <boost/json.hpp>
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <algorithm>
#include <set>
#include <map>
#include <cstdlib>
#include <fcntl.h>
#include <unistd.h>
namespace alliance_game {
namespace {
#include "alliance_game/story_adapters.inc"
namespace j=boost::json;namespace fs=std::filesystem;namespace pp=alliance::progression_protocol;
constexpr std::size_t max_record=4*1024*1024;
std::string str(const j::object& v,const char*k){return std::string(v.at(k).as_string());}
std::string hex(const unsigned char* p,std::size_t n){static const char d[]="0123456789abcdef";std::string s;for(std::size_t i=0;i<n;++i){s+=d[p[i]>>4];s+=d[p[i]&15];}return s;}
std::string uuid(){unsigned char b[16];if(RAND_bytes(b,16)!=1)throw std::runtime_error("Story randomness unavailable.");b[6]=(b[6]&15)|64;b[8]=(b[8]&63)|128;const auto s=hex(b,16);return s.substr(0,8)+"-"+s.substr(8,4)+"-"+s.substr(12,4)+"-"+s.substr(16,4)+"-"+s.substr(20);}
std::string digest(const std::string& data){unsigned char b[32];unsigned int n=0;if(EVP_Digest(data.data(),data.size(),b,&n,EVP_sha256(),nullptr)!=1||n!=32)throw std::runtime_error("Story digest failed.");return hex(b,32);}
fs::path directory(const PlayerSession& s,bool create){
 const char* env=std::getenv("WESNOTH_ALLIANCE_PROFILE");if(!env||!*env||!pp::uuid(s.account.account_id)||!pp::uuid(s.account.database_generation))throw std::runtime_error("Story account storage unavailable.");
 const fs::path profile(env);if(!profile.is_absolute()||profile.filename()!="alliance-public-profile.json")throw std::runtime_error("Story profile unavailable.");
 auto p=profile.parent_path();for(const auto& part:{std::string("unified-story"),s.account.database_generation,s.account.account_id}){p/=part;if(create&&!fs::exists(p)){fs::create_directory(p);fs::permissions(p,fs::perms::owner_all);}if(fs::exists(p)&&(!fs::is_directory(p)||fs::is_symlink(p)))throw std::runtime_error("Unsafe story directory.");}return p;
}
j::object read(const fs::path& p){if(fs::is_symlink(p)||!fs::is_regular_file(p)||fs::file_size(p)>max_record)throw std::runtime_error("Invalid story record.");std::ifstream in(p,std::ios::binary);std::string data((std::istreambuf_iterator<char>(in)),{});if(in.bad()||data.size()>max_record)throw std::runtime_error("Story read failed.");return j::parse(data).as_object();}
void write(const fs::path& path,const j::object& value){const auto data=j::serialize(value);if(data.size()>max_record)throw std::runtime_error("Story record exceeds limit.");auto tmp=path.string()+".tmp-"+uuid();int fd=::open(tmp.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW,0600);if(fd<0)throw std::runtime_error("Story write failed.");try{std::size_t pos=0;while(pos<data.size()){auto n=::write(fd,data.data()+pos,data.size()-pos);if(n<=0)throw std::runtime_error("Story write incomplete.");pos+=n;}if(::fsync(fd)!=0)throw std::runtime_error("Story sync failed.");::close(fd);fd=-1;if(fs::exists(path)&&(!fs::is_regular_file(path)||fs::is_symlink(path)))throw std::runtime_error("Story file changed.");fs::rename(tmp,path);int parent=::open(path.parent_path().c_str(),O_RDONLY|O_DIRECTORY);if(parent<0)throw std::runtime_error("Story directory unavailable.");int status=::fsync(parent);::close(parent);if(status)throw std::runtime_error("Story directory sync failed.");}catch(...){if(fd>=0)::close(fd);std::error_code e;fs::remove(tmp,e);throw;}}
bool request(const std::string& op,const j::object& body,j::object& out){alliance::account::Request input;input.operation=op;input.progression_json=j::serialize(body);alliance::account::Reply reply;if(!player_request(input,reply))return false;out=j::parse(reply.progression_json).as_object();return true;}
config& child(config& c,const char* key){return c.optional_child(key)?c.mandatory_child(key):c.add_child(key);}
void flush(const PlayerSession& s,const fs::path& path,j::object& record){auto& pending=record.at("pending").as_array();while(!pending.empty()){j::object result;if(!request("story_checkpoint",pending.front().as_object(),result))return;const auto& sent=pending.front().as_object();if(str(result,"run_id")!=str(sent,"run_id")||str(result,"checkpoint_id")!=str(sent,"checkpoint_id")||str(result,"outcome")!=str(sent,"outcome")||result.at("next_checkpoint").as_int64()!=sent.at("sequence").as_int64()+1)throw std::runtime_error("Story acknowledgement differs.");const auto session=player_session();if(!session||session->account.account_id!=s.account.account_id||session->account.database_generation!=s.account.database_generation)throw std::runtime_error("Story actor changed.");pending.erase(pending.begin());write(path,record);}}
void tracking_error() noexcept {try{if(resources::gamedata)resources::gamedata->get_variable("unified_story_tracking_error")=true;}catch(...){}}
const config* marker(){if(!resources::gamedata)return nullptr;const auto& v=resources::gamedata->get_variables();auto c=v.optional_child("unified_story");return c?&*c:nullptr;}

}
void retry_story_results() noexcept {retry_training_results();try{const auto s=player_session();if(!s)return;auto dir=directory(*s,false);if(!fs::exists(dir))return;std::size_t count=0;for(const auto& f:fs::directory_iterator(dir)){if(++count>4096)throw std::runtime_error("Story history full.");if(f.path().extension()!=".json")continue;auto r=read(f.path());flush(*s,f.path(),r);}}catch(...){/* Pending files remain durable and account-bound. */}}
bool begin_story_campaign(saved_game& state){
 const auto* campaign=find_story_campaign(state.classification().campaign);if(!campaign)return true;
 try{const auto s=player_session();if(!s)return false;retry_story_results();j::object result;if(!request("story_begin",{{"campaign_id",campaign->id},{"catalog_version",hero_catalog_version()},{"catalog_sha256",hero_catalog_sha256()}},result))return false;
 const auto& run=result.contains("run")?result.at("run").as_object():result;const auto id=str(run,"run_id");if(!pp::uuid(id)||str(run,"account_id")!=s->account.account_id||str(run,"database_generation")!=s->account.database_generation||str(run,"campaign_id")!=campaign->id||str(run,"catalog_version")!=hero_catalog_version()||str(run,"catalog_sha256")!=hero_catalog_sha256()||run.at("next_checkpoint").as_int64()!=0||str(run,"nonce").size()!=64)throw std::runtime_error("Story registration differs.");
 j::object record{{"format","unified-private-story-v1"},{"run",run},{"next_sequence",0},{"pending",j::array{}},{"completed",j::array{}}};auto p=directory(*s,true)/(id+".json");if(fs::exists(p))throw std::runtime_error("Story registration exists.");write(p,record);
 auto carry=state.to_config().child_or_empty("carryover_sides_start");auto& meta=child(child(carry,"variables"),"unified_story");meta["run_id"]=id;meta["account_id"]=s->account.account_id;meta["database_generation"]=s->account.database_generation;meta["campaign_id"]=campaign->id;state.set_carryover_sides_start(std::move(carry));return true;
 }catch(...){gui2::show_error_message("This story campaign could not register for your signed-in account. Your existing saves and heroes are unchanged. [story-register]");return false;}
}
bool prepare_story_scenario(saved_game& state,bool replay){if(replay)return true;const auto* campaign=find_story_campaign(state.classification().campaign);if(!campaign)return true;
 try{auto& scenario=state.get_starting_point();auto& vars=child(scenario,"variables");auto& meta=child(vars,"unified_story");const auto s=player_session();if(!s||meta["account_id"].str()!=s->account.account_id||meta["database_generation"].str()!=s->account.database_generation||meta["campaign_id"].str()!=campaign->id||!pp::uuid(meta["run_id"].str()))throw std::runtime_error("Story save belongs to another account or generation.");
 auto p=directory(*s,false)/(meta["run_id"].str()+".json");auto record=read(p);flush(*s,p,record);
 const auto sid=scenario["id"].str();if(std::find(campaign->scenario_ids.begin(),campaign->scenario_ids.end(),sid)==campaign->scenario_ids.end())throw std::runtime_error("Unregistered story scenario.");
 bool complete=false;for(const auto& done:record.at("completed").as_array())if(str(done.as_object(),"checkpoint_id")==meta["checkpoint_id"].str())complete=str(done.as_object(),"outcome")=="loss";
 if(meta["scenario_id"].str()!=sid||meta["checkpoint_id"].empty()||complete){vars.clear_children("unified_story_ledger");vars.remove_attribute("unified_story_tracking_error");meta["scenario_id"]=sid;meta["checkpoint_id"]=uuid();meta["sequence"]=static_cast<long long>(record.at("next_sequence").as_int64());}
 if(!state.is_mid_game_save()){patch_story_script_awards(scenario);
 auto& preload=scenario.add_child("event");preload["name"]="preload";preload["first_time_only"]=false;preload.add_child("lua")["code"]=std::string(story_harm_unit_lua)+"\n"+story_modify_unit_lua+"\n"+story_stored_bonus_lua;
 auto& participation=scenario.add_child("event");participation["name"]="start,recall,recruit,post advance,last breath,side turn end";participation["first_time_only"]=false;
 participation.add_child("lua")["code"]="for _,u in ipairs(wesnoth.units.find_on_map{}) do u.unified_story_credit=0 end";
for(auto& side:scenario.child_range("side"))for(auto& u:side.child_range("unit")){auto& uv=child(u,"variables");uv["unified_story_earned"]=0;uv["unified_story_checkpoint"]=meta["checkpoint_id"].str();uv.clear_children("unified_story_form");}}
 return true;
 }catch(...){gui2::show_error_message("This campaign save cannot resume for the signed-in account. Sign in to its original account and keep its private campaign records. [story-account]");return false;}
}
bool advance_story_scenario(saved_game& state) noexcept {try{
 auto carry=state.to_config().child_or_empty("carryover_sides_start");auto vars=carry.optional_child("variables");if(!vars)return true;auto meta=vars->optional_child("unified_story");if(!meta)return true;
 const auto next=carry["next_scenario"].str();if(next.empty()||next=="null")return true;
 const auto s=player_session();if(!s||(*meta)["account_id"].str()!=s->account.account_id||(*meta)["database_generation"].str()!=s->account.database_generation||!pp::uuid((*meta)["run_id"].str()))throw std::runtime_error("Story transition actor changed.");
 const auto record=read(directory(*s,false)/((*meta)["run_id"].str()+".json"));const auto& run=record.at("run").as_object();
 if(str(run,"run_id")!=(*meta)["run_id"].str()||str(run,"account_id")!=s->account.account_id||str(run,"database_generation")!=s->account.database_generation||str(run,"campaign_id")!=(*meta)["campaign_id"].str()||str(run,"campaign_id")!=state.classification().campaign)throw std::runtime_error("Story transition record differs.");
 const auto sequence=(*meta)["sequence"].to_long_long(-1);bool durable=false;for(const auto& done:record.at("completed").as_array()){const auto& row=done.as_object();if(str(row,"checkpoint_id")==(*meta)["checkpoint_id"].str()&&str(row,"scenario_id")==(*meta)["scenario_id"].str()&&str(row,"outcome")=="win")durable=true;}
 if(!durable||sequence<0||sequence==INT64_MAX||record.at("next_sequence").as_int64()!=sequence+1)throw std::runtime_error("Story transition requires the latest durable completed result.");
 (*meta)["checkpoint_id"]=uuid();(*meta)["sequence"]=static_cast<long long>(record.at("next_sequence").as_int64());(*meta)["scenario_id"]=next;
 vars->clear_children("unified_story_ledger");vars->remove_attribute("unified_story_tracking_error");state.set_carryover_sides_start(std::move(carry));return true;
 }catch(...){return false;}}
void record_story_form(unit& u) noexcept {try{const auto* m=marker();if(!m||!resources::gameboard||!resources::gameboard->map().on_board(u.get_location()))return;const auto& team=resources::gameboard->get_team(u.side());if(!team.is_local_human())return;const auto* alias=find_story_alias((*m)["campaign_id"].str(),u.id(),u.type_id(),u.variation());if(!alias)alias=find_story_alias((*m)["campaign_id"].str(),u.id(),u.type_id());if(!alias)return;
 auto& vars=u.variables();const auto checkpoint=(*m)["checkpoint_id"].str();if(vars["unified_story_checkpoint"].str()!=checkpoint){vars["unified_story_checkpoint"]=checkpoint;vars["unified_story_earned"]=0;vars.clear_children("unified_story_form");}
 bool seen=false;for(const auto& f:vars.child_range("unified_story_form"))if(f["id"].str()==alias->form_id)seen=true;if(!seen)vars.add_child("unified_story_form",config{"id",alias->form_id});
 auto& ledger=resources::gamedata->get_variable_cfg("unified_story_ledger");config* row=nullptr;for(auto& h:ledger.child_range("hero"))if(h["hero_id"].str()==alias->hero_id)row=&h;if(!row)row=&ledger.add_child("hero",config{"hero_id",alias->hero_id});
 (*row)["unit_id"]=alias->unit_id;(*row)["form_id"]=alias->form_id;(*row)["earned_experience"]=std::max((*row)["earned_experience"].to_long_long(),vars["unified_story_earned"].to_long_long());
 for(const auto& f:vars.child_range("unified_story_form")){bool found=false;for(const auto& old:row->child_range("form"))if(old["id"].str()==f["id"].str())found=true;if(!found)row->add_child("form",f);}
 }catch(...){tracking_error();}}
void capture_story_participants() noexcept {try{if(!resources::gameboard||!marker())return;for(auto& u:resources::gameboard->units())record_story_form(u);}catch(...){}}
void record_story_experience(unit& u,int earned) noexcept {try{record_story_form(u);if(earned<=0)return;const auto* m=marker();if(!m||!resources::gameboard||!resources::gameboard->get_team(u.side()).is_local_human())return;if(!find_story_alias((*m)["campaign_id"].str(),u.id(),u.type_id()))return;auto& vars=u.variables();auto amount=vars["unified_story_earned"].to_long_long();if(amount<0||amount>2147483647LL-earned)throw std::runtime_error("Story XP overflow.");vars["unified_story_earned"]=amount+earned;record_story_form(u);}catch(...){tracking_error();}}
void observe_story_completion(saved_game& state,level_result::type outcome) noexcept {if(outcome!=level_result::type::victory&&outcome!=level_result::type::defeat)return;try{
 const auto& scenario=state.get_starting_point();const auto& meta=scenario.child_or_empty("variables").child_or_empty("unified_story");if(meta.empty())return;if(scenario.child_or_empty("variables")["unified_story_tracking_error"].to_bool())throw std::runtime_error("Story accounting was incomplete.");const auto s=player_session();if(!s||meta["account_id"].str()!=s->account.account_id||meta["database_generation"].str()!=s->account.database_generation)throw std::runtime_error("Story actor changed.");auto p=directory(*s,false)/(meta["run_id"].str()+".json");auto record=read(p);const auto& run=record.at("run").as_object();
 const auto cid=meta["checkpoint_id"].str();for(const auto& done:record.at("completed").as_array())if(str(done.as_object(),"checkpoint_id")==cid){flush(*s,p,record);return;}
 std::map<std::string,j::object> rows;
 if(outcome==level_result::type::victory)for(const auto& h:scenario.child_or_empty("variables").child_or_empty("unified_story_ledger").child_range("hero")){
  const auto hero=h["hero_id"].str();const auto earned=h["earned_experience"].to_long_long();if(earned<0||earned>2147483647)throw std::runtime_error("Invalid earned XP.");j::array forms;for(const auto& f:h.child_range("form"))forms.emplace_back(f["id"].str());rows[hero]={{"hero_id",hero},{"unit_id",h["unit_id"].str()},{"form_id",h["form_id"].str()},{"earned_experience",earned},{"unlocked_forms",forms}};
 }
 j::array heroes;for(auto& [id,row]:rows)heroes.push_back(row);const auto replay_name="Story-"+meta["run_id"].str()+"-"+cid;
 const auto replay_path=fs::path(filesystem::get_saves_dir())/(replay_name+".gz");
 if(!fs::exists(replay_path)){savegame::replay_savegame replay_save(state,compression::format::gzip);if(!replay_save.save_game_automatic(false,replay_name))throw std::runtime_error("Story replay save failed.");}
 if(fs::is_symlink(replay_path)||!fs::is_regular_file(replay_path)||fs::file_size(replay_path)>32*1024*1024)throw std::runtime_error("Story replay invalid.");
 std::ifstream replay_file(replay_path,std::ios::binary);const std::string replay((std::istreambuf_iterator<char>(replay_file)),{});if(replay.empty()||replay_file.bad())throw std::runtime_error("Story replay incomplete.");
 auto next=scenario["next_scenario"].str();if(next=="null")next.clear();j::object checkpoint{{"run_id",meta["run_id"].str()},{"checkpoint_id",cid},{"sequence",meta["sequence"].to_long_long()},{"scenario_id",meta["scenario_id"].str()},{"next_scenario_id",next},{"outcome",outcome==level_result::type::victory?"win":"loss"},{"heroes",heroes},{"replay_sha256",digest(replay)},{"nonce",run.at("nonce")}};
 record.at("pending").as_array().push_back(checkpoint);record.at("completed").as_array().push_back(j::object{{"checkpoint_id",cid},{"scenario_id",meta["scenario_id"].str()},{"outcome",checkpoint.at("outcome")}});record["next_sequence"]=std::max(record.at("next_sequence").as_int64(),checkpoint.at("sequence").as_int64()+1);write(p,record);flush(*s,p,record);
 }catch(...){gui2::show_error_message("The campaign ended, but its hero result could not be confirmed. Keep this save and private game data; pending results retry when you reconnect. [story-result]");}}
}
