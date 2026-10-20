// SPDX-License-Identifier: GPL-2.0-or-later
// Standalone fixture for the production Alliance Client. All peers bind an
// ephemeral 127.0.0.1 port. Certificates and keys exist only in process memory.
// This does not contact the Windows server, MongoDB or the installed game.
#include "transport.hpp"
#include <boost/asio.hpp>
#include <boost/asio/ssl.hpp>
#include <boost/version.hpp>
#include <openssl/evp.h>
#include <openssl/pem.h>
#include <openssl/ssl.h>
#include <openssl/x509v3.h>
#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <functional>
#include <iostream>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#ifdef __ANDROID__
#if !defined(__x86_64__) || __ANDROID_API__ != 23
#error This check must target Android x86_64 / API23.
#endif
static_assert(BOOST_VERSION == 108800, "Use the completed Boost 1.88 prefix");
static_assert(OPENSSL_VERSION_NUMBER == 0x30400010L, "Use the completed OpenSSL 3.4.1 prefix");
#endif

namespace {
namespace asio = boost::asio;
using Tcp = asio::ip::tcp;
using Ec = boost::system::error_code;
using Clock = std::chrono::steady_clock;
using namespace std::chrono_literals;
template<class T, auto Free> using Owner = std::unique_ptr<T, decltype(Free)>;

void require(bool condition, const char* message) {
    if(!condition) throw std::runtime_error(message);
}
void run(const char* name) { std::cout << "RUN " << name << std::endl; }
void pass(const char* name) { std::cout << "PASS " << name << std::endl; }
const std::vector<std::uint8_t> payload{
    0x1f,0x8b,0x08,0x00,0x00,0x00,0x00,0x00,0x02,0x03,0x8b,0x4e,0xcc,0xc9,0xc9,0x4c,
    0xcc,0x4b,0x4e,0x8d,0x2f,0x28,0xca,0x4f,0x4a,0x8d,0xe5,0x2a,0x4b,0xcc,0x29,0x4d,
    0xb5,0x55,0xca,0x4b,0x2c,0xc9,0x2c,0x4b,0xd5,0x4d,0x4e,0xcc,0x4b,0x2c,0xaa,0xd4,
    0x2d,0x33,0x54,0xe2,0x8a,0xd6,0x47,0x57,0x0a,0x00,0xb6,0xc2,0xa6,0x93,0x3c,0x00,
    0x00,0x00
};

struct Identity {
    // The library context outlives its key/certificate and every peer context.
    Owner<OSSL_LIB_CTX, OSSL_LIB_CTX_free> library{OSSL_LIB_CTX_new(), OSSL_LIB_CTX_free};
    Owner<EVP_PKEY, EVP_PKEY_free> key{nullptr, EVP_PKEY_free};
    Owner<X509, X509_free> certificate{nullptr, X509_free};
    std::string pem, pin;

