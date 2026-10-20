// SPDX-License-Identifier: GPL-2.0-or-later
#include "account_client.hpp"
#include "account_protocol.hpp"
#include <openssl/rand.h>
#include <algorithm>
#include <array>
#include <chrono>
#include <fstream>
#include <initializer_list>
#include <limits>
#include <stdexcept>
#include <sys/stat.h>

namespace alliance::account {
namespace {
namespace json = boost::json;
[[noreturn]] void invalid() { throw std::runtime_error("Invalid Alliance account response."); }
void keys(const json::object& obj, std::initializer_list<const char*> expected) {
    if(obj.size() != expected.size()) invalid();
    for(const auto* key : expected) if(!obj.contains(key)) invalid();
}
std::string string(const json::object& obj, const char* key, std::size_t maximum) {
    const auto* value = obj.if_contains(key);
    if(!value || !value->is_string()) invalid();
    const auto& text = value->as_string();
    if(text.empty() || text.size() > maximum
       || std::find(text.begin(), text.end(), '\0') != text.end()) invalid();
    return {text.data(), text.size()};
}
int integer(const json::object& obj, const char* key, int minimum, int maximum) {
    const auto* value = obj.if_contains(key);
    if(!value || !value->is_int64() || value->as_int64() < minimum
       || value->as_int64() > maximum) invalid();
    return static_cast<int>(value->as_int64());
}
const json::object& object(const json::object& obj, const char* key) {
    const auto* value = obj.if_contains(key);
    if(!value || !value->is_object()) invalid();
    return value->as_object();
}
Reply failure(const std::string& code) {
    Reply result;
    result.error_code = code;
    if(code == "invalid_credentials") result.error_message = "Invalid username or password.";
    else if(code == "conflict") result.error_message = "That username is already in use.";
    else if(code == "invalid_session" || code == "forbidden" || code == "suspended")
        result.error_message = "This session is no longer valid. Please log in again.";
    else if(code == "rate_limited")
        result.error_message = "Too many login attempts. Try again in 15 minutes.";
    else if(code == "invalid_input" || code == "invalid_password")
        result.error_message = "Check your username, password and starter leader.";
    else if(code == "cancelled")
        result.error_message = "Request cancelled. If you were creating an account, try logging in to check whether it was created.";
    else if(code == "timeout")
        result.error_message = "The server did not reply in time. If you were creating an account, try logging in before registering again.";
    else if(code == "identity")
        result.error_message = "Server identity verification failed. Check the imported public profile.";
    else if(code == "protocol")
        result.error_message = "The server returned an unsupported account response.";
    else result.error_message = "The Alliance service is unavailable. Check the Windows database, service and account bridge.";
    return result;
}
Reply transport_failure(const Result& result) {
    if(result.error == Error::cancelled) return failure("cancelled");
    if(result.error == Error::timeout) return failure("timeout");
    if(result.stage == Stage::profile || result.stage == Stage::preamble_read
       || result.stage == Stage::tls_handshake || result.stage == Stage::pin)
        return failure("identity");
    return failure("unavailable");
}
Profile profile(const json::object& data) {
    const auto& account = object(data, "account");
    keys(account, {"id", "username", "leader"});
    Profile result;
    result.account_id = string(account, "id", 36);
    if(result.account_id.size() != 36) invalid();
    for(std::size_t i = 0; i < result.account_id.size(); ++i) {
        char c = result.account_id[i];
        if(i == 8 || i == 13 || i == 18 || i == 23) { if(c != '-') invalid(); }
        else if(!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) invalid();
    }
    result.username = string(account, "username", 24);
    if(!username_valid(result.username)) invalid();
    const auto& leader = object(account, "leader");
    keys(leader, {"starter_id", "unit_type", "level", "experience", "total_experience"});
    result.starter_id = string(leader, "starter_id", 32);
    if(!starter_valid(result.starter_id)) invalid();
    result.unit_type = string(leader, "unit_type", 64);
    if(!std::all_of(result.unit_type.begin(), result.unit_type.end(),
       [](char c) { return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || c == ' '; })) invalid();
    result.level = integer(leader, "level", 1, 3);
    result.experience = integer(leader, "experience", 0, std::numeric_limits<int>::max());
    result.total_experience = integer(leader, "total_experience", 0, std::numeric_limits<int>::max());
    return result;
}
}

TrustedProfile read_public_profile(const std::string& path) {
    // lstat rejects a final symlink in the trusted, app-private destination.
    struct stat info{};
    if(path.empty() || path.size() > 4096 || lstat(path.c_str(), &info) != 0
       || !S_ISREG(info.st_mode) || info.st_size < 1
       || static_cast<std::uint64_t>(info.st_size) > max_json_bytes)
        throw std::runtime_error("Import the Windows public server profile first.");
    std::ifstream stream(path, std::ios::binary);
    std::string raw(max_json_bytes + 1, '\0');
    stream.read(raw.data(), static_cast<std::streamsize>(raw.size()));
    raw.resize(static_cast<std::size_t>(stream.gcount()));
    if(stream.bad() || raw.empty() || raw.size() > max_json_bytes)
        throw std::runtime_error("Cannot read the public server profile.");
    auto document = parse_document(raw);
    keys(document, {"format", "protocol", "server_address", "port", "minimum_tls",
                    "certificate_pem", "certificate_sha256"});
    if(integer(document, "format", 1, 1) != 1
       || string(document, "protocol", 32) != "wesnoth-alliance-tls"
       || string(document, "minimum_tls", 8) != "1.2") invalid();
    TrustedProfile result;
    result.address = string(document, "server_address", 64);
    result.port = static_cast<std::uint16_t>(integer(document, "port", 1, 65535));
    result.certificate_pem = string(document, "certificate_pem", max_certificate_bytes);
    result.certificate_sha256 = string(document, "certificate_sha256", 64);
    if(!validate_profile(result))
        throw std::runtime_error("The public server profile is invalid or expired.");
    return result;
}

Reply perform_request(const TrustedProfile& trusted, const Request& request,
                      const CancellationToken& cancel) {
    try {
        json::object body;
        const auto& op = request.operation;
        if(op == "register" || op == "login") {
            if(!username_valid(request.username) || !password_valid(request.password, op == "register")
               || !request.token.empty()) return failure("invalid_input");
            body["username"] = request.username;
            body["password"] = request.password;
            if(op == "register") {
                if(!starter_valid(request.starter_id)) return failure("invalid_input");
                body["starter_id"] = request.starter_id;
            } else if(!request.starter_id.empty()) return failure("invalid_input");
        } else if(op == "me" || op == "logout") {
            if(!token_valid(request.token) || !request.username.empty()
               || !request.password.empty() || !request.starter_id.empty()) return failure("invalid_input");
        } else return failure("invalid_input");

        std::array<unsigned char, 16> random{};
        if(RAND_bytes(random.data(), random.size()) != 1) return failure("unavailable");
        constexpr char hex[] = "0123456789abcdef";
        std::string id;
        for(auto c : random) { id += hex[c >> 4]; id += hex[c & 15]; }
        json::object message{{"v", 1}, {"id", id}, {"op", op}, {"body", std::move(body)}};
        if(op == "me" || op == "logout") message["token"] = request.token;
        auto frame = encode_message(message);
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
        auto remaining = [&]() {
            return std::chrono::duration_cast<std::chrono::milliseconds>(
                deadline - std::chrono::steady_clock::now());
        };
        Client client;
        auto result = client.connect(trusted, remaining(), cancel);
        if(!result) return transport_failure(result);
        if(remaining().count() <= 0) return failure("timeout");
        result = client.send_frame(frame, remaining(), cancel);
        if(!result) return transport_failure(result);
        if(remaining().count() <= 0) return failure("timeout");
        result = client.receive_frame(remaining(), cancel);
        if(!result) return transport_failure(result);
        auto response = decode_message(result.frame);
        keys(response, {"v", "id", "ok", "status", "data"});
        integer(response, "v", 1, 1);
        if(string(response, "id", 32) != id || !response.at("ok").is_bool()) invalid();
        int status = integer(response, "status", 200, 599);
        bool ok = response.at("ok").as_bool();
        if(ok != (status >= 200 && status < 300)) invalid();
        const auto& data = object(response, "data");
        if(!ok) {
            keys(data, {"code", "message"});
            auto code = string(data, "code", 64);
            string(data, "message", 256);
            if(status == 401 || status == 403) {
                if(op == "me" || op == "logout" || code != "invalid_credentials")
                    code = "invalid_session";
            }
            return failure(code);
        }
        Reply reply;
        if(op == "logout") {
            keys(data, {"status"});
            if(status != 200 || string(data, "status", 16) != "logged_out") invalid();
        } else {
            if(op == "login") {
                keys(data, {"token", "account"});
                reply.token = string(data, "token", 43);
                if(!token_valid(reply.token)) invalid();
            } else keys(data, {"account"});
            if(status != (op == "register" ? 201 : 200)) invalid();
            reply.profile = profile(data);
        }
        reply.ok = true;
        return reply;
    } catch(...) { return failure("protocol"); }
}
}
