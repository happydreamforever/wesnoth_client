// SPDX-License-Identifier: GPL-2.0-or-later
// Exercise the production Windows reader with disposable profile files only.
#include "account_client.hpp"
#include "account_protocol.hpp"
#include <boost/json.hpp>
#include <iostream>
#include <string>

int main(int argc, char** argv)
{
    if(argc < 2 || argc > 3) return 2;
    if(argc == 2 && std::string(argv[1]) == "--limits") {
        std::cout << boost::json::serialize(boost::json::object{
            {"max_json_bytes", alliance::account::max_json_bytes}}) << '\n';
        return 0;
    }
    std::string path = argv[1];
    if(argc == 3) {
        if(std::string(argv[2]) != "--embedded-nul") return 2;
        path += std::string("\0ignored", 8);
    }
    boost::json::object output;
    try {
        const auto profile = alliance::account::read_public_profile(path);
        output = {{"ok", true}, {"address_matches", profile.address == "127.0.0.1"},
                  {"port_matches", profile.port == 15001},
                  {"pin_length", profile.certificate_sha256.size()},
                  {"has_certificate", !profile.certificate_pem.empty()}};
    } catch(const alliance::account::ProfileReadError& error) {
        output = {{"ok", false}, {"message", error.what()}};
    } catch(...) {
        output = {{"ok", false}, {"message", "unexpected-profile-failure"}};
    }
    std::cout << boost::json::serialize(output) << '\n';
}
