#include <curl/curl.h>
#include <openssl/crypto.h>
#include <openssl/err.h>
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <openssl/ssl.h>
#include <openssl/x509v3.h>

#include <arpa/inet.h>
#include <fcntl.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <exception>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>

#if !defined(__ANDROID__) || !defined(__x86_64__) || __ANDROID_API__ != 23
#error "Build the selected Android x86_64/API23 probe."
#endif
#if OPENSSL_VERSION_NUMBER != 0x30400010L || LIBCURL_VERSION_NUM != 0x080101
#error "This probe requires the pinned OpenSSL 3.4.1 and curl 8.1.1 headers."
#endif

static void require(bool condition, const char* message)
{
    if (!condition) throw std::runtime_error(message);
}

template<typename T, void (*Free)(T*)>
using Owned = std::unique_ptr<T, decltype(Free)>;
using Key = Owned<EVP_PKEY, EVP_PKEY_free>;
using Certificate = Owned<X509, X509_free>;
using Context = Owned<SSL_CTX, SSL_CTX_free>;
using Store = Owned<X509_STORE, X509_STORE_free>;

static void crypto_checks()
{
    std::array<unsigned char, 32> digest{};
    unsigned int digest_size = 0;
    const unsigned char expected[] = {
        0xba, 0x78, 0x16, 0xbf, 0x8f, 0x01, 0xcf, 0xea, 0x41, 0x41, 0x40, 0xde, 0x5d, 0xae, 0x22, 0x23,
        0xb0, 0x03, 0x61, 0xa3, 0x96, 0x17, 0x7a, 0x9c, 0xb4, 0x10, 0xff, 0x61, 0xf2, 0x00, 0x15, 0xad};
    require(EVP_Digest("abc", 3, digest.data(), &digest_size, EVP_sha256(), nullptr) == 1
            && digest_size == digest.size() && std::equal(digest.begin(), digest.end(), expected),
            "OpenSSL SHA256 known answer");
    struct Secrets {
        std::array<unsigned char, 32> key{};
        std::array<unsigned char, 12> nonce{};
        ~Secrets() { OPENSSL_cleanse(key.data(), key.size()); }
    } secrets;
    require(RAND_status() == 1 && RAND_bytes(secrets.key.data(), secrets.key.size()) == 1
            && RAND_bytes(secrets.nonce.data(), secrets.nonce.size()) == 1,
            "OpenSSL operating system entropy");
    Owned<EVP_CIPHER, EVP_CIPHER_free> cipher(EVP_CIPHER_fetch(nullptr, "AES-256-GCM", nullptr), EVP_CIPHER_free);
    Owned<EVP_CIPHER_CTX, EVP_CIPHER_CTX_free> encrypt(EVP_CIPHER_CTX_new(), EVP_CIPHER_CTX_free);
    require(cipher && encrypt, "OpenSSL AES-GCM provider/context");
    const std::string plaintext("Wesnoth\0offline authenticated payload", 37);
    const std::string aad("wesnoth-network-check-v1");
    std::array<unsigned char, 128> ciphertext{};
    std::array<unsigned char, 16> tag{};
    int count = 0, final_count = 0;
    require(EVP_EncryptInit_ex2(encrypt.get(), cipher.get(), secrets.key.data(), secrets.nonce.data(), nullptr) == 1
            && EVP_EncryptUpdate(encrypt.get(), nullptr, &count,
                reinterpret_cast<const unsigned char*>(aad.data()), aad.size()) == 1
            && EVP_EncryptUpdate(encrypt.get(), ciphertext.data(), &count,
                reinterpret_cast<const unsigned char*>(plaintext.data()), plaintext.size()) == 1,
            "OpenSSL AES-GCM encryption");
    const int encrypted_size = count;
    require(EVP_EncryptFinal_ex(encrypt.get(), ciphertext.data() + encrypted_size, &final_count) == 1
            && final_count == 0 && encrypted_size == static_cast<int>(plaintext.size())
            && EVP_CIPHER_CTX_ctrl(encrypt.get(), EVP_CTRL_AEAD_GET_TAG, tag.size(), tag.data()) == 1,
            "OpenSSL AES-GCM authentication tag");
    auto decrypt = [&](bool tamper) {
        Owned<EVP_CIPHER_CTX, EVP_CIPHER_CTX_free> context(EVP_CIPHER_CTX_new(), EVP_CIPHER_CTX_free);
        std::array<unsigned char, 128> recovered{};
        auto supplied_tag = tag;
        if (tamper) supplied_tag[0] ^= 1;
        require(context && EVP_DecryptInit_ex2(context.get(), cipher.get(), secrets.key.data(), secrets.nonce.data(), nullptr) == 1
                && EVP_DecryptUpdate(context.get(), nullptr, &count,
                    reinterpret_cast<const unsigned char*>(aad.data()), aad.size()) == 1
                && EVP_DecryptUpdate(context.get(), recovered.data(), &count, ciphertext.data(), encrypted_size) == 1
                && count == encrypted_size
                && EVP_CIPHER_CTX_ctrl(context.get(), EVP_CTRL_AEAD_SET_TAG, supplied_tag.size(), supplied_tag.data()) == 1,
                "OpenSSL AES-GCM decryption setup");
        const int accepted = EVP_DecryptFinal_ex(context.get(), recovered.data() + count, &final_count);
        if (tamper) require(accepted <= 0, "OpenSSL rejects modified authentication tag");
        else require(accepted == 1 && final_count == 0
                     && std::memcmp(recovered.data(), plaintext.data(), plaintext.size()) == 0,
                     "OpenSSL authenticated payload round trip");
        OPENSSL_cleanse(recovered.data(), recovered.size());
    };
    decrypt(false);
    decrypt(true);
    ERR_clear_error();
    std::puts("PASS OpenSSL SHA256 and authenticated AES-GCM");
}

