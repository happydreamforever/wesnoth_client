// SPDX-License-Identifier: GPL-2.0-or-later
#include "transport.hpp"

#include <boost/asio.hpp>
#include <boost/asio/ssl.hpp>
#include <openssl/crypto.h>
#include <openssl/evp.h>
#include <openssl/pem.h>
#include <openssl/ssl.h>
#include <openssl/x509v3.h>
#include <algorithm>
#include <array>
#include <functional>
#include <utility>

namespace alliance {
namespace {
namespace asio = boost::asio;
using Tcp = asio::ip::tcp;
using Clock = std::chrono::steady_clock;
using Ec = boost::system::error_code;
template<class T, auto Free> using Owner = std::unique_ptr<T, decltype(Free)>;
using Certificate = Owner<X509, X509_free>;
constexpr auto poll_period = std::chrono::milliseconds(20);

Result failure(Error error, Stage stage) { return {error, stage, {}, {}}; }
bool timeout_valid(std::chrono::milliseconds timeout) {
    return timeout.count() > 0 && timeout <= std::chrono::seconds(10);
}

struct ProfileState {
    Owner<OSSL_LIB_CTX, OSSL_LIB_CTX_free> library{OSSL_LIB_CTX_new(), OSSL_LIB_CTX_free};
    Certificate certificate{nullptr, X509_free};
    std::array<unsigned char, 32> fingerprint{};
};

Result parse_profile(const TrustedProfile& profile, ProfileState& out) {
    if(!out.library) return failure(Error::internal_error, Stage::profile);
    if(profile.address.empty() || profile.address.size() > 45 || profile.port == 0 ||
       profile.certificate_pem.empty() || profile.certificate_pem.size() > max_certificate_bytes ||
       profile.certificate_sha256.size() != 64 || profile.address.find('%') != std::string::npos)
        return failure(Error::invalid_profile, Stage::profile);
    Ec ec;
    const auto address = asio::ip::make_address(profile.address, ec);
    if(ec || address.to_string() != profile.address || address.is_unspecified() || address.is_multicast() ||
       profile.address == "255.255.255.255")
        return failure(Error::invalid_profile, Stage::profile);
    for(std::size_t i = 0; i < 32; ++i) {
        auto hex = [](char c) -> int {
            if(c >= '0' && c <= '9') return c - '0';
            if(c >= 'a' && c <= 'f') return c - 'a' + 10;
            return -1;
        };
        const int a = hex(profile.certificate_sha256[i * 2]);
        const int b = hex(profile.certificate_sha256[i * 2 + 1]);
        if(a < 0 || b < 0) return failure(Error::invalid_profile, Stage::profile);
        out.fingerprint[i] = static_cast<unsigned char>((a << 4) | b);
    }
    constexpr char begin[] = "-----BEGIN CERTIFICATE-----";
    constexpr char end[] = "-----END CERTIFICATE-----";
    // PEM readers skip arbitrary leading text, so enforce the envelope first.
    const auto& pem = profile.certificate_pem;
    if(pem.compare(0, sizeof(begin) - 1, begin) != 0)
        return failure(Error::invalid_profile, Stage::profile);
    const auto ending = pem.find(end);
    if(ending == std::string::npos || pem.find(begin, 1) != std::string::npos)
        return failure(Error::invalid_profile, Stage::profile);
    const auto tail = ending + sizeof(end) - 1;
    if(!std::all_of(pem.begin() + static_cast<std::ptrdiff_t>(tail), pem.end(),
                   [](char c) { return c == '\r' || c == '\n'; }))
        return failure(Error::invalid_profile, Stage::profile);
    Owner<BIO, BIO_free> input(BIO_new_mem_buf(pem.data(), static_cast<int>(pem.size())), BIO_free);
    if(!input) return failure(Error::internal_error, Stage::profile);
    out.certificate.reset(PEM_read_bio_X509(input.get(), nullptr, nullptr, nullptr));
    if(!out.certificate || X509_check_ca(out.certificate.get()) != 0)
        return failure(Error::invalid_profile, Stage::profile);
    Owner<EVP_MD, EVP_MD_free> digest(EVP_MD_fetch(out.library.get(), "SHA256", nullptr), EVP_MD_free);
    std::array<unsigned char, EVP_MAX_MD_SIZE> calculated{};
    unsigned int length = 0;
    if(!digest || !X509_digest(out.certificate.get(), digest.get(), calculated.data(), &length))
        return failure(Error::internal_error, Stage::profile);
    if(length != 32 || CRYPTO_memcmp(calculated.data(), out.fingerprint.data(), 32))
        return failure(Error::pin_mismatch, Stage::profile);
    Owner<X509_STORE, X509_STORE_free> store(X509_STORE_new(), X509_STORE_free);
    Owner<X509_STORE_CTX, X509_STORE_CTX_free> check(X509_STORE_CTX_new_ex(out.library.get(), nullptr), X509_STORE_CTX_free);
    if(!store || !check || X509_STORE_add_cert(store.get(), out.certificate.get()) != 1 ||
       X509_STORE_CTX_init(check.get(), store.get(), out.certificate.get(), nullptr) != 1)
        return failure(Error::internal_error, Stage::profile);
    auto* parameters = X509_STORE_CTX_get0_param(check.get());
    if(X509_VERIFY_PARAM_set_purpose(parameters, X509_PURPOSE_SSL_SERVER) != 1 ||
       X509_VERIFY_PARAM_set_flags(parameters, X509_V_FLAG_PARTIAL_CHAIN | X509_V_FLAG_X509_STRICT) != 1 ||
       X509_VERIFY_PARAM_set1_ip_asc(parameters, profile.address.c_str()) != 1)
        return failure(Error::invalid_profile, Stage::profile);
    if(X509_verify_cert(check.get()) != 1)
        return failure(Error::certificate_rejected, Stage::profile);
    return {};
}

struct BusyGuard {
    std::atomic_flag& flag;
    bool acquired;
    explicit BusyGuard(std::atomic_flag& value) : flag(value), acquired(!flag.test_and_set(std::memory_order_acquire)) {}
    ~BusyGuard() { if(acquired) flag.clear(std::memory_order_release); }
};
} // namespace

const char* name(Error error) noexcept {
    switch(error) {
#define CASE(x) case Error::x: return #x
    CASE(none); CASE(invalid_profile); CASE(invalid_argument); CASE(busy);
    CASE(not_connected); CASE(already_connected); CASE(connect_failed);
    CASE(preamble_failed); CASE(tls_required); CASE(tls_failed);
    CASE(certificate_rejected); CASE(pin_mismatch); CASE(timeout); CASE(cancelled);
    CASE(write_failed); CASE(read_failed); CASE(invalid_frame); CASE(internal_error);
#undef CASE
    }
    return "internal_error";
}
const char* name(Stage stage) noexcept {
    switch(stage) {
#define CASE(x) case Stage::x: return #x
    CASE(none); CASE(profile); CASE(connect); CASE(preamble_write); CASE(preamble_read);
    CASE(tls_handshake); CASE(pin); CASE(send_frame); CASE(receive_header); CASE(receive_body);
#undef CASE
    }
    return "none";
}

Result validate_profile(const TrustedProfile& profile) {
    try { ProfileState parsed; return parse_profile(profile, parsed); }
    catch(...) { return failure(Error::internal_error, Stage::profile); }
}

struct Client::Impl {
    asio::io_context io;
    std::atomic_flag busy = ATOMIC_FLAG_INIT;
    // Destroy stream/context before their private OpenSSL library context.
    std::unique_ptr<ProfileState> profile_state;
    std::unique_ptr<asio::ssl::context> context;
    std::unique_ptr<asio::ssl::stream<Tcp::socket>> stream;
    bool connected = false;
    std::string tls_version;

