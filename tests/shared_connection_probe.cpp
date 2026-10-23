// SPDX-License-Identifier: GPL-2.0-or-later
#include "wesnothd_connection.hpp"
#include "alliance_native/account_client.hpp"
#include "config.hpp"
#include "filesystem.hpp"
#include <cstdlib>
#include <chrono>
#include <iostream>
#include <thread>
extern "C" int __real_main(int,char**);
extern "C" int __wrap_main(int argc,char** argv){
    if(argc<4||std::string(argv[1])!="--connection")return __real_main(argc,argv);
    const std::string mode=argv[3];
    try{
        filesystem::set_user_data_dir(std::string(std::getenv("ALLIANCE_SHARED_CONNECTION_SCRATCH"))+"/userdata");
        const auto profile=alliance::account::read_public_profile(argv[2]);
        wesnothd_connection connection(profile);connection.wait_for_handshake();
        if(!connection.using_tls())throw std::runtime_error("Unexpected plaintext connection.");
        if(mode=="cancel")connection.cancel();
        if(mode=="echo")connection.send_data(config{"probe",config{"value","safe fixture payload"}});
        const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(25);
        config reply;
        while(!connection.receive_data(reply)){
            if(std::chrono::steady_clock::now()>deadline)throw std::runtime_error("Probe deadline reached.");
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        if(mode!="echo"||reply.child_or_empty("probe")["value"].str()!="safe fixture payload")throw std::runtime_error("Unexpected probe reply.");
        std::cout<<"PASS strict engine connection echo\n";return 0;
    }catch(...){if(mode=="reject"||mode=="cancel"){std::cout<<"PASS strict engine connection rejection\n";return 0;}std::cerr<<"FAIL strict engine connection\n";return 1;}
}