static Key make_key()
{
    Key key(EVP_PKEY_Q_keygen(nullptr, nullptr, "EC", "P-256"), EVP_PKEY_free);
    require(static_cast<bool>(key), "OpenSSL ephemeral P-256 key generation");
    return key;
}

static void extension(X509* cert, X509* issuer, int nid, const char* value)
{
    X509V3_CTX context{};
    X509V3_set_ctx(&context, issuer, cert, nullptr, nullptr, 0);
    Owned<X509_EXTENSION, X509_EXTENSION_free> ext(
        X509V3_EXT_conf_nid(nullptr, &context, nid, value), X509_EXTENSION_free);
    require(ext && X509_add_ext(cert, ext.get(), -1) == 1, "OpenSSL certificate extension");
}

static Certificate make_certificate(EVP_PKEY* key, X509* issuer, EVP_PKEY* signing_key,
                                    bool wrong_host = false, bool expired = false)
{
    Certificate cert(X509_new(), X509_free);
    const bool root = issuer == nullptr;
    require(cert && X509_set_version(cert.get(), 2) == 1
            && ASN1_INTEGER_set(X509_get_serialNumber(cert.get()), root ? 1 : (wrong_host ? 3 : 2)) == 1
            && X509_gmtime_adj(X509_getm_notBefore(cert.get()), expired ? -7200 : -300)
            && X509_gmtime_adj(X509_getm_notAfter(cert.get()), expired ? -3600 : 3600)
            && X509_set_pubkey(cert.get(), key) == 1, "OpenSSL ephemeral certificate fields");
    X509_NAME* subject = X509_get_subject_name(cert.get());
    const char* name = root ? "Wesnoth ephemeral probe CA" : "Wesnoth ephemeral loopback";
    require(subject && X509_NAME_add_entry_by_txt(subject, "CN", MBSTRING_ASC,
            reinterpret_cast<const unsigned char*>(name), -1, -1, 0) == 1
            && X509_set_issuer_name(cert.get(), root ? subject : X509_get_subject_name(issuer)) == 1,
            "OpenSSL ephemeral certificate names");
    X509* actual_issuer = root ? cert.get() : issuer;
    extension(cert.get(), actual_issuer, NID_basic_constraints, root ? "critical,CA:TRUE,pathlen:0" : "critical,CA:FALSE");
    extension(cert.get(), actual_issuer, NID_key_usage, root ? "critical,keyCertSign,cRLSign" : "critical,digitalSignature");
    extension(cert.get(), actual_issuer, NID_subject_key_identifier, "hash");
    if (!root) {
        extension(cert.get(), issuer, NID_authority_key_identifier, "keyid:always");
        extension(cert.get(), issuer, NID_ext_key_usage, "serverAuth");
        extension(cert.get(), issuer, NID_subject_alt_name, wrong_host ? "IP:127.0.0.2" : "IP:127.0.0.1");
    }
    require(X509_sign(cert.get(), signing_key, EVP_sha256()) > 0, "OpenSSL ephemeral certificate signature");
    return cert;
}