    void close() noexcept {
        connected = false;
        if(stream) { Ec ec; stream->lowest_layer().cancel(ec); stream->lowest_layer().close(ec); }
    }
    Result result(Error error = Error::none, Stage stage = Stage::none) {
        return {error, stage, tls_version, {}};
    }

    template<class Start>
    Result wait(Clock::time_point deadline, const CancellationToken& cancellation,
                Stage stage, Error io_error, Start start) {
        if(cancellation.requested()) { close(); return result(Error::cancelled, stage); }
        if(Clock::now() >= deadline) { close(); return result(Error::timeout, stage); }
        io.restart();
        asio::steady_timer timer(io);
        Ec completed_error;
        bool finished = false;
        Error forced_error = Error::none;
        std::function<void()> arm;
        arm = [&] {
            timer.expires_at(std::min(deadline, Clock::now() + poll_period));
            timer.async_wait([&](const Ec& ec) {
                if(ec || finished) return;
                if(cancellation.requested() || Clock::now() >= deadline) {
                    forced_error = cancellation.requested() ? Error::cancelled : Error::timeout;
                    close();
                } else arm();
            });
        };
        auto finish = [&](const Ec& ec) {
            completed_error = ec;
            finished = true;
            timer.cancel();
        };
        // All handlers are drained before this stack frame returns, including
        // cancelled operations. No callback captures a destroyed frame.
        try { arm(); start(finish); io.run(); }
        catch(...) {
            forced_error = Error::internal_error;
            close();
            timer.cancel();
            io.restart();
            io.run();
        }
        // Check the token/deadline even if I/O completion beat the timer handler.
        if(forced_error == Error::none && cancellation.requested()) forced_error = Error::cancelled;
        if(forced_error == Error::none && Clock::now() >= deadline) forced_error = Error::timeout;
        if(forced_error != Error::none) { close(); return result(forced_error, stage); }
        if(!finished || completed_error) { close(); return result(io_error, stage); }
        return result();
    }
};

Client::Client() : impl_(std::make_unique<Impl>()) {}
Client::~Client() = default;

Result Client::connect(const TrustedProfile& profile, std::chrono::milliseconds timeout,
                       const CancellationToken& cancellation) {
    auto& p = *impl_;
    BusyGuard guard(p.busy);
    if(!guard.acquired) return failure(Error::busy, Stage::connect);
    if(!timeout_valid(timeout)) return p.result(Error::invalid_argument, Stage::connect);
    if(p.connected) return p.result(Error::already_connected, Stage::connect);
    const auto deadline = Clock::now() + timeout;
    if(cancellation.requested()) return p.result(Error::cancelled, Stage::connect);
    try {
        p.close(); p.stream.reset(); p.context.reset(); p.profile_state.reset(); p.tls_version.clear();
        p.profile_state = std::make_unique<ProfileState>();
        auto result = parse_profile(profile, *p.profile_state);
        if(!result) return result;
        // A private library context does not load the process/global OpenSSL
        // configuration. This fresh store contains only the supplied leaf.
        SSL_CTX* native = SSL_CTX_new_ex(p.profile_state->library.get(), nullptr, TLS_client_method());
        if(!native) return p.result(Error::internal_error, Stage::profile);
        p.context = std::make_unique<asio::ssl::context>(native); // takes ownership
        if(SSL_CTX_set_min_proto_version(native, TLS1_2_VERSION) != 1 ||
           X509_STORE_add_cert(SSL_CTX_get_cert_store(native), p.profile_state->certificate.get()) != 1)
            return p.result(Error::internal_error, Stage::profile);
        SSL_CTX_set_verify(native, SSL_VERIFY_PEER, nullptr);
        SSL_CTX_set_options(native, SSL_OP_NO_COMPRESSION | SSL_OP_NO_RENEGOTIATION);
        SSL_CTX_set_session_cache_mode(native, SSL_SESS_CACHE_OFF);
        SSL_CTX_set_max_cert_list(native, max_certificate_bytes);
        p.stream = std::make_unique<asio::ssl::stream<Tcp::socket>>(p.io, *p.context);
        auto* parameters = SSL_get0_param(p.stream->native_handle());
        if(X509_VERIFY_PARAM_set1_ip_asc(parameters, profile.address.c_str()) != 1 ||
           X509_VERIFY_PARAM_set_purpose(parameters, X509_PURPOSE_SSL_SERVER) != 1 ||
           X509_VERIFY_PARAM_set_flags(parameters, X509_V_FLAG_PARTIAL_CHAIN | X509_V_FLAG_X509_STRICT) != 1)
            return p.result(Error::internal_error, Stage::profile);
        const auto endpoint = Tcp::endpoint(asio::ip::make_address(profile.address), profile.port);
        result = p.wait(deadline, cancellation, Stage::connect, Error::connect_failed,
            [&](auto finish) { p.stream->next_layer().async_connect(endpoint, finish); });
        if(!result) return result;
        const std::array<std::uint8_t, 4> preamble{0, 0, 0, 1};
        result = p.wait(deadline, cancellation, Stage::preamble_write, Error::preamble_failed,
            [&](auto finish) { asio::async_write(p.stream->next_layer(), asio::buffer(preamble),
                [finish](const Ec& ec, std::size_t) { finish(ec); }); });
        if(!result) return result;
        std::array<std::uint8_t, 4> reply{};
        result = p.wait(deadline, cancellation, Stage::preamble_read, Error::preamble_failed,
            [&](auto finish) { asio::async_read(p.stream->next_layer(), asio::buffer(reply),
                [finish](const Ec& ec, std::size_t) { finish(ec); }); });
        if(!result) return result;
        if(reply != std::array<std::uint8_t, 4>{0, 0, 0, 0}) {
            p.close(); return p.result(Error::tls_required, Stage::preamble_read);
        }
        result = p.wait(deadline, cancellation, Stage::tls_handshake, Error::tls_failed,
            [&](auto finish) { p.stream->async_handshake(asio::ssl::stream_base::client, finish); });
        if(!result) {
            if(result.error == Error::tls_failed && SSL_get_verify_result(p.stream->native_handle()) != X509_V_OK)
                result.error = Error::certificate_rejected;
            return result;
        }
        Certificate peer(SSL_get1_peer_certificate(p.stream->native_handle()), X509_free);
        Owner<EVP_MD, EVP_MD_free> digest(EVP_MD_fetch(p.profile_state->library.get(), "SHA256", nullptr), EVP_MD_free);
        std::array<unsigned char, EVP_MAX_MD_SIZE> peer_hash{};
        unsigned int size = 0;
        if(!peer || !digest || SSL_get_verify_result(p.stream->native_handle()) != X509_V_OK ||
           X509_digest(peer.get(), digest.get(), peer_hash.data(), &size) != 1 || size != 32 ||
           CRYPTO_memcmp(peer_hash.data(), p.profile_state->fingerprint.data(), 32)) {
            p.close(); return p.result(Error::pin_mismatch, Stage::pin);
        }
        if(cancellation.requested() || Clock::now() >= deadline) {
            p.close(); return p.result(cancellation.requested() ? Error::cancelled : Error::timeout, Stage::pin);
        }
        p.tls_version = SSL_get_version(p.stream->native_handle());
        p.connected = true;
        return p.result();
    } catch(...) { p.close(); return p.result(Error::internal_error, Stage::connect); }
}

Result Client::send_frame(const std::vector<std::uint8_t>& frame, std::chrono::milliseconds timeout,
                          const CancellationToken& cancellation) {
    auto& p = *impl_;
    BusyGuard guard(p.busy);
    if(!guard.acquired) return failure(Error::busy, Stage::send_frame);
    if(!timeout_valid(timeout)) return p.result(Error::invalid_argument, Stage::send_frame);
    if(!p.connected) return p.result(Error::not_connected, Stage::send_frame);
    if(frame.empty() || frame.size() > max_frame_bytes) return p.result(Error::invalid_frame, Stage::send_frame);
    const auto size = static_cast<std::uint32_t>(frame.size());
    const std::array<std::uint8_t, 4> header{
        static_cast<std::uint8_t>(size >> 24), static_cast<std::uint8_t>(size >> 16),
        static_cast<std::uint8_t>(size >> 8), static_cast<std::uint8_t>(size)};
    const std::array<asio::const_buffer, 2> data{asio::buffer(header), asio::buffer(frame)};
    return p.wait(Clock::now() + timeout, cancellation, Stage::send_frame, Error::write_failed,
        [&](auto finish) { asio::async_write(*p.stream, data,
            [finish](const Ec& ec, std::size_t) { finish(ec); }); });
}

Result Client::receive_frame(std::chrono::milliseconds timeout, const CancellationToken& cancellation) {
    auto& p = *impl_;
    BusyGuard guard(p.busy);
    if(!guard.acquired) return failure(Error::busy, Stage::receive_header);
    if(!timeout_valid(timeout)) return p.result(Error::invalid_argument, Stage::receive_header);
    if(!p.connected) return p.result(Error::not_connected, Stage::receive_header);
    const auto deadline = Clock::now() + timeout;
    std::array<std::uint8_t, 4> header{};
    auto result = p.wait(deadline, cancellation, Stage::receive_header, Error::read_failed,
        [&](auto finish) { asio::async_read(*p.stream, asio::buffer(header),
            [finish](const Ec& ec, std::size_t) { finish(ec); }); });
    if(!result) return result;
    const std::uint32_t size = (std::uint32_t(header[0]) << 24) | (std::uint32_t(header[1]) << 16) |
                               (std::uint32_t(header[2]) << 8) | std::uint32_t(header[3]);
    if(size == 0 || size > max_frame_bytes) { p.close(); return p.result(Error::invalid_frame, Stage::receive_header); }
    try {
        std::vector<std::uint8_t> body(size);
        result = p.wait(deadline, cancellation, Stage::receive_body, Error::read_failed,
            [&](auto finish) { asio::async_read(*p.stream, asio::buffer(body),
                [finish](const Ec& ec, std::size_t) { finish(ec); }); });
        if(result) result.frame = std::move(body);
        return result;
    } catch(...) { p.close(); return p.result(Error::internal_error, Stage::receive_body); }
}

} // namespace alliance
