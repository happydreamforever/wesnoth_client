// SPDX-License-Identifier: GPL-2.0-or-later
// Host/NDK compilable semantic checks; no Android runtime claim from host use.
#include "account_protocol.hpp"
#include "account_client.hpp"
#include <zlib.h>
#include <functional>
#include <iostream>
#include <stdexcept>

namespace {
void require(bool result) { if(!result) throw std::runtime_error("account protocol assertion failed"); }
void rejects(const std::function<void()>& operation) {
    bool failed = false;
    try { operation(); } catch(...) { failed = true; }
    require(failed);
}
std::vector<std::uint8_t> gzip(const std::string& value) {
    z_stream z{};
    require(deflateInit2(&z, 6, Z_DEFLATED, 31, 8, Z_DEFAULT_STRATEGY) == Z_OK);
    std::vector<std::uint8_t> result(value.size() + 256);
    z.next_in = reinterpret_cast<Bytef*>(const_cast<char*>(value.data()));
    z.avail_in = value.size(); z.next_out = result.data(); z.avail_out = result.size();
    require(deflate(&z, Z_FINISH) == Z_STREAM_END);
    result.resize(z.total_out); deflateEnd(&z);
    return result;
}
}
int main() {
    using namespace alliance::account;
    try {
        boost::json::object input{{"v", 1}, {"id", "0123456789abcdef0123456789abcdef"},
                                 {"op", "login"}, {"body", {{"username", "Player"}, {"password", "test-password"}}}};
        auto frame = encode_message(input);
        require(decode_message(frame) == input);
        std::cout << "PASS canonical gzip JSON roundtrip\n";
        for(const std::string text : {"[]", "null", "{\"v\":1,\"v\":1}", "{ \"v\": 1 }",
                                     "{\"v\":1}junk", "{\"v\":\"\\ud800\"}"})
            rejects([&] { decode_message(gzip(text)); });
        rejects([&] { decode_message(gzip(std::string("{\"x\":\"") + char(0xff) + "\"}")); });
        rejects([&] { parse_document("{\"x\":[[[[[[[[[1]]]]]]]]]}"); });
        rejects([&] { parse_document("{ \"port\": 15001, \"port\": 15002 }"); });
        rejects([&] { parse_document("{\"x\":[{\"a\":1,\"\\u0061\":2}]}"); });
        require(parse_document("{ \"address\" : \"::1\", \"x\": [{\"escaped\": \"\\\"x:y\"}] }").size() == 2);
        std::cout << "PASS malformed duplicate noncanonical and deep JSON rejection\n";
        auto damaged = frame; damaged.back() ^= 1;
        rejects([&] { decode_message(damaged); });
        damaged = frame; damaged.pop_back();
        rejects([&] { decode_message(damaged); });
        damaged = frame; damaged.push_back(0);
        rejects([&] { decode_message(damaged); });
        damaged = frame; damaged.insert(damaged.end(), frame.begin(), frame.end());
        rejects([&] { decode_message(damaged); });
        rejects([&] { decode_message({}); });
        rejects([&] { decode_message(std::vector<std::uint8_t>(65537, 0)); });
        rejects([&] { decode_message(gzip("{\"x\":\"" + std::string(100000, 'a') + "\"}")); });
        rejects([&] { encode_message({{"x", std::string(32768, 'a')}}); });
        std::cout << "PASS checksum truncation trailing-member and expansion limits\n";
        require(username_valid("Player_123") && !username_valid("x") && !username_valid("../x"));
        require(token_valid(std::string(43, 'a')) && !token_valid(std::string(44, 'a')));
        require(starter_valid("elvish-fighter") && !starter_valid("admin"));
        require(password_valid("long-password", true) && !password_valid("short", true));
        require(password_valid("éééééééééééé", true));
        require(!password_valid(std::string(12, char(0xff)), true));
        require(!password_valid(std::string("long-pass\0word", 14), true));
        std::cout << "PASS credential shapes and Unicode password counting\n";
        alliance::CancellationToken cancel;
        Request bad; bad.operation = "/server/settle";
        auto reply = perform_request({}, bad, cancel);
        require(!reply.ok && reply.error_code == "invalid_input");
        bad.operation = "me"; bad.token = "bad";
        reply = perform_request({}, bad, cancel);
        require(!reply.ok && reply.error_code == "invalid_input");
        std::cout << "PASS unsupported operation and invalid token refused before socket\n";
        std::cout << "ALLIANCE ACCOUNT PROTOCOL CHECK PASSED\n";
        return 0;
    } catch(...) {
        std::cerr << "ERROR account protocol check failed\n";
        return 1;
    }
}
