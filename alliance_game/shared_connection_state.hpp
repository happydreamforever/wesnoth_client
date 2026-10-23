// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
// Implementation-only state: stock wesnothd_connection's layout stays unchanged.
#include "alliance_native/transport.hpp"
#include <boost/asio/steady_timer.hpp>
#include <openssl/x509.h>
#include <openssl/evp.h>
#include <zlib.h>
#include <array>
#include <map>
#include <memory>
#include <mutex>
#include <sstream>
#include <stdexcept>
namespace {
constexpr std::size_t alliance_socket_frame_limit=64*1024;
constexpr std::size_t alliance_socket_plain_limit=2*1024*1024;
struct alliance_socket_state {
    alliance::TrustedProfile profile;
    boost::asio::steady_timer timer;
    bool partial_frame=false;
    alliance_socket_state(const alliance::TrustedProfile& p,boost::asio::io_context& io):profile(p),timer(io){}
};
std::mutex alliance_socket_registry_mutex;
std::map<const void*,std::shared_ptr<alliance_socket_state>> alliance_socket_registry;
std::shared_ptr<alliance_socket_state> alliance_socket_lookup(const void* key) {
    std::lock_guard<std::mutex> lock(alliance_socket_registry_mutex);
    const auto it=alliance_socket_registry.find(key);return it==alliance_socket_registry.end()?nullptr:it->second;
}
void alliance_socket_insert(const void* key,std::shared_ptr<alliance_socket_state> state) {
    std::lock_guard<std::mutex> lock(alliance_socket_registry_mutex);
    if(alliance_socket_registry.size()>=8||!alliance_socket_registry.emplace(key,std::move(state)).second)
        throw std::runtime_error("Too many Alliance connections.");
}
void alliance_socket_erase(const void* key) {
    std::lock_guard<std::mutex> lock(alliance_socket_registry_mutex);alliance_socket_registry.erase(key);
}
std::string alliance_certificate_pin(X509* cert) {
    unsigned char value[EVP_MAX_MD_SIZE];unsigned int size=0;
    if(!cert||X509_digest(cert,EVP_sha256(),value,&size)!=1||size!=32)return {};
    static const char hex[]="0123456789abcdef";std::string result;
    for(unsigned int i=0;i<size;++i){result+=hex[value[i]>>4];result+=hex[value[i]&15];}return result;
}
std::string alliance_inflate_frame(std::istream& input) {
    const std::string compressed{std::istreambuf_iterator<char>(input),{}};
    if(compressed.empty()||compressed.size()>alliance_socket_frame_limit)throw std::runtime_error("Invalid Alliance frame.");
    z_stream stream{};
    if(inflateInit2(&stream,16+MAX_WBITS)!=Z_OK)throw std::runtime_error("Alliance decompression unavailable.");
    struct finish {z_stream* s;~finish(){inflateEnd(s);}} cleanup{&stream};
    stream.next_in=reinterpret_cast<Bytef*>(const_cast<char*>(compressed.data()));stream.avail_in=compressed.size();
    std::string plain;std::array<char,8192> buffer{};
    int result=Z_OK;
    while(result==Z_OK) {
        stream.next_out=reinterpret_cast<Bytef*>(buffer.data());stream.avail_out=buffer.size();
        result=inflate(&stream,Z_NO_FLUSH);const auto count=buffer.size()-stream.avail_out;
        if(plain.size()+count>alliance_socket_plain_limit)throw std::runtime_error("Alliance frame exceeds its limit.");
        plain.append(buffer.data(),count);
    }
    if(result!=Z_STREAM_END||stream.avail_in!=0||plain.empty())throw std::runtime_error("Invalid Alliance compression.");
    return plain;
}
}
