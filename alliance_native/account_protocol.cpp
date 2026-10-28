// SPDX-License-Identifier: GPL-2.0-or-later
#include "account_protocol.hpp"
#include "transport.hpp"
#include <boost/json/src.hpp>
#include <zlib.h>
#include <algorithm>
#include <array>
#include <stdexcept>

namespace alliance::account {
namespace {
[[noreturn]] void invalid() { throw std::runtime_error("Invalid Alliance account message."); }
bool ascii_name(char c) {
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
        || (c >= '0' && c <= '9') || c == '_';
}
}

boost::json::object parse_document(const std::string& text) {
    if(text.empty() || text.size() > max_json_bytes) invalid();
    boost::json::parse_options options;
    options.max_depth = 12;
    boost::system::error_code ec;
    auto result = boost::json::parse(text, ec, {}, options);
    if(ec || !result.is_object()) invalid();
    // Every colon outside a JSON string introduces exactly one object member.
    // Compare that lexical count with the parsed tree: duplicate keys discarded
    // by Boost.JSON would reduce the latter. Works for the pretty public profile
    // as well as nested account messages, without imposing a whitespace style.
    std::size_t lexical_members = 0;
    bool quoted = false, escaped = false;
    for(char c : text) {
        if(quoted) {
            if(escaped) escaped = false;
            else if(c == '\\') escaped = true;
            else if(c == '"') quoted = false;
        } else if(c == '"') quoted = true;
        else if(c == ':') ++lexical_members;
    }
    const auto members = [](const auto& self, const boost::json::value& value) -> std::size_t {
        std::size_t count = 0;
        if(value.is_object()) {
            count = value.as_object().size();
            for(const auto& member : value.as_object()) count += self(self, member.value());
        } else if(value.is_array()) {
            for(const auto& item : value.as_array()) count += self(self, item);
        }
        return count;
    };
    if(lexical_members != members(members, result)) invalid();
    return std::move(result.as_object());
}

std::vector<std::uint8_t> encode_message(const boost::json::object& object) {
    const std::string text = boost::json::serialize(object);
    if(text.empty() || text.size() > max_json_bytes) invalid();
    z_stream stream{};
    if(deflateInit2(&stream, Z_DEFAULT_COMPRESSION, Z_DEFLATED, MAX_WBITS + 16, 8,
                    Z_DEFAULT_STRATEGY) != Z_OK) invalid();
    struct End { z_stream* stream; ~End() { deflateEnd(stream); } } end{&stream};
    std::vector<std::uint8_t> output(max_frame_bytes);
    stream.next_in = reinterpret_cast<Bytef*>(const_cast<char*>(text.data()));
    stream.avail_in = static_cast<uInt>(text.size());
    stream.next_out = output.data();
    stream.avail_out = static_cast<uInt>(output.size());
    if(deflate(&stream, Z_FINISH) != Z_STREAM_END || stream.avail_in) invalid();
    output.resize(stream.total_out);
    return output;
}

boost::json::object decode_message(const std::vector<std::uint8_t>& frame) {
    if(frame.empty() || frame.size() > max_frame_bytes) invalid();
    z_stream stream{};
    if(inflateInit2(&stream, MAX_WBITS + 16) != Z_OK) invalid();
    struct End { z_stream* stream; ~End() { inflateEnd(stream); } } end{&stream};
    std::array<char, max_json_bytes + 1> text{};
    stream.next_in = const_cast<Bytef*>(frame.data());
    stream.avail_in = static_cast<uInt>(frame.size());
    stream.next_out = reinterpret_cast<Bytef*>(text.data());
    stream.avail_out = static_cast<uInt>(text.size());
    if(inflate(&stream, Z_FINISH) != Z_STREAM_END || stream.avail_in
       || stream.total_out > max_json_bytes) invalid();
    const std::string raw(text.data(), stream.total_out);
    auto parsed = parse_document(raw);
    // Our private protocol uses canonical serializer output. This also rejects
    // duplicate keys instead of permitting ambiguous first/last-key semantics.
    if(boost::json::serialize(parsed) != raw) invalid();
    return parsed;
}

bool username_valid(const std::string& text) {
    return text.size() >= 3 && text.size() <= 24
        && std::all_of(text.begin(), text.end(), ascii_name);
}
bool token_valid(const std::string& text) {
    return text.size() == 43 && std::all_of(text.begin(), text.end(),
        [](char c) { return ascii_name(c) || c == '-'; });
}
bool starter_valid(const std::string& text) {
    return text == "spearman" || text == "elvish-fighter" || text == "orcish-grunt"
        || text == "dwarvish-fighter" || text == "drake-fighter" || text == "skeleton";
}
bool password_valid(const std::string& text, bool registration) {
    if(text.empty() || text.size() > 512 || text.find('\0') != std::string::npos) return false;
    // The JSON parser validates UTF-8. Count Unicode scalars to match Python's
    // 12..128 character rule, not UTF-8 byte count.
    const auto count = std::count_if(text.begin(), text.end(),
        [](unsigned char c) { return (c & 0xc0) != 0x80; });
    if(count < (registration ? 12 : 1) || count > 128) return false;
    try {
        boost::json::object object{{"password", text}};
        parse_document(boost::json::serialize(object));
        return true;
    } catch(...) { return false; }
}
}