static int verify_certificate(X509* leaf, X509* root)
{
    Store store(X509_STORE_new(), X509_STORE_free);
    Owned<X509_STORE_CTX, X509_STORE_CTX_free> context(X509_STORE_CTX_new(), X509_STORE_CTX_free);
    require(store && context && X509_STORE_set_flags(store.get(), X509_V_FLAG_X509_STRICT) == 1,
            "OpenSSL strict certificate store");
    if (root) require(X509_STORE_add_cert(store.get(), root) == 1, "OpenSSL private CA trust");
    require(X509_STORE_CTX_init(context.get(), store.get(), leaf, nullptr) == 1
            && X509_STORE_CTX_set_purpose(context.get(), X509_PURPOSE_SSL_SERVER) == 1
            && X509_VERIFY_PARAM_set1_ip_asc(X509_STORE_CTX_get0_param(context.get()), "127.0.0.1") == 1,
            "OpenSSL certificate verification parameters");
    const int verified = X509_verify_cert(context.get());
    const int error = X509_STORE_CTX_get_error(context.get());
    require((verified == 1) == (error == X509_V_OK), "OpenSSL verification result consistency");
    ERR_clear_error();
    return error;
}

struct Fd {
    int value = -1;
    explicit Fd(int fd) : value(fd) {}
    ~Fd() { if (value >= 0) ::close(value); }
    Fd(const Fd&) = delete;
    Fd& operator=(const Fd&) = delete;
};

using Clock = std::chrono::steady_clock;
static bool wait_socket(int fd, short events, Clock::time_point deadline)
{
    for (;;) {
        const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - Clock::now()).count();
        if (remaining <= 0) return false;
        pollfd entry{fd, events, 0};
        const int result = ::poll(&entry, 1, static_cast<int>(remaining));
        if (result > 0) return (entry.revents & events) != 0;
        if (result == 0 || errno != EINTR) return false;
    }
}

static bool retry_ssl(SSL* ssl, int result, int fd, Clock::time_point deadline)
{
    const int error = SSL_get_error(ssl, result);
    if (error == SSL_ERROR_WANT_READ) return wait_socket(fd, POLLIN, deadline);
    if (error == SSL_ERROR_WANT_WRITE) return wait_socket(fd, POLLOUT, deadline);
    return false;
}

enum class Reply { normal, not_found, truncated };

struct Server {
    Context context{nullptr, SSL_CTX_free};
    Fd listener{::socket(AF_INET, SOCK_STREAM, 0)};
    unsigned short port = 0;
    std::thread worker;
    std::exception_ptr failure;
    bool request_received = false;
    int negotiated_version = 0;

