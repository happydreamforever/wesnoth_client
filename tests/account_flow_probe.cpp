// SPDX-License-Identifier: GPL-2.0-or-later
// Disposable integration fixture. Credentials arrive through stdin, never argv.
#include "account_client.hpp"
#include "account_protocol.hpp"
#include <iostream>
#include <stdexcept>

int main(int argc, char** argv) {
    using namespace alliance::account;
    try {
        if(argc != 2) throw std::runtime_error("args");
        auto profile = read_public_profile(argv[1]);
        std::string input;
        for(char c; std::cin.get(c);) {
            if(input.size() >= 4096) throw std::runtime_error("stdin");
            input += c;
        }
        auto data = parse_document(input);
        auto username = std::string(data.at("username").as_string());
        auto password = std::string(data.at("password").as_string());
        alliance::CancellationToken cancel;
        Request request;
        request.operation = "register"; request.username = username; request.password = password;
        request.starter_id = "spearman";
        auto created = perform_request(profile, request, cancel);
        if(!created.ok || created.profile.username != username || created.profile.unit_type != "Spearman"
           || created.profile.level != 1 || created.profile.experience != 0
           || created.profile.total_experience != 0 || !created.token.empty())
            throw std::runtime_error("register");
        std::cout << "PASS register creates personal leader\n";
        auto duplicate = perform_request(profile, request, cancel);
        if(duplicate.ok || duplicate.error_code != "conflict") throw std::runtime_error("duplicate");
        std::cout << "PASS duplicate registration rejected\n";
        request.operation = "login"; request.starter_id.clear();
        request.password = "wrong-password-for-check";
        auto wrong = perform_request(profile, request, cancel);
        if(wrong.ok || wrong.error_code != "invalid_credentials") throw std::runtime_error("wrong-password");
        std::cout << "PASS incorrect password rejected\n";
        request.password = password;
        auto login = perform_request(profile, request, cancel);
        if(!login.ok || !token_valid(login.token) || login.profile.account_id != created.profile.account_id)
            throw std::runtime_error("login");
        std::cout << "PASS login returns same personal leader\n";
        request = {}; request.operation = "me"; request.token = login.token;
        auto me = perform_request(profile, request, cancel);
        if(!me.ok || me.profile.account_id != created.profile.account_id) throw std::runtime_error("me");
        std::cout << "PASS authenticated profile refresh\n";
        request.operation = "logout";
        auto logout = perform_request(profile, request, cancel);
        if(!logout.ok) throw std::runtime_error("logout");
        std::cout << "PASS logout acknowledged\n";
        request.operation = "me";
        auto after = perform_request(profile, request, cancel);
        if(after.ok || after.error_code != "invalid_session") throw std::runtime_error("revocation");
        std::cout << "PASS logged-out token rejected\n";
        std::cout << "ALLIANCE ACCOUNT FLOW CHECK PASSED\n";
        return 0;
    } catch(const std::exception& error) {
        // Only fixed stage names are printed; never payloads/credentials.
        (void)error;
        std::cerr << "ERROR account flow fixture failed\n";
        return 1;
    }
}