    explicit Identity(bool expired = false) {
        require(bool(library), "ephemeral library context");
        key.reset(EVP_PKEY_Q_keygen(library.get(), nullptr, "EC", "P-256"));
        certificate.reset(X509_new_ex(library.get(), nullptr));
        auto* cert = certificate.get();
        require(key && cert && X509_set_version(cert, 2) == 1 &&
                ASN1_INTEGER_set(X509_get_serialNumber(cert), 1) == 1 &&
                X509_gmtime_adj(X509_getm_notBefore(cert), expired ? -7200 : -300) &&
                X509_gmtime_adj(X509_getm_notAfter(cert), expired ? -3600 : 3600) &&
                X509_set_pubkey(cert, key.get()) == 1, "ephemeral certificate fields");
        auto* subject = X509_get_subject_name(cert);
        const unsigned char cn[] = "Wesnoth Alliance loopback fixture";
        require(subject && X509_NAME_add_entry_by_txt(subject, "CN", MBSTRING_ASC, cn, -1, -1, 0) == 1 &&
                X509_set_issuer_name(cert, subject) == 1, "ephemeral certificate subject");
        X509V3_CTX extension_context{};
        X509V3_set_ctx(&extension_context, cert, cert, nullptr, nullptr, 0);
        auto extension = [&](int nid, const char* value) {
            Owner<X509_EXTENSION, X509_EXTENSION_free> item(
                X509V3_EXT_conf_nid(nullptr, &extension_context, nid, value), X509_EXTENSION_free);
            require(item && X509_add_ext(cert, item.get(), -1) == 1, "ephemeral certificate extension");
        };
        extension(NID_basic_constraints, "critical,CA:FALSE");
        extension(NID_key_usage, "critical,digitalSignature");
        extension(NID_ext_key_usage, "serverAuth");
        extension(NID_subject_alt_name, "IP:127.0.0.1");
        extension(NID_subject_key_identifier, "hash");
        extension(NID_authority_key_identifier, "keyid:always");
        Owner<EVP_MD_CTX, EVP_MD_CTX_free> signing(EVP_MD_CTX_new(), EVP_MD_CTX_free);
        require(signing && EVP_DigestSignInit_ex(signing.get(), nullptr, "SHA256", library.get(),
                    nullptr, key.get(), nullptr) == 1 &&
                X509_sign_ctx(cert, signing.get()) > 0, "ephemeral certificate signature");
        Owner<BIO, BIO_free> output(BIO_new(BIO_s_mem()), BIO_free);
        require(output && PEM_write_bio_X509(output.get(), cert) == 1, "public certificate encoding");
        char* data = nullptr;
        const long size = BIO_get_mem_data(output.get(), &data);
        require(data && size > 0 && size <= long(alliance::max_certificate_bytes), "public certificate bound");
        pem.assign(data, static_cast<std::size_t>(size));
        Owner<EVP_MD, EVP_MD_free> digest(EVP_MD_fetch(library.get(), "SHA256", nullptr), EVP_MD_free);
        std::array<unsigned char, EVP_MAX_MD_SIZE> hash{};
        unsigned int hash_size = 0;
        require(digest && X509_digest(cert, digest.get(), hash.data(), &hash_size) == 1 &&
                hash_size == 32, "public certificate fingerprint");
        constexpr char hex[] = "0123456789abcdef";
        for(unsigned int i = 0; i < hash_size; ++i) {
            pin += hex[hash[i] >> 4]; pin += hex[hash[i] & 15];
        }
    }
    alliance::TrustedProfile profile(std::uint16_t port) const { return {"127.0.0.1", port, pem, pin}; }
};

enum class Mode {
    echo, push, untrusted, downgrade, plaintext, preamble_eof, preamble_reset,
    preamble_stall, tls_stall, oversized, empty, partial_header, partial_body, receive_stall
};

SSL_CTX* server_context(const Identity& identity, int version) {
    Owner<SSL_CTX, SSL_CTX_free> context(
        SSL_CTX_new_ex(identity.library.get(), nullptr, TLS_server_method()), SSL_CTX_free);
    require(context && SSL_CTX_set_min_proto_version(context.get(), version) == 1 &&
            SSL_CTX_set_max_proto_version(context.get(), version) == 1 &&
            SSL_CTX_use_certificate(context.get(), identity.certificate.get()) == 1 &&
            SSL_CTX_use_PrivateKey(context.get(), identity.key.get()) == 1 &&
            SSL_CTX_check_private_key(context.get()) == 1, "ephemeral TLS server context");
    SSL_CTX_set_session_cache_mode(context.get(), SSL_SESS_CACHE_OFF);
    SSL_CTX_set_options(context.get(), SSL_OP_NO_COMPRESSION | SSL_OP_NO_RENEGOTIATION);
    return context.release(); // asio::ssl::context owns this handle.
}

class Peer {
    asio::io_context io_;
    asio::ssl::context context_;
    asio::ssl::stream<Tcp::socket> stream_;
    Tcp::acceptor acceptor_;
    const Mode mode_;
    const Clock::time_point deadline_ = Clock::now() + 7s;
    std::atomic<bool> stopping_{false};
    std::thread worker_;
    bool failed_ = false;