    Server(X509* cert, EVP_PKEY* key, Reply reply, bool rejection_expected)
    {
        require(listener.value >= 0, "OpenSSL loopback socket creation");
        const int listener_flags = ::fcntl(listener.value, F_GETFL, 0);
        require(listener_flags >= 0 && ::fcntl(listener.value, F_SETFL, listener_flags | O_NONBLOCK) == 0,
                "OpenSSL nonblocking listener");
        context.reset(SSL_CTX_new(TLS_server_method()));
        require(context && SSL_CTX_set_min_proto_version(context.get(), TLS1_2_VERSION) == 1
                && SSL_CTX_set_max_proto_version(context.get(), TLS1_3_VERSION) == 1
                && SSL_CTX_use_certificate(context.get(), cert) == 1
                && SSL_CTX_use_PrivateKey(context.get(), key) == 1
                && SSL_CTX_check_private_key(context.get()) == 1,
                "OpenSSL local TLS server context");
        sockaddr_in address{};
        address.sin_family = AF_INET;
        address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        require(::bind(listener.value, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0
                && ::listen(listener.value, 1) == 0, "OpenSSL loopback-only ephemeral listener");
        socklen_t length = sizeof(address);
        require(::getsockname(listener.value, reinterpret_cast<sockaddr*>(&address), &length) == 0
                && length == sizeof(address) && address.sin_addr.s_addr == htonl(INADDR_LOOPBACK)
                && address.sin_port != 0, "OpenSSL loopback listener address");
        port = ntohs(address.sin_port);
        worker = std::thread([this, reply, rejection_expected] {
            try { serve(reply, rejection_expected); }
            catch (...) { failure = std::current_exception(); }
        });
    }
    ~Server() { if (worker.joinable()) worker.join(); }
    void finish() { worker.join(); if (failure) std::rethrow_exception(failure); }

    void serve(Reply reply, bool rejection_expected)
    {
        const auto deadline = Clock::now() + std::chrono::seconds(4);
        sockaddr_in peer{};
        int accepted = -1;
        while (accepted < 0) {
            require(wait_socket(listener.value, POLLIN, deadline), "OpenSSL loopback accept timeout");
            socklen_t peer_size = sizeof(peer);
            accepted = ::accept(listener.value, reinterpret_cast<sockaddr*>(&peer), &peer_size);
            require(accepted >= 0 || errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK,
                    "OpenSSL loopback accept");
        }
        Fd connection(accepted);
        require(peer.sin_addr.s_addr == htonl(INADDR_LOOPBACK), "OpenSSL accepted peer must be loopback");
        const int flags = ::fcntl(connection.value, F_GETFL, 0);
        require(flags >= 0 && ::fcntl(connection.value, F_SETFL, flags | O_NONBLOCK) == 0,
                "OpenSSL nonblocking accepted socket");
        Owned<SSL, SSL_free> ssl(SSL_new(context.get()), SSL_free);
        require(ssl && SSL_set_fd(ssl.get(), connection.value) == 1, "OpenSSL accepted TLS stream");
        for (;;) {
            ERR_clear_error();
            const int result = SSL_accept(ssl.get());
            if (result == 1) break;
            if (retry_ssl(ssl.get(), result, connection.value, deadline)) continue;
            require(rejection_expected, "OpenSSL loopback TLS handshake");
            return;
        }
        negotiated_version = SSL_version(ssl.get());
        std::string request;
        while (request.find("\r\n\r\n") == std::string::npos) {
            std::array<char, 512> buffer{};
            ERR_clear_error();
            const int count = SSL_read(ssl.get(), buffer.data(), buffer.size());
            if (count > 0) {
                request.append(buffer.data(), static_cast<std::size_t>(count));
                require(request.size() <= 4096, "OpenSSL bounded HTTP request");
                continue;
            }
            if (retry_ssl(ssl.get(), count, connection.value, deadline)) continue;
            require(rejection_expected, "OpenSSL loopback HTTP request read");
            return;
        }
        request_received = true;
        require(!rejection_expected && request.rfind("GET /offline-check HTTP/1.1\r\n", 0) == 0,
                "OpenSSL expected HTTP request after verified TLS");
        const std::string payload("Wesnoth\0HTTPS\n", 14);
        const bool missing = reply == Reply::not_found;
        const std::string body = missing ? std::string("not found") : payload;
        const std::string response = std::string(missing ? "HTTP/1.1 404 Not Found\r\n" : "HTTP/1.1 200 OK\r\n")
            + "Content-Length: " + std::to_string(body.size() + (reply == Reply::truncated ? 5 : 0))
            + "\r\nConnection: close\r\nContent-Type: application/octet-stream\r\n\r\n" + body;
        std::size_t sent = 0;
        while (sent < response.size()) {
            ERR_clear_error();
            const int count = SSL_write(ssl.get(), response.data() + sent, static_cast<int>(response.size() - sent));
            if (count > 0) sent += static_cast<std::size_t>(count);
            else require(retry_ssl(ssl.get(), count, connection.value, deadline), "OpenSSL bounded HTTP response write");
        }
        // Emit close_notify without waiting for the peer's reply. The socket is
        // then closed by its owner. This makes truncated HTTP content distinct
        // from a truncated TLS record and prevents an unbounded shutdown wait.
        for (;;) {
            ERR_clear_error();
            const int result = SSL_shutdown(ssl.get());
            if (result >= 0 || !retry_ssl(ssl.get(), result, connection.value, deadline)) break;
        }
    }
};

struct Response {
    std::string body;
    bool limit_hit = false;
};

static std::size_t write_response(char* bytes, std::size_t size, std::size_t count, void* userdata) noexcept
{
    auto& response = *static_cast<Response*>(userdata);
    if (size && count > 4096 / size) { response.limit_hit = true; return 0; }
    const std::size_t length = size * count;
    if (length > 4096 - response.body.size()) { response.limit_hit = true; return 0; }
    try { response.body.append(bytes, length); }
    catch (...) { return 0; }
    return length;
}

struct Trust { X509* root; bool installed = false; };
static CURLcode install_trust(CURL*, void* raw_context, void* raw_trust) noexcept
{
    auto& trust = *static_cast<Trust*>(raw_trust);
    Store store(X509_STORE_new(), X509_STORE_free);
    if (!store || X509_STORE_set_flags(store.get(), X509_V_FLAG_X509_STRICT) != 1) return CURLE_OUT_OF_MEMORY;
    if (trust.root && X509_STORE_add_cert(store.get(), trust.root) != 1) return CURLE_SSL_CERTPROBLEM;
    // Replace the context's store; never augment ambient device trust. An empty
    // store is used only for the negative test and must reject the same leaf.
    SSL_CTX_set_cert_store(static_cast<SSL_CTX*>(raw_context), store.release());
    trust.installed = true;
    return CURLE_OK;
}

static void curl_case(X509* cert, EVP_PKEY* key, X509* root, Reply reply,
                      CURLcode expected_result, int tls_version)
{
    const bool rejection_expected = expected_result == CURLE_PEER_FAILED_VERIFICATION;
    Server server(cert, key, reply, rejection_expected);
    Owned<CURL, curl_easy_cleanup> curl(curl_easy_init(), curl_easy_cleanup);
    require(static_cast<bool>(curl), "curl easy allocation");
    const std::string url = "https://127.0.0.1:" + std::to_string(server.port) + "/offline-check";
    Response response;
    Trust trust{root};
    std::array<char, CURL_ERROR_SIZE> error{};
    auto option = [&](CURLoption name, auto value) {
        require(curl_easy_setopt(curl.get(), name, value) == CURLE_OK, "curl option setup");
    };
    option(CURLOPT_URL, url.c_str());
    option(CURLOPT_PROXY, "");
    option(CURLOPT_NOPROXY, "*");
    option(CURLOPT_PROTOCOLS_STR, "https");
    option(CURLOPT_REDIR_PROTOCOLS_STR, "https");
    option(CURLOPT_FOLLOWLOCATION, 0L);
    option(CURLOPT_NOSIGNAL, 1L);
    option(CURLOPT_TIMEOUT_MS, 4000L);
    option(CURLOPT_CONNECTTIMEOUT_MS, 3000L);
    option(CURLOPT_HTTP_VERSION, static_cast<long>(CURL_HTTP_VERSION_1_1));
    option(CURLOPT_IPRESOLVE, static_cast<long>(CURL_IPRESOLVE_V4));
    option(CURLOPT_SSL_VERIFYPEER, 1L);
    option(CURLOPT_SSL_VERIFYHOST, 2L);
    option(CURLOPT_SSLVERSION, tls_version == TLS1_2_VERSION
        ? static_cast<long>(CURL_SSLVERSION_TLSv1_2 | CURL_SSLVERSION_MAX_TLSv1_2)
        : static_cast<long>(CURL_SSLVERSION_TLSv1_3 | CURL_SSLVERSION_MAX_TLSv1_3));
    option(CURLOPT_CAINFO, static_cast<const char*>(nullptr));
    option(CURLOPT_CAPATH, static_cast<const char*>(nullptr));
    option(CURLOPT_SSL_CTX_FUNCTION, install_trust);
    option(CURLOPT_SSL_CTX_DATA, &trust);
    option(CURLOPT_FRESH_CONNECT, 1L);
    option(CURLOPT_FORBID_REUSE, 1L);
    option(CURLOPT_FAILONERROR, 1L);
    option(CURLOPT_WRITEFUNCTION, write_response);
    option(CURLOPT_WRITEDATA, &response);
    option(CURLOPT_ERRORBUFFER, error.data());
    const CURLcode result = curl_easy_perform(curl.get());
    server.finish();
    if (result != expected_result)
        std::fprintf(stderr, "curl case: expected=%d actual=%d detail=%s\n", expected_result, result, error.data());
    require(result == expected_result && trust.installed && !response.limit_hit, "curl expected transfer result and private trust");
    long status = 0, verification = 0;
    require(curl_easy_getinfo(curl.get(), CURLINFO_RESPONSE_CODE, &status) == CURLE_OK
            && curl_easy_getinfo(curl.get(), CURLINFO_SSL_VERIFYRESULT, &verification) == CURLE_OK,
            "curl HTTP/certificate result inspection");
    if (rejection_expected) {
        require(!server.request_received && status == 0 && response.body.empty() && error[0] != '\0',
                "curl rejects certificate before HTTP payload");
    } else {
        char* connected_address = nullptr;
        require(curl_easy_getinfo(curl.get(), CURLINFO_PRIMARY_IP, &connected_address) == CURLE_OK
                && connected_address && std::strcmp(connected_address, "127.0.0.1") == 0
                && verification == X509_V_OK && server.request_received && server.negotiated_version == tls_version,
                "curl verified loopback peer and exact TLS version");
        if (reply == Reply::not_found)
            require(status == 404 && response.body.empty() && error[0] != '\0', "curl FAILONERROR rejects HTTP404");
        else require(status == 200 && response.body == std::string("Wesnoth\0HTTPS\n", 14),
                     "curl binary HTTP payload and status");
        if (reply == Reply::truncated) require(error[0] != '\0', "curl truncated-response error detail");
    }
    ERR_clear_error();
}

static void curl_metadata()
{
    const curl_version_info_data* version = curl_version_info(CURLVERSION_NOW);
    require(version && version->version_num == LIBCURL_VERSION_NUM && version->ssl_version
            && std::strcmp(version->ssl_version, "OpenSSL/3.4.1") == 0
            && (version->features & CURL_VERSION_SSL) && (version->features & CURL_VERSION_LIBZ),
            "curl pinned runtime, OpenSSL backend and zlib support");
    bool https = false;
    for (auto p = version->protocols; p && *p; ++p) if (std::strcmp(*p, "https") == 0) https = true;
    require(https, "curl HTTPS protocol");
    Owned<CURLU, curl_url_cleanup> url(curl_url(), curl_url_cleanup);
    require(url && curl_url_set(url.get(), CURLUPART_URL, "https://127.0.0.1:15000/offline-check?q=1", 0) == CURLUE_OK,
            "curl structured URL parsing");
    char* host = nullptr;
    require(curl_url_get(url.get(), CURLUPART_HOST, &host, 0) == CURLUE_OK, "curl URL host retrieval");
    const bool correct = host && std::strcmp(host, "127.0.0.1") == 0;
    curl_free(host);
    require(correct && curl_url_set(url.get(), CURLUPART_URL, "https://[invalid", 0) != CURLUE_OK,
            "curl URL host and malformed input rejection");
    std::puts("PASS curl URL parsing and pinned TLS backend");
}

int main(int argc, char**)
{
    try {
        require(argc == 1, "No command-line arguments are accepted");
        require(std::signal(SIGPIPE, SIG_IGN) != SIG_ERR, "Ignore closed-socket SIGPIPE for bounded tests");
        // Initialize before curl or any provider call. No openssl.cnf, external
        // trust bundle, private key file, proxy, or network host is consulted.
        require(OPENSSL_init_ssl(OPENSSL_INIT_NO_LOAD_CONFIG, nullptr) == 1
                && OpenSSL_version_num() == OPENSSL_VERSION_NUMBER, "OpenSSL pinned runtime initialization");
        require(curl_global_init(CURL_GLOBAL_DEFAULT) == CURLE_OK, "curl global initialization");
        struct CurlCleanup { ~CurlCleanup() { curl_global_cleanup(); } } cleanup;
        std::puts("WESNOTH_ANDROID_NETWORK version=1.19.26 abi=x86_64 min_api=23");
        std::puts("OpenSSL=3.4.1 curl=8.1.1; ephemeral private trust; loopback only; no external connections");
        crypto_checks();
        auto ca_key = make_key();
        auto ca = make_certificate(ca_key.get(), nullptr, ca_key.get());
        auto leaf_key = make_key();
        auto leaf = make_certificate(leaf_key.get(), ca.get(), ca_key.get());
        auto wrong_host = make_certificate(leaf_key.get(), ca.get(), ca_key.get(), true);
        auto expired = make_certificate(leaf_key.get(), ca.get(), ca_key.get(), false, true);
        require(verify_certificate(leaf.get(), ca.get()) == X509_V_OK, "OpenSSL valid private certificate chain");
        require(verify_certificate(leaf.get(), nullptr) == X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT_LOCALLY,
                "OpenSSL rejects untrusted certificate chain");
        require(verify_certificate(wrong_host.get(), ca.get()) == X509_V_ERR_IP_ADDRESS_MISMATCH,
                "OpenSSL rejects certificate identity mismatch");
        require(verify_certificate(expired.get(), ca.get()) == X509_V_ERR_CERT_HAS_EXPIRED,
                "OpenSSL rejects expired certificate");
        std::puts("PASS OpenSSL ephemeral certificate chain and rejection");
        curl_metadata();
        curl_case(leaf.get(), leaf_key.get(), ca.get(), Reply::normal, CURLE_OK, TLS1_2_VERSION);
        curl_case(leaf.get(), leaf_key.get(), ca.get(), Reply::normal, CURLE_OK, TLS1_3_VERSION);
        std::puts("PASS curl verified loopback HTTPS TLS1.2 and TLS1.3");
        curl_case(leaf.get(), leaf_key.get(), nullptr, Reply::normal, CURLE_PEER_FAILED_VERIFICATION, TLS1_3_VERSION);
        curl_case(wrong_host.get(), leaf_key.get(), ca.get(), Reply::normal, CURLE_PEER_FAILED_VERIFICATION, TLS1_3_VERSION);
        curl_case(leaf.get(), leaf_key.get(), ca.get(), Reply::not_found, CURLE_HTTP_RETURNED_ERROR, TLS1_3_VERSION);
        curl_case(leaf.get(), leaf_key.get(), ca.get(), Reply::truncated, CURLE_PARTIAL_FILE, TLS1_3_VERSION);
        std::puts("PASS curl certificate hostname and HTTP failure rejection");
        std::puts("PASS wesnoth-android-network");
        return 0;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "FAIL wesnoth-android-network: %s\n", error.what());
        ERR_print_errors_fp(stderr);
        return 1;
    }
}
