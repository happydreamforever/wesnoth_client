// SPDX-License-Identifier: GPL-2.0-or-later
// Production reader/transport probe for disposable local fixtures only.
#include "account_client.hpp"
#include "account_protocol.hpp"
#include <boost/json.hpp>
#include <openssl/crypto.h>
#include <cstdlib>
#include <iostream>
#include <type_traits>

using alliance::account::ProfileReadError;
using alliance::account::ProfileReadFailure;
static_assert(!std::is_constructible_v<ProfileReadError, std::string>);
static_assert(!std::is_constructible_v<ProfileReadError, const char*>);
static_assert(std::is_final_v<ProfileReadError>);
static_assert(noexcept(std::declval<const ProfileReadError&>().what()));

// Test-only link wrapper: fail the real native profile's private crypto context
// allocation without substituting its parsing, identity or verification code.
extern "C" OSSL_LIB_CTX* __real_OSSL_LIB_CTX_new();
extern "C" OSSL_LIB_CTX* __wrap_OSSL_LIB_CTX_new()
{
    if(std::getenv("ALLIANCE_PROFILE_TEST_CRYPTO_FAILURE")) return nullptr;
    return __real_OSSL_LIB_CTX_new();
}

int main(int argc, char** argv)
{
    boost::json::object output;
    try {
        if(argc == 2 && std::string(argv[1]) == "--limits") {
            std::cout << boost::json::serialize(boost::json::object{
                {"max_json_bytes", alliance::account::max_json_bytes}}) << '\n';
            return 0;
        }
        if(argc == 2 && std::string(argv[1]) == "--unknown-category") {
            throw ProfileReadError(static_cast<ProfileReadFailure>(999));
        }
        if(argc != 2) return 2;
        const auto profile = alliance::account::read_public_profile(argv[1]);
        output = {{"ok", true}, {"address_matches", profile.address == "127.0.0.1"},
                  {"port_matches", profile.port == 15001},
                  {"pin_length", profile.certificate_sha256.size()},
                  {"has_certificate", !profile.certificate_pem.empty()}};
    } catch(const ProfileReadError& error) {
        output = {{"ok", false}, {"message", error.what()}};
    } catch(...) {
        // Mirrors the UI boundary's fixed fallback; arbitrary exceptions and
        // profile contents never become output even on an unexpected failure.
        output = {{"ok", false}, {"message", "unexpected-profile-failure"}};
    }
    std::cout << boost::json::serialize(output) << '\n';
}
