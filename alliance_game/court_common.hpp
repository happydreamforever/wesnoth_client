// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "alliance_native/transport.hpp"
#include <boost/json.hpp>
#include <openssl/rand.h>
#include <atomic>
#include <functional>
#include <memory>
#include <thread>
#include <algorithm>
#include <string>
namespace alliance_game::court_ui {
namespace json=boost::json;
inline const json::object& object(const json::object& v,const char* k){static const json::object empty;const auto* c=v.if_contains(k);return c&&c->is_object()?c->as_object():empty;}
inline const json::array& array(const json::object& v,const char* k){static const json::array empty;const auto* c=v.if_contains(k);return c&&c->is_array()?c->as_array():empty;}
inline std::string text(const json::object& v,const char* k,const std::string& d={}){const auto* c=v.if_contains(k);return c&&c->is_string()?std::string(c->as_string()):d;}
inline std::int64_t number(const json::object& v,const char* k,std::int64_t d=0){const auto* c=v.if_contains(k);if(c&&c->is_int64())return c->as_int64();if(c&&c->is_uint64()&&c->as_uint64()<=INT64_MAX)return c->as_uint64();return d;}
inline bool flag(const json::object& v,const char* k,bool d=false){const auto* c=v.if_contains(k);return c&&c->is_bool()?c->as_bool():d;}
inline std::string title(std::string s){std::replace(s.begin(),s.end(),'_',' ');if(!s.empty())s[0]=std::toupper(static_cast<unsigned char>(s[0]));return s;}
inline std::string command_id(){unsigned char b[16];if(RAND_bytes(b,16)!=1)throw std::runtime_error("Could not prepare this action.");b[6]=(b[6]&15)|64;b[8]=(b[8]&63)|128;const char* hex="0123456789abcdef";std::string s;for(int i=0;i<16;++i){if(i==4||i==6||i==8||i==10)s+='-';s+=hex[b[i]>>4];s+=hex[b[i]&15];}return s;}
// Convert the server's UTC wire timestamp without using the device timezone.
inline std::int64_t utc_seconds(const std::string& value){
    if(value.size()<20||value[4]!='-'||value[7]!='-'||value[10]!='T'||value[13]!=':'||value[16]!=':')return 0;
    try{int y=std::stoi(value.substr(0,4)),m=std::stoi(value.substr(5,2)),d=std::stoi(value.substr(8,2));int h=std::stoi(value.substr(11,2)),mi=std::stoi(value.substr(14,2)),se=std::stoi(value.substr(17,2));if(y<1970||m<1||m>12||d<1||d>31||h>23||mi>59||se>60)return 0;y-=m<=2;const auto era=(y>=0?y:y-399)/400;const auto yo=unsigned(y-era*400);const auto doy=(153*(m+(m>2?-3:9))+2)/5+d-1;const auto doe=yo*365+yo/4-yo/100+doy;return (era*146097+static_cast<int>(doe)-719468)*86400LL+h*3600+mi*60+se;}catch(...){return 0;}
}
class ServerClock {
    std::int64_t utc_=0;std::chrono::steady_clock::time_point received_{};
public:
    void update(const std::string& timestamp){const auto parsed=utc_seconds(timestamp);if(parsed){utc_=parsed;received_=std::chrono::steady_clock::now();}}
    std::string until(const std::string& timestamp)const{const auto target=utc_seconds(timestamp);if(!utc_||!target)return timestamp;const auto elapsed=std::chrono::duration_cast<std::chrono::seconds>(std::chrono::steady_clock::now()-received_).count();const auto left=std::max<std::int64_t>(0,target-utc_-elapsed);return left?std::to_string(left/60)+"m "+std::to_string(left%60)+"s":"Now";}
};
struct Reply {bool ok=false;std::string code,message;json::object document;};
struct Hooks {
    std::string actor_id,generation;bool journal=true;
    std::function<bool()> same_actor;
    std::function<Reply(bool,const std::string&,json::object,alliance::CancellationToken&)> request;
    std::function<void(const Reply&)> connection_failure;
    std::function<bool(std::string&,std::string&)> choose_hero;
    std::function<void(const std::string&,bool)> launch;
    std::function<bool()> destination_pending;
    json::array heroes;
};
class Job {
    alliance::CancellationToken cancel_;std::atomic<bool> done_{false};Reply reply_;std::thread thread_;
public:
    Job(const Hooks& hooks,bool mutation,std::string op,json::object payload):thread_([this,hooks,mutation,op=std::move(op),payload=std::move(payload)]()mutable{try{reply_=hooks.request(mutation,op,std::move(payload),cancel_);}catch(...){reply_.code="request_failed";reply_.message="The server is unavailable. Please log in again.";}done_.store(true,std::memory_order_release);}){}
    ~Job(){cancel_.cancel();if(thread_.joinable())thread_.join();}
    bool complete()const{return done_.load(std::memory_order_acquire);}
    Reply take(){if(thread_.joinable())thread_.join();return std::move(reply_);}
};
inline bool connection_error(const std::string& code){return code=="generation_changed"||code=="invalid_credentials"||code=="invalid_session"||code=="session_expired"||code=="account_unavailable"||code=="request_failed"||code=="unavailable"||code=="timeout"||code=="service_unavailable"||code=="database_unavailable"||code=="unauthorized";}
}