    void close() {
        Ec ignored;
        acceptor_.close(ignored);
        stream_.next_layer().cancel(ignored);
        stream_.next_layer().close(ignored);
    }
    // All socket operations and closes happen on the peer thread. Each
    // operation drains cancellation callbacks before its buffers leave scope.
    template<class Start> bool wait(Start start) {
        io_.restart();
        asio::steady_timer timer(io_);
        bool completed = false, success = false;
        std::function<void()> arm;
        arm = [&] {
            timer.expires_after(10ms);
            timer.async_wait([&](const Ec& ec) {
                if(ec || completed) return;
                if(stopping_.load() || Clock::now() >= deadline_) close();
                else arm();
            });
        };
        arm();
        start([&](const Ec& ec) {
            completed = true; success = !ec; timer.cancel();
        });
        io_.run();
        return completed && success;
    }
    template<class Stream> bool read(Stream& stream, std::vector<std::uint8_t>& bytes) {
        return wait([&](auto done) {
            asio::async_read(stream, asio::buffer(bytes),
                [done](const Ec& ec, std::size_t) { done(ec); });
        });
    }
    template<class Stream> bool write(Stream& stream, const std::vector<std::uint8_t>& bytes) {
        return wait([&](auto done) {
            asio::async_write(stream, asio::buffer(bytes),
                [done](const Ec& ec, std::size_t) { done(ec); });
        });
    }
    void pause() {
        while(!stopping_.load() && Clock::now() < deadline_) std::this_thread::sleep_for(10ms);
    }
    void run() {
        require(wait([&](auto done) { acceptor_.async_accept(stream_.next_layer(), done); }), "peer accept");
        require(stream_.next_layer().remote_endpoint().address() == asio::ip::make_address("127.0.0.1"),
                "peer loopback address");
        std::vector<std::uint8_t> preamble(4);
        require(read(stream_.next_layer(), preamble) &&
                preamble == std::vector<std::uint8_t>({0, 0, 0, 1}), "TLS-only preamble");
        accepted = true;
        if(mode_ == Mode::preamble_eof || mode_ == Mode::preamble_reset) {
            if(mode_ == Mode::preamble_reset) stream_.next_layer().set_option(asio::socket_base::linger(true, 0));
            served = true; return;
        }
        if(mode_ == Mode::preamble_stall) { served = true; pause(); return; }
        if(mode_ == Mode::downgrade) {
            require(write(stream_.next_layer(), {255, 255, 255, 255}), "downgrade reply");
            served = true; pause(); return;
        }
        require(write(stream_.next_layer(), {0, 0, 0, 0}), "TLS preamble reply");
        if(mode_ == Mode::plaintext) {
            require(write(stream_.next_layer(), {'N', 'O', ' ', 'T', 'L', 'S', '\n'}), "plaintext reply");
            served = true; pause(); return;
        }
        if(mode_ == Mode::tls_stall) { served = true; pause(); return; }
        const bool handshake = wait([&](auto done) {
            stream_.async_handshake(asio::ssl::stream_base::server, done);
        });
        if(mode_ == Mode::untrusted) {
            require(!handshake, "untrusted peer must not finish TLS"); served = true; return;
        }
        require(handshake, "peer TLS handshake");
        secured = true;
        if(mode_ == Mode::receive_stall) { served = true; pause(); return; }
        std::vector<std::uint8_t> reply;
        if(mode_ == Mode::echo) {
            std::vector<std::uint8_t> header(4), body(payload.size());
            require(read(stream_, header) && header == std::vector<std::uint8_t>({0, 0, 0,
                        static_cast<std::uint8_t>(payload.size())}), "request frame header");
            require(read(stream_, body) && body == payload, "request frame payload");
            request_received = true;
            reply = header; reply.insert(reply.end(), body.begin(), body.end());
        } else if(mode_ == Mode::push) {
            reply = {0, 0, 0, static_cast<std::uint8_t>(payload.size())};
            reply.insert(reply.end(), payload.begin(), payload.end());
        } else if(mode_ == Mode::oversized) reply = {0, 1, 0, 1};
        else if(mode_ == Mode::empty) reply = {0, 0, 0, 0};
        else if(mode_ == Mode::partial_header) reply = {0, 0};
        else if(mode_ == Mode::partial_body) reply = {0, 0, 0, 8, 1, 2, 3};
        else throw std::runtime_error("unexpected peer mode");
        require(write(stream_, reply), "peer response frame");
        served = true;
        // Partial frames deliberately end without a TLS close-notify. Positive
        // cases remain open until the client is done, avoiding a reset race.
        if(mode_ != Mode::partial_header && mode_ != Mode::partial_body) pause();
    }
public:
    const std::uint16_t port;
    // Written by worker and read only after finish() joins it.
    bool accepted = false, secured = false, served = false, request_received = false;
    Peer(const Identity& identity, Mode mode, int version = TLS1_3_VERSION)
        : context_(server_context(identity, version)), stream_(io_, context_),
          acceptor_(io_, Tcp::endpoint(asio::ip::make_address("127.0.0.1"), 0)),
          mode_(mode), port(acceptor_.local_endpoint().port()) {
        worker_ = std::thread([this] {
            try { run(); }
            catch(...) { failed_ = true; }
            close();
        });
    }
    ~Peer() { finish(); }
    void finish() {
        stopping_.store(true);
        if(worker_.joinable()) worker_.join();
    }
    bool passed() const { return !failed_ && accepted && served; }
};

struct Canceller {
    alliance::CancellationToken& token;
    std::mutex mutex;
    std::condition_variable changed;
    bool done = false;
    std::thread worker;
    explicit Canceller(alliance::CancellationToken& value) : token(value), worker([this] {
        std::unique_lock<std::mutex> lock(mutex);
        if(!changed.wait_for(lock, 100ms, [this] { return done; })) token.cancel();
    }) {}
    ~Canceller() {
        { std::lock_guard<std::mutex> lock(mutex); done = true; }
        changed.notify_all(); worker.join();
    }
};

void expect(const alliance::Result& result, alliance::Error error, alliance::Stage stage) {
    require(result.error == error && result.stage == stage && result.frame.empty(), "unexpected client result");
}
void guard_case(const char* name, const alliance::TrustedProfile& profile, alliance::Error expected) {
    run(name);
    expect(alliance::validate_profile(profile), expected, alliance::Stage::profile);
    alliance::Client client;
    alliance::CancellationToken cancellation;
    expect(client.connect(profile, 1s, cancellation), expected, alliance::Stage::profile);
    pass(name);
}
void success_case(const char* name, const Identity& identity, Mode mode, int version) {
    run(name);
    Peer peer(identity, mode, version);
    alliance::Client client;
    alliance::CancellationToken cancellation;
    auto result = client.connect(identity.profile(peer.port), 5s, cancellation);
    require(result && result.tls_version == (version == TLS1_2_VERSION ? "TLSv1.2" : "TLSv1.3"),
            "negotiated TLS version");
    if(mode == Mode::echo) require(bool(client.send_frame(payload, 5s, cancellation)), "client frame write");
    result = client.receive_frame(5s, cancellation);
    require(result && result.frame == payload, "client exact frame response");
    peer.finish();
    require(peer.passed() && peer.secured && (mode != Mode::echo || peer.request_received),
            "positive peer completion");
    pass(name);
}
void reject_case(const char* name, const Identity& trusted, const Identity& served,
                 Mode mode, alliance::Error error, alliance::Stage stage, bool receive = false,
                 bool cancel = false) {
    run(name);
    Peer peer(served, mode);
    alliance::Client client;
    alliance::CancellationToken cancellation;
    const bool stall_connect = mode == Mode::preamble_stall || mode == Mode::tls_stall;
    auto operation_started = Clock::now();
    auto result = client.connect(trusted.profile(peer.port), stall_connect ? 700ms : 5s, cancellation);
    if(receive) {
        require(bool(result), "negative frame case TLS setup");
        operation_started = Clock::now();
        if(cancel) {
            Canceller canceller(cancellation);
            result = client.receive_frame(3s, cancellation);
        } else result = client.receive_frame(700ms, cancellation);
    }
    const auto elapsed = Clock::now() - operation_started;
    expect(result, error, stage);
    // The peer's emergency deadline must never make a broken client timer
    // look successful. Measure only the operation under test, not TLS setup.
    if(error == alliance::Error::timeout) {
        require(elapsed >= 600ms, "timeout returned before its deadline");
        require(elapsed < 3s, "timeout exceeded client-operation bound");
    }
    if(error == alliance::Error::cancelled)
        require(elapsed < 2s, "cancellation exceeded client-operation bound");
    peer.finish();
    require(peer.passed(), "negative peer completion");
    require(!receive || peer.secured, "negative frame case peer TLS");
    pass(name);
}
} // namespace

