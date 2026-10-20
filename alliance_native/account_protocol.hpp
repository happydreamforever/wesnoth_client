// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <boost/json.hpp>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace alliance::account {
constexpr std::size_t max_json_bytes = 32 * 1024;
// One gzip member, bounded expansion, strict UTF-8 JSON, depth <= 8.
std::vector<std::uint8_t> encode_message(const boost::json::object&);
boost::json::object decode_message(const std::vector<std::uint8_t>&);
boost::json::object parse_document(const std::string&);
bool username_valid(const std::string&);
bool token_valid(const std::string&);
bool starter_valid(const std::string&);
bool password_valid(const std::string&, bool registration);
}
