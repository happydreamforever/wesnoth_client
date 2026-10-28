"""Strict Alliance socket overlay; does not change the stock connection layout."""
from pathlib import Path
import hashlib


def once(text, before, after):
    if text.count(before) != 1:
        raise RuntimeError('Pinned connection overlay anchor changed')
    return text.replace(before, after, 1)


def transform_header(text):
    if hashlib.sha256(text.encode()).hexdigest() != "654ae1f630d3652256a3469eed90aebd0df31db7a20e16ca6bfe9f444a93d752":
        raise RuntimeError("Pinned connection header changed")
    text = once(text, '/** A class that represents', 'namespace alliance { struct TrustedProfile; }\n\n/** A class that represents')
    text = once(text, '\twesnothd_connection(const std::string& host, const std::string& service);', '\twesnothd_connection(const std::string& host, const std::string& service);\n\texplicit wesnothd_connection(const alliance::TrustedProfile& profile);\n\tvoid set_alliance_observer();')
    return once(text, 'private:\n', 'private:\n\tvoid alliance_fail(const boost::system::error_code&);\n')


def transform_source(text):
    if hashlib.sha256(text.encode()).hexdigest() != "8d94146259d4cd2bc4086236d2f299db2914ab36411db38597a4c79373b8e1ac":
        raise RuntimeError("Pinned connection source changed")
    text = once(text, '#include "wesnothd_connection.hpp"', '#include "wesnothd_connection.hpp"\n#include "alliance_game/shared_connection_state.hpp"')
    start = text.index('wesnothd_connection::wesnothd_connection(const std::string& host')
    end = text.index('\nwesnothd_connection::~wesnothd_connection()', start)
    ctor = text[start:end]
    strict = ctor.replace('const std::string& host, const std::string& service', 'const alliance::TrustedProfile& profile', 1).replace('host_(host)', 'host_(profile.address)').replace('service_(service)', 'service_(std::to_string(profile.port))')
    a = strict.index('\terror_code ec;')
    b = strict.index('\n\t// Starts the worker thread.', a)
    strict = strict[:a] + '''\tif((profile.port != 15000 && profile.port != 15002) || !alliance::validate_profile(profile))
        throw error(boost::asio::error::make_error_code(boost::asio::error::invalid_argument));
    auto state = std::make_shared<alliance_socket_state>(profile, io_context_);
    tls_context_.add_certificate_authority(boost::asio::buffer(profile.certificate_pem));
    if(SSL_CTX_set_min_proto_version(tls_context_.native_handle(), TLS1_2_VERSION) != 1)
        throw error(boost::asio::error::make_error_code(boost::asio::error::operation_not_supported));
    error_code ec;
    auto result = resolver_.resolve(host_, service_, boost::asio::ip::resolver_query_base::numeric_host |
        boost::asio::ip::resolver_query_base::numeric_service, ec);
    if(ec) throw error(ec);
    alliance_socket_insert(this, state);
    state->timer.expires_after(10s);
    state->timer.async_wait([this](const error_code& error) { if(!error) alliance_fail(boost::asio::error::timed_out); });
    boost::asio::post(io_context_, [this, result]() { handle_resolve({}, result); });
''' + strict[b:]
    strict = strict.replace('LOG_NW << "Resolving hostname: " << host;', 'LOG_NW << "Connecting to the pinned Alliance game service";')
    # Thread creation failure cannot leave a stale registry pointer.
    strict = strict.replace('worker_thread_ = std::thread([this]() {', 'try { worker_thread_ = std::thread([this]() {', 1)
    strict = strict.replace('\n\t});\n\n\tLOG_NW', '\n\t}); } catch(...) { alliance_socket_erase(this); throw; }\n\n\tLOG_NW', 1)
    text = text[:end] + '\n' + strict + text[end:]
    # Errors after a completed TLS handshake must reach the game reader too.
    text = text.replace('} catch(const boost::system::system_error&) {\n\t\t\ttry {', '} catch(const boost::system::system_error& e) {\n            if(alliance_socket_lookup(this)) alliance_fail(e.code());\n\t\t\ttry {')
    text = text.replace('} catch(...) {\n\t\t\tDBG_NW << "wesnothd_connection worker', '} catch(...) {\n            if(alliance_socket_lookup(this)) alliance_fail(boost::asio::error::invalid_argument);\n\t\t\tDBG_NW << "wesnothd_connection worker')
    text = once(text, '\tif(auto socket = utils::get_if<tls_socket>(&socket_)) {', '\tconst auto alliance_state = alliance_socket_lookup(this);\n\tif(!alliance_state) if(auto socket = utils::get_if<tls_socket>(&socket_)) {')
    text = once(text, '\tworker_thread_.join();', '\tworker_thread_.join();\n\talliance_socket_erase(this);')
    text = once(text, 'if(endpoint.address().is_loopback()) {', 'if(endpoint.address().is_loopback() && !alliance_socket_lookup(this)) {')
    # Strict branch precedes all historical fallback logic.
    anchor = 'void wesnothd_connection::handle_handshake(const error_code& ec)\n{\n\tMPTEST_LOG;'
    text = once(text, anchor, anchor + '''
    if(auto state = alliance_socket_lookup(this)) {
        if(ec) throw system_error(ec);
        if(handshake_response_ != 0) throw system_error(boost::asio::error::operation_not_supported);
        raw_socket raw{std::move(utils::get<raw_socket>(socket_))};
        tls_socket secure{new tls_socket::element_type{std::move(*raw), tls_context_}};
        socket_ = std::move(secure);
        auto& socket = *utils::get<tls_socket>(socket_);
        socket.set_verify_mode(boost::asio::ssl::verify_peer);
        if(X509_VERIFY_PARAM_set1_ip_asc(SSL_get0_param(socket.native_handle()), host_.c_str()) != 1)
            throw system_error(boost::asio::error::invalid_argument);
        socket.set_verify_callback([pin=state->profile.certificate_sha256](bool verified, boost::asio::ssl::verify_context& ctx) {
            if(!verified) return false;
            if(X509_STORE_CTX_get_error_depth(ctx.native_handle()) != 0) return true;
            return alliance_certificate_pin(X509_STORE_CTX_get_current_cert(ctx.native_handle())) == pin;
        });
        socket.async_handshake(boost::asio::ssl::stream_base::client, [this,state](const error_code& error) {
            if(error) throw system_error(error);
            state->timer.cancel();
            handshake_finished_.set_value();
            recv();
        });
        return;
    }
''')
    text = once(text, 'void wesnothd_connection::cancel()\n{\n\tMPTEST_LOG;', 'void wesnothd_connection::cancel()\n{\n\tMPTEST_LOG;\n    if(alliance_socket_lookup(this)) { boost::asio::post(io_context_, [this]() { alliance_fail(boost::asio::error::operation_aborted); }); return; }')
    text = once(text, '\tassert(use_tls_ == true);', '\tif(alliance_socket_lookup(this)) throw system_error(boost::asio::error::operation_not_supported);\n\tassert(use_tls_ == true);')
    text = once(text, 'auto timeout = 60s;', 'auto timeout = alliance_socket_lookup(this) ? 10s : 60s;')
    text = once(text, 'if(err.code() == boost::asio::error::operation_aborted || err.code() == boost::asio::error::eof) {', 'if(!alliance_socket_lookup(this) && (err.code() == boost::asio::error::operation_aborted || err.code() == boost::asio::error::eof)) {')
    text = once(text, '\tio::write_gz(os, request);', '''    if(auto state=alliance_socket_lookup(this); state && state->read_only_observer.load()) {
        // The stock engine generates an init_side command while catching up.
        // A spectator never transmits game actions, including that transient
        // command. Authority remains enforced independently by the daemon.
        std::ostringstream plain; io::write(plain, request);
        const config packet=io::read(plain.str());
        if(packet.attribute_count()!=0 || packet.all_children_count()!=1 ||
           (!packet.has_child("leave_game") && !packet.has_child("stop_updates") &&
            !packet.has_child("world_native_terminal"))) return;
    }
    if(alliance_socket_lookup(this)) {
        std::ostringstream plain; io::write(plain, request);
        if(plain.str().size() > alliance_plain_limit(this)) throw error(boost::asio::error::make_error_code(boost::asio::error::message_size));
    }
\tio::write_gz(os, request);
    if(alliance_socket_lookup(this) && (buf_ptr->size() == 0 || buf_ptr->size() > alliance_frame_limit(this)))
        throw error(boost::asio::error::make_error_code(boost::asio::error::message_size));''')
    text = once(text, '\t\tsend_queue_.push(std::move(buf_ptr));', '''        if(auto state=alliance_socket_lookup(this)) {
            if(send_queue_.size()>=128 || state->queued_send_bytes+buf_ptr->size()>8*1024*1024) {
                alliance_fail(boost::asio::error::no_buffer_space); return;
            }
            state->queued_send_bytes+=buf_ptr->size();
        }
\t\tsend_queue_.push(std::move(buf_ptr));''')
    text = once(text, '\tsend_queue_.pop();', '''    if(auto state=alliance_socket_lookup(this)) state->queued_send_bytes-=send_queue_.front()->size();
\tsend_queue_.pop();''')
    text = once(text, '\tbytes_read_ = bytes_transferred;','''    if(auto state=alliance_socket_lookup(this)) {
        if(bytes_transferred && !state->partial_frame) {
            state->partial_frame=true;
            state->timer.expires_after(20s);
            state->timer.async_wait([this](const error_code& error) { if(!error) alliance_fail(boost::asio::error::timed_out); });
        }
    }
\tbytes_read_ = bytes_transferred;''')
    text = once(text, '\t\tbytes_to_read_ = ntohl(data_size) + 4;', '''        const auto length=ntohl(data_size);
        if(alliance_socket_lookup(this) && (length == 0 || length > alliance_frame_limit(this))) {
            alliance_fail(boost::asio::error::message_size); return 0;
        }
\t\tbytes_to_read_ = static_cast<std::size_t>(length) + 4;''')
    text = once(text, '\tbytes_to_read_ = 0;\n\tif(last_error_', '''    if(auto state=alliance_socket_lookup(this)) {
        state->timer.cancel(); state->partial_frame=false;
        if(ec) { alliance_fail(ec); return; }
        { std::scoped_lock lock(last_error_mutex_); if(last_error_) return; }
    }
\tbytes_to_read_ = 0;
\tif(last_error_''')
    text = once(text, '\tconfig data = io::read_gz(is);\n\tif(!data.empty()) { DBG_NW << "Received:\\n" << data; }', '''    const bool strict=bool(alliance_socket_lookup(this));
    std::string plain;
    if(strict) plain=alliance_inflate_frame(is, alliance_frame_limit(this), alliance_plain_limit(this));
    config data = strict ? io::read(plain) : io::read_gz(is);
    if(!strict && !data.empty()) { DBG_NW << "Received:\\n" << data; }''')
    text = once(text, '\t\trecv_queue_.emplace(std::move(data));', '''        if(auto state=alliance_socket_lookup(this)) {
            if(recv_queue_.size()>=128 || state->queued_receive_bytes+plain.size()>64*1024*1024) {
                alliance_fail(boost::asio::error::no_buffer_space); return;
            }
            state->queued_receive_bytes+=plain.size();state->queued_receive_sizes.push_back(plain.size());
        }
\t\trecv_queue_.emplace(std::move(data));''')
    text = once(text, '\t\t\trecv_queue_.pop();', '''            if(auto state=alliance_socket_lookup(this)) {
                state->queued_receive_bytes-=state->queued_receive_sizes.front();state->queued_receive_sizes.pop_front();
            }
\t\t\trecv_queue_.pop();''')
    text = once(text, 'bool wesnothd_connection::wait_and_receive_data(config& data)\n{', 'bool wesnothd_connection::wait_and_receive_data(config& data)\n{\n    if(alliance_socket_lookup(this)) {\n        while(!receive_data(data)) { gui2::dialogs::loading_screen::spin(); std::this_thread::sleep_for(10ms); }\n        return true;\n    }')
    text += '''
void wesnothd_connection::set_alliance_observer()
{
    auto state=alliance_socket_lookup(this);
    if(!state || state->profile.port!=15002) throw std::runtime_error("Observer socket is unavailable.");
    state->read_only_observer.store(true);
}
void wesnothd_connection::alliance_fail(const error_code& code)
{
    { std::scoped_lock lock(last_error_mutex_); last_error_=code; }
    try { handshake_finished_.set_exception(std::make_exception_ptr(system_error(code))); } catch(const std::future_error&) {}
    recv_queue_lock_.notify_all();
    utils::visit([](auto&& socket) { error_code ignored; socket->lowest_layer().close(ignored); }, socket_);
    io_context_.stop();
}
'''
    return text


def write_overlay(source_dir, output_dir):
    source_dir, output_dir = Path(source_dir), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for suffix, transform in [('hpp', transform_header), ('cpp', transform_source)]:
        (output_dir / ('wesnothd_connection.' + suffix)).write_text(transform((source_dir / ('wesnothd_connection.' + suffix)).read_text()))
