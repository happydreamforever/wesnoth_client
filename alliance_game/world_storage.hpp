// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <filesystem>
#include <string>
namespace alliance_game {
std::string world_sha256(const std::string&);
std::string world_read_file(const std::filesystem::path&,std::size_t limit);
void world_write_file(const std::filesystem::path&,const std::string&,std::size_t limit);
void world_private_directory(const std::filesystem::path&);
bool world_uuid(const std::string&) noexcept;
}