int main(int argc, char**) {
    try {
        require(argc == 1, "no command-line arguments are accepted");
        require(OpenSSL_version_num() == OPENSSL_VERSION_NUMBER, "OpenSSL runtime/header version mismatch");
        std::cout << "WESNOTH_ANDROID_ALLIANCE abi="
#ifdef __ANDROID__
                  << "x86_64 min_api=" << __ANDROID_API__
#else
                  << "host min_api=0"
#endif
                  << " boost=" << BOOST_LIB_VERSION << " openssl=0x" << std::hex
                  << OpenSSL_version_num() << std::dec << std::endl;
        const Identity trusted, untrusted, expired(true);
        const auto profile = trusted.profile(1);
        require(bool(alliance::validate_profile(profile)), "trusted profile validation");
        run("before-connect-guards");
        alliance::Client disconnected;
        alliance::CancellationToken token;
        expect(disconnected.send_frame(payload, 1s, token), alliance::Error::not_connected, alliance::Stage::send_frame);
        expect(disconnected.receive_frame(1s, token), alliance::Error::not_connected, alliance::Stage::receive_header);
        expect(disconnected.connect(profile, 0ms, token), alliance::Error::invalid_argument, alliance::Stage::connect);
        token.cancel();
        expect(disconnected.connect(profile, 1s, token), alliance::Error::cancelled, alliance::Stage::connect);
        pass("before-connect-guards");
        auto wrong = profile;
        wrong.address = "127.0.0.2";
        guard_case("wrong-ip", wrong, alliance::Error::certificate_rejected);
        wrong = profile; wrong.certificate_sha256[0] = wrong.certificate_sha256[0] == '0' ? '1' : '0';
        guard_case("wrong-pin", wrong, alliance::Error::pin_mismatch);
        wrong = profile; wrong.address = "localhost";
        guard_case("hostname-refused", wrong, alliance::Error::invalid_profile);
        wrong = profile; wrong.address = "255.255.255.255";
        guard_case("broadcast-refused", wrong, alliance::Error::invalid_profile);
        guard_case("expired", expired.profile(1), alliance::Error::certificate_rejected);
        success_case("TLS1.2", trusted, Mode::echo, TLS1_2_VERSION);
        success_case("TLS1.3", trusted, Mode::echo, TLS1_3_VERSION);
        success_case("server-push", trusted, Mode::push, TLS1_3_VERSION);
        reject_case("untrusted", trusted, untrusted, Mode::untrusted,
                    alliance::Error::certificate_rejected, alliance::Stage::tls_handshake);
        reject_case("downgrade", trusted, trusted, Mode::downgrade,
                    alliance::Error::tls_required, alliance::Stage::preamble_read);
        reject_case("plaintext", trusted, trusted, Mode::plaintext,
                    alliance::Error::tls_failed, alliance::Stage::tls_handshake);
        reject_case("preamble-eof", trusted, trusted, Mode::preamble_eof,
                    alliance::Error::preamble_failed, alliance::Stage::preamble_read);
        reject_case("preamble-reset", trusted, trusted, Mode::preamble_reset,
                    alliance::Error::preamble_failed, alliance::Stage::preamble_read);
        reject_case("preamble-stall", trusted, trusted, Mode::preamble_stall,
                    alliance::Error::timeout, alliance::Stage::preamble_read);
        reject_case("tls-stall", trusted, trusted, Mode::tls_stall,
                    alliance::Error::timeout, alliance::Stage::tls_handshake);
        reject_case("oversized", trusted, trusted, Mode::oversized,
                    alliance::Error::invalid_frame, alliance::Stage::receive_header, true);
        reject_case("empty-frame", trusted, trusted, Mode::empty,
                    alliance::Error::invalid_frame, alliance::Stage::receive_header, true);
        reject_case("partial-header", trusted, trusted, Mode::partial_header,
                    alliance::Error::read_failed, alliance::Stage::receive_header, true);
        reject_case("partial-body", trusted, trusted, Mode::partial_body,
                    alliance::Error::read_failed, alliance::Stage::receive_body, true);
        reject_case("receive-stall", trusted, trusted, Mode::receive_stall,
                    alliance::Error::timeout, alliance::Stage::receive_header, true);
        reject_case("cancel", trusted, trusted, Mode::receive_stall,
                    alliance::Error::cancelled, alliance::Stage::receive_header, true, true);
        std::cout << "ALLIANCE ANDROID TRANSPORT CHECK PASSED" << std::endl;
        return 0;
    } catch(const std::exception& error) {
        // Exceptions contain fixed fixture labels, never key/certificate data.
        std::cerr << "FAIL Alliance Android transport check: " << error.what() << std::endl;
        return 1;
    } catch(...) {
        std::cerr << "FAIL Alliance Android transport check: unexpected fixture error" << std::endl;
        return 1;
    }
}
