// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/shared_result.hpp"
#include "filesystem.hpp"
#include "saved_game.hpp"
#include "savegame.hpp"
#include "serialization/parser.hpp"
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <algorithm>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <fcntl.h>
#include <unistd.h>
#include <zlib.h>
#include <array>
namespace alliance_game {
namespace {
namespace fs=std::filesystem;
struct Active { PracticePlan plan;std::string owner; };
std::optional<Active> active;
std::string notice;
constexpr std::size_t replay_limit=32*1024*1024;
bool uuid(const std::string& s) {
    if(s.size()!=36)return false;
    for(std::size_t i=0;i<s.size();++i)if(i==8||i==13||i==18||i==23){if(s[i]!='-')return false;}else if(!((s[i]>='0'&&s[i]<='9')||(s[i]>='a'&&s[i]<='f')))return false;return true;
}
std::string hex(const unsigned char* p,std::size_t n){static const char h[]="0123456789abcdef";std::string s;for(std::size_t i=0;i<n;++i){s+=h[p[i]>>4];s+=h[p[i]&15];}return s;}
std::string digest(const std::string& s){unsigned char h[EVP_MAX_MD_SIZE];unsigned n=0;if(EVP_Digest(s.data(),s.size(),h,&n,EVP_sha256(),nullptr)!=1||n!=32)throw std::runtime_error("Replay hashing unavailable.");return hex(h,n);}
void directory(const fs::path& p){if(fs::exists(p)){if(!fs::is_directory(p)||fs::is_symlink(p))throw std::runtime_error("Invalid shared storage.");}else fs::create_directory(p);fs::permissions(p,fs::perms::owner_all,fs::perm_options::replace);}
fs::path record(const std::string& account,const std::string& run,bool create=false){
    if(!uuid(account)||!uuid(run))throw std::invalid_argument("Invalid shared identity.");
    const char* p=std::getenv("WESNOTH_ALLIANCE_PROFILE");if(!p)throw std::runtime_error("Private shared storage is unavailable.");
    const fs::path profile(p);if(!profile.is_absolute()||(profile.filename()!="alliance-public-profile.json"&&profile.filename()!="alliance-bundled-profile.json")||!fs::is_directory(profile.parent_path())||fs::is_symlink(profile.parent_path()))throw std::runtime_error("Invalid shared profile directory.");
    const auto root=profile.parent_path()/"alliance-shared",owner=root/account;
    for(const auto& path:{root,owner}) {if(create)directory(path);if(fs::exists(path)&&(!fs::is_directory(path)||fs::is_symlink(path)))throw std::runtime_error("Invalid shared directory.");}
    return owner/(run+".cfg");
}
std::string bounded(const fs::path& p,std::size_t bound){if(!fs::is_regular_file(p)||fs::is_symlink(p)||fs::file_size(p)>bound)throw std::runtime_error("Invalid shared file.");std::ifstream in(p,std::ios::binary);std::string s(bound+1,'\0');in.read(s.data(),s.size());if(in.bad()||static_cast<std::size_t>(in.gcount())>bound)throw std::runtime_error("Shared file cannot be read.");s.resize(in.gcount());return s;}
config encode(const SharedPending& p){return config{"format","alliance-shared-result-v1","run_id",p.run_id,"account_id",p.account_id,"winner_team_id",p.winner_team_id,"draw",p.draw,"replay_sha256",p.replay_sha256,"replay_filename",p.replay_filename,"replay_bytes",static_cast<unsigned long long>(p.replay_bytes),"status",p.status};}
SharedPending decode(const fs::path& p){const auto c=io::read(bounded(p,8192));SharedPending s{c["run_id"].str(),c["account_id"].str(),c["winner_team_id"].str(),c["replay_sha256"].str(),c["replay_filename"].str(),c["status"].str(),c["draw"].to_bool(),c["replay_bytes"].to_unsigned()};if(c["format"].str()!="alliance-shared-result-v1"||!uuid(s.run_id)||!uuid(s.account_id)||p.filename()!=s.run_id+".cfg"||p.parent_path().filename()!=s.account_id||s.replay_filename!="Alliance-battle-"+s.run_id+"-"+s.account_id+".gz"||(s.status!="pending"&&s.status!="acknowledged")||s.replay_bytes==0||s.replay_bytes>replay_limit||(s.draw?!s.winner_team_id.empty():!uuid(s.winner_team_id))||s.replay_sha256.size()!=64||!std::all_of(s.replay_sha256.begin(),s.replay_sha256.end(),[](char x){return(x>='0'&&x<='9')||(x>='a'&&x<='f');}))throw std::runtime_error("Invalid shared result.");return s;}
void write_bytes(const fs::path& path,const std::string& bytes,std::size_t limit){if(bytes.size()>limit)throw std::runtime_error("Shared record exceeds limit.");unsigned char r[12];if(RAND_bytes(r,sizeof(r))!=1)throw std::runtime_error("Storage randomness unavailable.");const auto temp=path.string()+".tmp-"+hex(r,sizeof(r));int fd=::open(temp.c_str(),O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW,0600);if(fd<0)throw std::runtime_error("Shared record cannot be created.");try{std::size_t pos=0;while(pos<bytes.size()){auto n=::write(fd,bytes.data()+pos,bytes.size()-pos);if(n<=0)throw std::runtime_error("Shared record cannot be written.");pos+=n;}if(::fsync(fd)!=0)throw std::runtime_error("Shared record cannot be synchronized.");::close(fd);fd=-1;if(fs::exists(path)&&(!fs::is_regular_file(path)||fs::is_symlink(path)))throw std::runtime_error("Shared record changed.");fs::rename(temp,path);const int parent=::open(path.parent_path().c_str(),O_RDONLY|O_DIRECTORY);if(parent<0)throw std::runtime_error("Shared directory cannot be synchronized.");const int result=::fsync(parent);::close(parent);if(result!=0)throw std::runtime_error("Shared directory cannot be synchronized.");}catch(...){if(fd>=0)::close(fd);std::error_code ec;fs::remove(temp,ec);throw;}}
void write(const fs::path& path,const SharedPending& value){std::ostringstream out;io::write(out,encode(value));write_bytes(path,out.str(),8192);}
config checked_download(const std::string& owner,const std::string& run){
    const auto path=record(owner,run).string()+".download";const auto c=io::read(bounded(path,8192));
    const auto source=c["source_account"].str(),hash=c["sha256"].str();
    if(c["format"].str()!="alliance-downloaded-replay-v1"||c["run_id"].str()!=run||c["owner"].str()!=owner||!uuid(source)||
       c["filename"].str()!="Alliance-server-battle-"+run+"-"+source+".gz"||hash.size()!=64||
       !std::all_of(hash.begin(),hash.end(),[](char x){return(x>='0'&&x<='9')||(x>='a'&&x<='f');})||
       c["bytes"].to_unsigned()==0||c["bytes"].to_unsigned()>replay_limit)throw std::runtime_error("Invalid downloaded replay record.");
    const auto bytes=bounded(fs::path(filesystem::get_saves_dir())/c["filename"].str(),replay_limit);
    if(bytes.size()!=c["bytes"].to_unsigned()||digest(bytes)!=hash)throw std::runtime_error("Downloaded replay changed.");return c;
}
void validate_replay_bytes(const std::string& bytes,const std::string& run){
    z_stream stream{};if(inflateInit2(&stream,16+MAX_WBITS)!=Z_OK)throw std::runtime_error("Replay decompression unavailable.");
    struct Cleanup{z_stream* s;~Cleanup(){inflateEnd(s);}} cleanup{&stream};
    stream.next_in=reinterpret_cast<Bytef*>(const_cast<char*>(bytes.data()));stream.avail_in=bytes.size();
    std::string plain;std::array<char,16384> buffer;int status=Z_OK;
    while(status==Z_OK){stream.next_out=reinterpret_cast<Bytef*>(buffer.data());stream.avail_out=buffer.size();status=inflate(&stream,Z_NO_FLUSH);const auto n=buffer.size()-stream.avail_out;if(plain.size()+n>128*1024*1024)throw std::runtime_error("Replay exceeds its decompression limit.");plain.append(buffer.data(),n);}
    if(status!=Z_STREAM_END||stream.avail_in)throw std::runtime_error("Replay compression is invalid.");
    const auto c=io::read(plain);const auto& v=c.child_or_empty("replay_start").child_or_empty("variables");
    if(!v["alliance_shared"].to_bool()||v["alliance_shared_run_id"].str()!=run||(v["alliance_shared_version"].str()!="alliance-shared-v1"&&v["alliance_shared_version"].str()!="alliance-shared-v2")||!c.has_child("replay"))throw std::runtime_error("Downloaded replay belongs to another game.");
}
}
SharedActiveLaunch::SharedActiveLaunch(const PracticePlan& p,const std::string& a){if(active||!uuid(p.battle_id)||!uuid(a)||std::none_of(p.seats.begin(),p.seats.end(),[&](const auto& s){return s.id==a;}))throw std::runtime_error("Invalid shared launch.");active=Active{p,a};}
SharedActiveLaunch::~SharedActiveLaunch(){active.reset();}
bool shared_save(const saved_game& s)noexcept{try{return s.get_starting_point().child_or_empty("variables")["alliance_shared"].to_bool()||s.replay_start().child_or_empty("variables")["alliance_shared"].to_bool();}catch(...){return true;}}
std::vector<SharedPending> inspect_pending_shared(const std::string& a){const auto folder=record(a,"00000000-0000-0000-0000-000000000000").parent_path();std::vector<SharedPending> rows;if(!fs::exists(folder))return rows;std::size_t count=0;for(const auto& e:fs::directory_iterator(folder)){if(++count>4096)throw std::runtime_error("Shared history exceeds its limit.");if(e.path().extension()==".cfg")rows.push_back(decode(e.path()));}return rows;}
std::string read_shared_replay(const SharedPending& p){auto exact=decode(record(p.account_id,p.run_id));if(exact.replay_sha256!=p.replay_sha256||exact.winner_team_id!=p.winner_team_id||exact.draw!=p.draw)throw std::runtime_error("Shared result changed.");const auto bytes=bounded(fs::path(filesystem::get_saves_dir())/exact.replay_filename,replay_limit);if(bytes.size()!=exact.replay_bytes||digest(bytes)!=exact.replay_sha256)throw std::runtime_error("Shared replay changed.");return bytes;}
void acknowledge_shared_result(const SharedPending& p){const auto path=record(p.account_id,p.run_id);auto exact=decode(path);if(exact.replay_sha256!=p.replay_sha256||exact.winner_team_id!=p.winner_team_id||exact.draw!=p.draw)throw std::runtime_error("Shared acknowledgement does not match.");if(exact.status=="pending"){exact.status="acknowledged";write(path,exact);}}
std::string verified_shared_replay(const std::string& a,const std::string& r){const auto path=record(a,r);if(fs::exists(path)){const auto p=decode(path);(void)read_shared_replay(p);return p.replay_filename;}return checked_download(a,r)["filename"].str();}
void store_downloaded_shared_replay(const std::string& owner,const std::string& run,const std::string& source,const std::string& hash,const std::string& bytes){
    if(!uuid(source)||bytes.empty()||bytes.size()>replay_limit||digest(bytes)!=hash)throw std::runtime_error("Downloaded replay hash does not match.");
    validate_replay_bytes(bytes,run);
    const auto target=record(owner,run,true).string()+".download";
    if(fs::exists(target)){const auto prior=checked_download(owner,run);if(prior["sha256"].str()!=hash||prior["source_account"].str()!=source)throw std::runtime_error("A different replay is already retained.");return;}
    const auto name="Alliance-server-battle-"+run+"-"+source+".gz";const fs::path replay=fs::path(filesystem::get_saves_dir())/name;
    if(fs::exists(replay)){if(bounded(replay,replay_limit)!=bytes)throw std::runtime_error("A different replay file already exists.");}
    else write_bytes(replay,bytes,replay_limit);
    config meta{"format","alliance-downloaded-replay-v1","run_id",run,"owner",owner,"source_account",source,"sha256",hash,"bytes",static_cast<unsigned long long>(bytes.size()),"filename",name};
    std::ostringstream out;io::write(out,meta);write_bytes(target,out.str(),8192);
}
void observe_shared_completion(saved_game& state,level_result::type result)noexcept{
    if(!active||(result!=level_result::type::victory&&result!=level_result::type::defeat))return;
    try{
        const auto& cfg=state.get_starting_point();const auto& vars=cfg.child_or_empty("variables");
        if(!vars["alliance_shared"].to_bool()||vars["alliance_shared_run_id"].str()!=active->plan.battle_id||vars["alliance_shared_version"].str()!=(active->plan.shared_format==2?"alliance-shared-v2":"alliance-shared-v1"))throw std::runtime_error("Shared result identity mismatch.");
        const auto own=std::find_if(active->plan.seats.begin(),active->plan.seats.end(),[&](const auto& s){return s.id==active->owner;});
        const auto rival=std::find_if(active->plan.seats.begin(),active->plan.seats.end(),[&](const auto& s){return s.team_id!=own->team_id;});
        if(rival==active->plan.seats.end())throw std::runtime_error("Shared opponent missing.");
        SharedPending pending;pending.run_id=active->plan.battle_id;pending.account_id=active->owner;pending.status="pending";
        const auto terminal=vars["alliance_terminal_kind"].str();
        if(!terminal.empty()) {
            const auto winner=vars["alliance_terminal_winner_team_id"].str();const bool draw=vars["alliance_terminal_draw"].to_bool();
            if(vars["alliance_terminal_run_id"].str()!=active->plan.battle_id ||
               (terminal!="surrender"&&terminal!="draw") || (terminal=="draw"?(!draw||!winner.empty()):(draw||(winner!=own->team_id&&winner!=rival->team_id))))
                throw std::runtime_error("Invalid synchronized terminal result.");
            pending.draw=draw;pending.winner_team_id=winner;
        } else {
            bool own_alive=false,rival_alive=false;
            for(const auto& side:cfg.child_range("side"))for(const auto& unit:side.child_range("unit"))if(unit["canrecruit"].to_bool()&&unit["hitpoints"].to_int()>0){if(side["team_name"].str()==own->team_id)own_alive=true;else rival_alive=true;}
            if(own_alive&&rival_alive){notice="Your local game ended while both teams still had leaders. No final result was reported; reconnect to the active shared battle.";return;}
            pending.draw=!own_alive&&!rival_alive;pending.winner_team_id=pending.draw?"":own_alive?own->team_id:rival->team_id;
        }
        const auto path=record(pending.account_id,pending.run_id,true);
        if(fs::exists(path)){const auto old=decode(path);if(old.draw!=pending.draw||old.winner_team_id!=pending.winner_team_id)throw std::runtime_error("Conflicting shared result.");return;}
        if(inspect_pending_shared(pending.account_id).size()>=4096)throw std::runtime_error("Shared result storage is full.");
        const auto name="Alliance-battle-"+pending.run_id+"-"+pending.account_id;pending.replay_filename=name+".gz";
        const auto replay=fs::path(filesystem::get_saves_dir())/pending.replay_filename;if(fs::exists(replay))throw std::runtime_error("Shared replay already exists.");
        savegame::replay_savegame save(state,compression::format::gzip);if(!save.save_game_automatic(false,name))throw std::runtime_error("Shared replay could not be saved.");
        const auto bytes=bounded(replay,replay_limit);if(bytes.empty())throw std::runtime_error("Shared replay is empty.");pending.replay_bytes=bytes.size();pending.replay_sha256=digest(bytes);write(path,pending);
        notice="Shared result and replay saved. The replay will be uploaded; both captains must confirm the result before rewards are settled.";
    }catch(...){notice="The shared game ended, but its replay could not be saved safely. No result was submitted. [shared-save]";}
}
std::string take_shared_notice(){auto s=std::move(notice);notice.clear();return s;}
}
