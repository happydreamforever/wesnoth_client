// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/world_worker.hpp"
#include "alliance_game/world_launch.hpp"
#include "alliance_native/account_client.hpp"
#include "video.hpp"
#include <boost/json.hpp>
#include <openssl/crypto.h>
#include <cstdlib>
#include <iostream>
namespace alliance_game {
bool world_worker_requested()noexcept{return std::getenv("WESNOTH_WORLD_WORKER_PROFILE")!=nullptr||std::getenv("WESNOTH_WORLD_COMPANION_PROFILE")!=nullptr;}
int run_world_worker()noexcept{std::string text;struct Cleanup{std::string&s;~Cleanup(){if(!s.empty())OPENSSL_cleanse(s.data(),s.size());}}cleanup{text};try{const char*profile=std::getenv("WESNOTH_WORLD_WORKER_PROFILE");const bool witness=profile!=nullptr;if(!profile)profile=std::getenv("WESNOTH_WORLD_COMPANION_PROFILE");if(!video::headless()||!profile)throw std::runtime_error("Worker requires headless mode.");for(char c;std::cin.get(c)&&c!='\n';){if(text.size()>=4096)throw std::runtime_error("Worker admission exceeds limit.");text+=c;}auto input=boost::json::parse(text).as_object();const bool replay=!witness&&input.contains("intent");if(input.size()!=(witness?2:replay?4:3)||(replay&&input.at("intent").as_string()!="replay"))throw std::runtime_error("Worker admission differs.");WorldAdmission a;a.profile=alliance::account::read_public_profile(profile);a.profile.port=15002;a.run_id=std::string(input.at("run_id").as_string());a.token=std::string(input.at("token").as_string());a.worker=witness;a.replay=replay;a.computer_controlled=!witness&&!replay;if(!witness)a.account_id=std::string(input.at("account_id").as_string());queue_world(std::move(a));run_pending_world();return world_last_launch_complete()?0:1;}catch(...){std::cerr<<"WORLD WORKER ADMISSION FAILED"<<std::endl;return 1;}}
}
