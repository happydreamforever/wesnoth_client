// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/progression_result.hpp"
#include "alliance_game/progression_scenario.hpp"
#include "filesystem.hpp"
#include "saved_game.hpp"
#include "savegame.hpp"
#include "serialization/parser.hpp"
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <algorithm>
#include <array>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <system_error>
#include <fcntl.h>
#include <unistd.h>
namespace alliance_game {
namespace {
namespace fs=std::filesystem;
std::optional<ProgressionRun> active;
std::string notice;
constexpr std::size_t max_record=8192,max_replay=32*1024*1024;
bool uuid(const std::string& s) {
    if(s.size()!=36) return false;
    for(std::size_t i=0;i<s.size();++i) { if(i==8||i==13||i==18||i==23) {if(s[i]!='-')return false;} else if(!((s[i]>='0'&&s[i]<='9')||(s[i]>='a'&&s[i]<='f')))return false; }
    return true;
}
std::string hex(const unsigned char* bytes,std::size_t size) {
    static const char digits[]="0123456789abcdef";std::string out;out.reserve(size*2);
    for(std::size_t i=0;i<size;++i) {out+=digits[bytes[i]>>4];out+=digits[bytes[i]&15];}return out;
}
fs::path private_root() {
    const char* value=std::getenv("WESNOTH_ALLIANCE_PROFILE");
    if(!value||!*value) throw std::runtime_error("Private mission storage is unavailable.");
    const fs::path profile(value);
    if(!profile.is_absolute()||profile.filename()!="alliance-public-profile.json") throw std::runtime_error("Invalid private mission storage.");
    const auto base=profile.parent_path();
    if(!fs::is_directory(base)||fs::is_symlink(base)) throw std::runtime_error("Invalid private mission directory.");
    return base/"alliance-progression";
}
void directory(const fs::path& path) {
    if(fs::exists(path)) {if(!fs::is_directory(path)||fs::is_symlink(path)) throw std::runtime_error("Invalid mission directory.");}
    else fs::create_directory(path);
    fs::permissions(path,fs::perms::owner_all,fs::perm_options::replace);
}
fs::path record_path(const std::string& owner,const std::string& id,bool create=false) {
    if(!uuid(owner)||!uuid(id))throw std::invalid_argument("Invalid mission identity.");
    const auto root=private_root();if(create)directory(root);
    if(fs::exists(root)&&(!fs::is_directory(root)||fs::is_symlink(root)))throw std::runtime_error("Invalid mission directory.");
    const auto own=root/owner;if(create)directory(own);
    if(fs::exists(own)&&(!fs::is_directory(own)||fs::is_symlink(own)))throw std::runtime_error("Invalid mission owner directory.");
    return own/(id+".cfg");
}
std::string read_bounded(const fs::path& path,std::size_t max) {
    if(!fs::is_regular_file(path)||fs::is_symlink(path)||fs::file_size(path)>max)throw std::runtime_error("Invalid mission record.");
    std::ifstream input(path,std::ios::binary);
    std::string data(max+1, '\0');input.read(data.data(),static_cast<std::streamsize>(data.size()));
    const auto size=input.gcount();if(input.bad())throw std::runtime_error("Mission record cannot be read.");
    if(size<0||static_cast<std::size_t>(size)>max)throw std::runtime_error("Mission record exceeds its limit.");
    data.resize(static_cast<std::size_t>(size));return data;
}
void write_atomic(const fs::path& path,const config& cfg) {
    std::ostringstream out;io::write(out,cfg);const auto bytes=out.str();if(bytes.size()>max_record)throw std::runtime_error("Mission record exceeds its limit.");
    unsigned char random[12];if(RAND_bytes(random,sizeof(random))!=1)throw std::runtime_error("Mission storage randomness unavailable.");
    const auto temporary=path.string()+".tmp-"+hex(random,sizeof(random));
    int fd=::open(temporary.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW,0600);
    if(fd<0)throw std::runtime_error("Mission record cannot be created.");
    try {
        std::size_t offset=0;while(offset<bytes.size()) {const auto n=::write(fd,bytes.data()+offset,bytes.size()-offset);if(n<=0)throw std::runtime_error("Mission record cannot be written.");offset+=static_cast<std::size_t>(n);}
        if(::fsync(fd)!=0)throw std::runtime_error("Mission record cannot be synchronized.");
        if(::close(fd)!=0){fd=-1;throw std::runtime_error("Mission record cannot be closed.");}fd=-1;
        if(fs::exists(path)&&(!fs::is_regular_file(path)||fs::is_symlink(path)))throw std::runtime_error("Mission record was replaced.");
        fs::rename(temporary,path);
        const int parent=::open(path.parent_path().c_str(),O_RDONLY|O_DIRECTORY);
        if(parent<0)throw std::runtime_error("Mission directory cannot be synchronized.");
        const int status=::fsync(parent);::close(parent);if(status!=0)throw std::runtime_error("Mission directory cannot be synchronized.");
    } catch(...) {if(fd>=0)::close(fd);std::error_code ignored;fs::remove(temporary,ignored);throw;}
}
config encode(const ProgressionPending& p) {
    config c;c["format"]="alliance-private-progression-v1";
    c["id"]=p.run.id;c["account_id"]=p.run.account_id;c["username"]=p.run.username;c["mission_id"]=p.run.mission_id;
    c["mission_version"]=p.run.mission_version;c["ruleset_version"]=p.run.ruleset_version;c["unit_type"]=p.run.unit_type;c["leader_level"]=p.run.leader_level;c["nonce"]=p.run.nonce;
    c["status"]=p.status;c["outcome"]=p.outcome;c["replay_sha256"]=p.replay_sha256;c["replay_filename"]=p.replay_filename;c["replay_bytes"]=static_cast<unsigned long long>(p.replay_bytes);return c;
}
ProgressionPending decode(const fs::path& file) {
    const auto c=io::read(read_bounded(file,max_record));ProgressionPending p;
    p.run={c["id"].str(),c["account_id"].str(),c["username"].str(),c["mission_id"].str(),c["mission_version"].str(),c["ruleset_version"].str(),c["unit_type"].str(),c["nonce"].str(),c["leader_level"].to_int()};
    p.status=c["status"].str();p.outcome=c["outcome"].str();p.replay_sha256=c["replay_sha256"].str();p.replay_filename=c["replay_filename"].str();p.replay_bytes=c["replay_bytes"].to_unsigned();
    if(c["format"].str()!="alliance-private-progression-v1"||!valid_progression_run(p.run)||
       (p.status!="started"&&p.status!="pending"&&p.status!="acknowledged"&&p.status!="cancelled")||
       file.filename()!=p.run.id+".cfg"||file.parent_path().filename()!=p.run.account_id)throw std::runtime_error("Invalid mission record.");
    if(p.status=="pending"||p.status=="acknowledged") {
        if((p.outcome!="win"&&p.outcome!="loss")||p.replay_sha256.size()!=64||p.replay_bytes==0||p.replay_bytes>max_replay||
           p.replay_filename!="Alliance-mission-"+p.run.id+".gz"||
           !std::all_of(p.replay_sha256.begin(),p.replay_sha256.end(),[](char x){return(x>='0'&&x<='9')||(x>='a'&&x<='f');}))throw std::runtime_error("Invalid saved mission result.");
    } else if(!p.outcome.empty()||!p.replay_sha256.empty()||!p.replay_filename.empty()||p.replay_bytes)throw std::runtime_error("Invalid unfinished mission record.");
    return p;
}
std::string digest(const std::string& bytes) {
    unsigned char hash[EVP_MAX_MD_SIZE];unsigned int size=0;
    if(EVP_Digest(bytes.data(),bytes.size(),hash,&size,EVP_sha256(),nullptr)!=1||size!=32)throw std::runtime_error("Replay hash unavailable.");return hex(hash,size);
}
}
void register_progression_run(const ProgressionRun& run) {
    if(!valid_progression_run(run))throw std::invalid_argument("Invalid mission registration.");
    const auto path=record_path(run.account_id,run.id,true);
    if(fs::exists(path))throw std::runtime_error("This mission run was already registered. Cancel it before starting a new run.");
    std::size_t records=0;for(const auto& entry:fs::directory_iterator(path.parent_path())) { (void)entry;if(++records>=4096)throw std::runtime_error("Mission history storage is full."); }
    write_atomic(path,encode(ProgressionPending{run,"started","","","",0}));
}
ProgressionActiveLaunch::ProgressionActiveLaunch(const ProgressionRun& run) {
    if(active||!valid_progression_run(run))throw std::runtime_error("Another progression mission is active.");
    auto saved=decode(record_path(run.account_id,run.id));
    if(saved.status!="started"||!same_progression_run(saved.run,run))throw std::runtime_error("Mission registration does not match.");active=run;
}
ProgressionActiveLaunch::~ProgressionActiveLaunch(){active.reset();}
std::vector<ProgressionPending> inspect_pending_progression(const std::string& owner) {
    if(!uuid(owner))throw std::invalid_argument("Invalid mission owner.");
    const auto path=record_path(owner,"00000000-0000-0000-0000-000000000000").parent_path();
    std::vector<ProgressionPending> result;if(!fs::exists(path))return result;
    std::size_t seen=0;for(const auto& entry:fs::directory_iterator(path)) {
        if(++seen>4096)throw std::runtime_error("Mission history exceeds its limit.");
        if(entry.path().extension()!=".cfg")continue;auto saved=decode(entry.path());
        if(saved.run.account_id!=owner)throw std::runtime_error("Mission owner does not match.");
        result.push_back(std::move(saved));
    }
    std::sort(result.begin(),result.end(),[](const auto&a,const auto&b){return a.run.id<b.run.id;});return result;
}
void acknowledge_progression_result(const std::string& owner,const std::string& id,const std::string& outcome) {
    const auto path=record_path(owner,id);auto record=decode(path);
    if((record.status!="pending"&&record.status!="acknowledged")||record.outcome!=outcome)throw std::runtime_error("Mission acknowledgement does not match.");
    if(record.status=="pending"){record.status="acknowledged";write_atomic(path,encode(record));}
}
void acknowledge_progression_cancel(const std::string& owner,const std::string& id) {
    const auto path=record_path(owner,id);if(!fs::exists(path))return;auto record=decode(path);
    if(record.status=="cancelled")return;
    if(record.status!="started")throw std::runtime_error("A completed local result cannot be discarded as an interruption.");
    record.status="cancelled";write_atomic(path,encode(record));
}
std::string verified_progression_replay(const std::string& owner,const std::string& id) {
    const auto record=decode(record_path(owner,id));
    if(record.status!="pending"&&record.status!="acknowledged")throw std::runtime_error("This mission has no completed replay.");
    const auto bytes=read_bounded(fs::path(filesystem::get_saves_dir())/record.replay_filename,max_replay);
    if(bytes.size()!=record.replay_bytes||digest(bytes)!=record.replay_sha256)throw std::runtime_error("Saved mission replay does not match its result.");
    return record.replay_filename;
}
bool progression_save(const saved_game& state) noexcept {
    try{return state.get_starting_point().child_or_empty("variables")["alliance_progression"].to_bool()||state.replay_start().child_or_empty("variables")["alliance_progression"].to_bool();}catch(...){return true;}
}
void observe_progression_completion(saved_game& state,level_result::type result) noexcept {
    if(!active||(result!=level_result::type::victory&&result!=level_result::type::defeat))return;
    try {
        if(!progression_marker_matches(state.get_starting_point(),*active))throw std::runtime_error("Mission result does not match registration.");
        const auto path=record_path(active->account_id,active->id);auto record=decode(path);
        if(!same_progression_run(record.run,*active))throw std::runtime_error("Mission owner changed.");
        if(record.status!="started")return;
        const std::string name="Alliance-mission-"+active->id;
        const fs::path replay=fs::path(filesystem::get_saves_dir())/(name+".gz");
        if(fs::exists(replay))throw std::runtime_error("A replay already exists for this mission.");
        savegame::replay_savegame save(state,compression::format::gzip);
        if(!save.save_game_automatic(false,name))throw std::runtime_error("Mission replay could not be saved.");
        const auto bytes=read_bounded(replay,max_replay);
        if(bytes.empty())throw std::runtime_error("Mission replay is empty.");
        record.status="pending";record.outcome=result==level_result::type::victory?"win":"loss";
        record.replay_filename=replay.filename().string();record.replay_bytes=bytes.size();record.replay_sha256=digest(bytes);
        write_atomic(path,encode(record));
        notice="Mission result and replay saved. Open Alliance to submit this result to your Windows server. Experience is awarded only after the server confirms settlement.";
    } catch(...) {notice="The mission ended, but its result could not be saved safely. No reward was submitted. Open Alliance to inspect the active run. [progression-save]";}
}
std::string take_progression_notice(){auto value=std::move(notice);notice.clear();return value;}
}
