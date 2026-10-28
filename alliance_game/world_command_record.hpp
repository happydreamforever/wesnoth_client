// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "alliance_game/world_storage.hpp"
#include "alliance_native/account_protocol.hpp"
#include "alliance_native/world_protocol.hpp"
#include <boost/json.hpp>
#include <filesystem>
#include <cstdlib>
#include <fstream>
#include <stdexcept>
#include <optional>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <fcntl.h>
#include <unistd.h>
#endif

namespace alliance_game::world_command_record {
namespace fs=std::filesystem;
namespace json=boost::json;
inline bool uuid(const std::string& value){return world_uuid(value);}
inline void ordinary(const fs::path& path){
    for(auto at=path;!at.empty();at=at.parent_path()) {
        if(fs::is_symlink(at))throw std::runtime_error("Linked action storage is unavailable.");
#ifdef _WIN32
        const auto attributes=GetFileAttributesW(at.c_str());
        if(attributes!=INVALID_FILE_ATTRIBUTES&&(attributes&FILE_ATTRIBUTE_REPARSE_POINT))
            throw std::runtime_error("Reparsed action storage is unavailable.");
#endif
        if(at==at.parent_path())break;
    }
}
inline fs::path path(const std::string& actor,const std::string& generation,bool create){
    if(!uuid(actor)||!uuid(generation))throw std::runtime_error("Pending action identity is invalid.");
    const auto* raw=std::getenv("WESNOTH_ALLIANCE_PROFILE");
    if(!raw||!*raw)throw std::runtime_error("Private action storage is unavailable.");
    const fs::path profile(raw);
    if(!profile.is_absolute()||(profile.filename()!="alliance-bundled-profile.json"&&profile.filename()!="alliance-public-profile.json"))
        throw std::runtime_error("Private action storage is invalid.");
    auto root=profile.parent_path();ordinary(root);
    if(!fs::is_directory(root))throw std::runtime_error("Private action directory changed.");
    for(const auto& part:{std::string("alliance-world-commands"),generation,actor}){
        root/=part;ordinary(root);
        if(create)world_private_directory(root);
        else if(fs::exists(root)&&!fs::is_directory(root))throw std::runtime_error("Pending action storage changed.");
    }
    return root/"pending.json";
}
inline void validate(const json::object& doc,const std::string& actor,const std::string& generation){
    if(doc.size()!=5||doc.at("format")!="alliance-world-command-1"||doc.at("actor_id")!=json::value(actor)||doc.at("database_generation")!=json::value(generation)||!doc.at("operation").is_string()||!doc.at("payload").is_object())
        throw std::runtime_error("Pending action owner or generation differs.");
    alliance::world_protocol::request("world_command",{{"operation",doc.at("operation")},{"payload",doc.at("payload")}});
}
inline std::optional<json::object> read(const std::string& actor,const std::string& generation){
    const auto file=path(actor,generation,false);ordinary(file);
    if(!fs::exists(file))return std::nullopt;
    std::string bytes;
    const auto links=fs::hard_link_count(file);
    if(links==2){
        // A durable archive hard-link can survive a crash immediately before
        // unlinking pending. Accept only that exact, identity-bound pair.
        if(!fs::is_regular_file(file)||fs::file_size(file)>65536)throw std::runtime_error("Pending action record changed.");
        std::ifstream stream(file,std::ios::binary);bytes.assign(65537,'\0');stream.read(bytes.data(),bytes.size());
        if(stream.bad()||stream.gcount()<0||stream.gcount()>65536)throw std::runtime_error("Pending action record exceeds its limit.");
        bytes.resize(stream.gcount());
    }else bytes=world_read_file(file,65536);
    auto doc=alliance::account::parse_document(bytes); // Bounded depth; duplicate keys rejected.
    validate(doc,actor,generation);
    if(links==2){const auto receipt=file.parent_path()/(std::string(doc.at("payload").as_object().at("command_id").as_string())+".json");ordinary(receipt);if(!fs::exists(receipt)||!fs::equivalent(receipt,file))throw std::runtime_error("Unexpected linked action record.");}
    return doc;
}
inline void save(const std::string& actor,const std::string& generation,const std::string& operation,const json::object& payload){
    const auto file=path(actor,generation,true);
    const json::object document{{"format","alliance-world-command-1"},{"actor_id",actor},{"database_generation",generation},{"operation",operation},{"payload",payload}};
    validate(document,actor,generation);
    if(const auto prior=read(actor,generation)){
        if(*prior==document)return;
        throw std::runtime_error("Check the previous pending action first.");
    }
    world_write_file(file,json::serialize(document),65536);
}
inline void acknowledge(const std::string& actor,const std::string& generation,const std::string& id){
    if(!uuid(id))throw std::runtime_error("Action acknowledgement is invalid.");
    const auto record=read(actor,generation);if(!record)return;
    if(record->at("payload").as_object().at("command_id")!=json::value(id))throw std::runtime_error("Pending acknowledgement differs.");
    const auto file=path(actor,generation,false),receipt=file.parent_path()/(id+".json");ordinary(receipt);
    // A crash after creating the archive but before removing pending is resumable.
    if(fs::exists(receipt)){
        if(!fs::equivalent(receipt,file)&&world_read_file(receipt,65536)!=world_read_file(file,65536))throw std::runtime_error("An action acknowledgement differs.");
        fs::remove(file);
    }else{
#ifdef _WIN32
        if(!MoveFileExW(file.c_str(),receipt.c_str(),MOVEFILE_WRITE_THROUGH))throw std::runtime_error("Action history could not be committed.");
#else
        // link is atomic and never replaces existing history.
        if(::link(file.c_str(),receipt.c_str())!=0)throw std::runtime_error("Action history could not be committed.");
        if(::unlink(file.c_str())!=0)throw std::runtime_error("Pending action could not be retired.");
#endif
    }
#ifndef _WIN32
    const int parent=::open(file.parent_path().c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW);
    if(parent<0)throw std::runtime_error("Action history directory unavailable.");
    const int result=::fsync(parent);::close(parent);
    if(result)throw std::runtime_error("Action history could not be synchronized.");
#endif
}
} // namespace alliance_game::world_command_record
