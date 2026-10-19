// SPDX-License-Identifier: GPL-2.0-or-later
#include "transport.hpp"
#include <boost/version.hpp>
#include <openssl/opensslv.h>
#include <openssl/crypto.h>
#include <chrono>
#include <condition_variable>
#include <fstream>
#include <iostream>
#include <map>
#include <mutex>
#include <string>
#include <thread>

namespace {
constexpr std::uint8_t canary_bytes[] = {
    0x1f,0x8b,0x08,0x00,0x00,0x00,0x00,0x00,0x02,0x03,0x8b,0x4e,0xcc,0xc9,0xc9,0x4c,
    0xcc,0x4b,0x4e,0x8d,0x2f,0x28,0xca,0x4f,0x4a,0x8d,0xe5,0x2a,0x4b,0xcc,0x29,0x4d,
    0xb5,0x55,0xca,0x4b,0x2c,0xc9,0x2c,0x4b,0xd5,0x4d,0x4e,0xcc,0x4b,0x2c,0xaa,0xd4,
    0x2d,0x33,0x54,0xe2,0x8a,0xd6,0x47,0x57,0x0a,0x00,0xb6,0xc2,0xa6,0x93,0x3c,0x00,
    0x00,0x00
};
bool number(const std::string& input, int low, int high, int& output) {
    if(input.empty() || input.size() > 5) return false;
    output = 0;
    for(char c : input) {
        if(c < '0' || c > '9') return false;
        output = output * 10 + c - '0';
    }
    return output >= low && output <= high;
}
// Only controlled strings enter JSON. No error text, address, certificate,
// key, endpoint response or application bytes are printed.
int print(const alliance::Result& result, const char* check = "transport") {
    const bool gzip = result.frame.size() >= 3 && result.frame[0] == 0x1f &&
                      result.frame[1] == 0x8b && result.frame[2] == 8;
    std::cout << "{\"ok\":" << (result ? "true" : "false")
              << ",\"check\":\"" << check << "\",\"error\":\"" << alliance::name(result.error)
              << "\",\"stage\":\"" << alliance::name(result.stage)
              << "\",\"tls_version\":\"" << result.tls_version
              << "\",\"received_bytes\":" << result.frame.size()
              << ",\"is_gzip\":" << (gzip ? "true" : "false") << "}" << std::endl;
    return result ? 0 : 1;
}
alliance::Result invalid() { return {alliance::Error::invalid_argument, alliance::Stage::profile, {}, {}}; }

struct Canceller {
    alliance::CancellationToken& token;
    std::mutex mutex;
    std::condition_variable changed;
    bool done = false;
    std::thread worker;
    Canceller(alliance::CancellationToken& value, int delay) : token(value) {
        if(delay == 0) token.cancel();
        else if(delay > 0) worker = std::thread([this, delay] {
            std::unique_lock<std::mutex> lock(mutex);
            if(!changed.wait_for(lock, std::chrono::milliseconds(delay), [this] { return done; })) token.cancel();
        });
    }
    ~Canceller() {
        { std::lock_guard<std::mutex> lock(mutex); done = true; }
        changed.notify_all();
        if(worker.joinable()) worker.join();
    }
};
} // namespace

int main(int argc, char** argv) {
    try {
        if(argc == 2 && std::string(argv[1]) == "--version") {
            std::cout << "{\"ok\":true,\"check\":\"version\",\"protocol\":1,\"boost\":\"" << BOOST_LIB_VERSION
                      << "\",\"openssl_header_number\":" << OPENSSL_VERSION_NUMBER
                      << ",\"openssl_library_number\":" << OpenSSL_version_num()
                      << ",\"compiler_major\":" << __GNUC__ << ",\"compiler_minor\":" << __GNUC_MINOR__
#ifdef __clang__
                      << ",\"compiler\":\"clang\",\"clang_major\":" << __clang_major__
#else
                      << ",\"compiler\":\"gcc\""
#endif
                      << "}" << std::endl;
            return 0;
        }
        if(argc == 2 && std::string(argv[1]) == "--guards-only") {
            alliance::Client client;
            alliance::CancellationToken cancellation;
            const auto timeout = std::chrono::milliseconds(100);
            const std::vector<std::uint8_t> canary(std::begin(canary_bytes), std::end(canary_bytes));
            if(client.send_frame(canary, timeout, cancellation).error != alliance::Error::not_connected ||
               client.receive_frame(timeout, cancellation).error != alliance::Error::not_connected)
                return print({alliance::Error::internal_error, alliance::Stage::none, {}, {}}, "guards");
            return print({}, "guards");
        }
        std::map<std::string, std::string> values;
        bool validate = false, receive = false;
        for(int i = 1; i < argc; ++i) {
            const std::string key = argv[i];
            if(key == "--validate-only") { if(validate || receive) return print(invalid()); validate = true; }
            else if(key == "--receive-only") { if(validate || receive) return print(invalid()); receive = true; }
            else if(key == "--address" || key == "--port" || key == "--certificate" || key == "--sha256" ||
                    key == "--timeout-ms" || key == "--cancel-after-ms") {
                if(++i == argc || !values.emplace(key, argv[i]).second) return print(invalid());
            } else return print(invalid());
        }
        for(const auto* required : {"--address", "--port", "--certificate", "--sha256"})
            if(values.find(required) == values.end()) return print(invalid());
        int port = 0, timeout_ms = 2000, cancel_ms = -1;
        if(!number(values.at("--port"), 1, 65535, port) ||
           (values.count("--timeout-ms") && !number(values.at("--timeout-ms"), 1, 10000, timeout_ms)) ||
           (values.count("--cancel-after-ms") && !number(values.at("--cancel-after-ms"), 0, 10000, cancel_ms)) ||
           (validate && cancel_ms >= 0)) return print(invalid());
        // Read at most max+1 bytes. No file size supplied by the caller can
        // cause an allocation above this fixed bound.
        std::ifstream certificate(values.at("--certificate"), std::ios::binary);
        if(!certificate) return print(invalid());
        std::string pem(alliance::max_certificate_bytes + 1, '\0');
        certificate.read(pem.data(), static_cast<std::streamsize>(pem.size()));
        const auto count = certificate.gcount();
        if(count <= 0 || count > static_cast<std::streamsize>(alliance::max_certificate_bytes) || certificate.bad())
            return print(invalid());
        pem.resize(static_cast<std::size_t>(count));
        const alliance::TrustedProfile profile{values.at("--address"), static_cast<std::uint16_t>(port),
                                               std::move(pem), values.at("--sha256")};
        if(validate) return print(alliance::validate_profile(profile), "profile");
        alliance::Client client;
        alliance::CancellationToken cancellation;
        Canceller canceller(cancellation, cancel_ms);
        const auto timeout = std::chrono::milliseconds(timeout_ms);
        auto result = client.connect(profile, timeout, cancellation);
        if(!result) return print(result);
        const std::vector<std::uint8_t> canary(std::begin(canary_bytes), std::end(canary_bytes));
        if(!receive) {
            result = client.send_frame(canary, timeout, cancellation);
            if(!result) return print(result);
        }
        result = client.receive_frame(timeout, cancellation);
        if(result && !receive && result.frame != canary) {
            result.error = alliance::Error::invalid_frame;
            result.stage = alliance::Stage::receive_body;
            result.frame.clear();
        }
        return print(result);
    } catch(...) { return print({alliance::Error::internal_error, alliance::Stage::none, {}, {}}); }
}
