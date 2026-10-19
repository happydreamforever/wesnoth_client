// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace alliance {

constexpr std::size_t max_frame_bytes = 64 * 1024;
constexpr std::size_t max_certificate_bytes = 16 * 1024;

// These public fields come from a separately trusted public profile, never
// from the server being contacted. No discovery, DNS or TOFU is performed.
struct TrustedProfile {
    std::string address; // Canonical numeric IPv4 or IPv6, without a scope ID.
    std::uint16_t port = 0;
    std::string certificate_pem; // Exactly one public X.509 leaf certificate.
    std::string certificate_sha256; // Lowercase SHA256 of its DER encoding.
};

enum class Error {
    none, invalid_profile, invalid_argument, busy, not_connected,
    already_connected, connect_failed, preamble_failed, tls_required,
    tls_failed, certificate_rejected, pin_mismatch, timeout, cancelled,
    write_failed, read_failed, invalid_frame, internal_error
};
enum class Stage {
    none, profile, connect, preamble_write, preamble_read, tls_handshake,
    pin, send_frame, receive_header, receive_body
};
const char* name(Error) noexcept;
const char* name(Stage) noexcept;

struct Result {
    Error error = Error::none;
    Stage stage = Stage::none;
    std::string tls_version;
    // Populated only by receive_frame; callers must apply a separate bound
    // while decompressing/parsing WML. This transport never decompresses.
    std::vector<std::uint8_t> frame;
    explicit operator bool() const noexcept { return error == Error::none; }
};

class CancellationToken {
public:
    // May be called by another thread. Tokens are one-way and may be shared.
    void cancel() noexcept { requested_.store(true, std::memory_order_release); }
    bool requested() const noexcept { return requested_.load(std::memory_order_acquire); }
private:
    std::atomic<bool> requested_{false};
};

// Checks the profile without opening a socket or changing trust stores.
Result validate_profile(const TrustedProfile&);

// Worker-thread API: never call from the rendering/UI thread. Each call drives
// asynchronous I/O with one monotonic deadline and cancellation polling <=20ms.
// One operation at a time; concurrent calls fail Busy without adding a queue.
// Call cancel on the operation's token before joining its worker. Destroy this
// object only after its worker has returned. No automatic retry or fallback.
// Connection errors close the socket; callers must explicitly create/reconnect.
class Client {
public:
    Client();
    ~Client();
    Client(const Client&) = delete;
    Client& operator=(const Client&) = delete;
    Result connect(const TrustedProfile&, std::chrono::milliseconds timeout,
                   const CancellationToken&);
    Result send_frame(const std::vector<std::uint8_t>& compressed_wml,
                      std::chrono::milliseconds timeout, const CancellationToken&);
    Result receive_frame(std::chrono::milliseconds timeout, const CancellationToken&);
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace alliance
