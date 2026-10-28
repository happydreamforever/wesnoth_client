// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/world_storage.hpp"
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <fstream>
#include <stdexcept>
#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
#else
#include <fcntl.h>
#include <unistd.h>
#endif
namespace alliance_game {
namespace fs=std::filesystem;
namespace {
void ordinary(const fs::path& p){for(auto at=p;!at.empty();at=at.parent_path()){if(fs::is_symlink(at))throw std::runtime_error("Linked world storage is unavailable.");
#ifdef _WIN32
const auto attributes=GetFileAttributesW(at.c_str());if(attributes!=INVALID_FILE_ATTRIBUTES&&(attributes&FILE_ATTRIBUTE_REPARSE_POINT))throw std::runtime_error("Reparsed world storage is unavailable.");
#endif
if(at==at.parent_path())break;}}
}
bool world_uuid(const std::string& s)noexcept{if(s.size()!=36)return false;for(std::size_t i=0;i<s.size();++i)if(i==8||i==13||i==18||i==23){if(s[i]!='-')return false;}else if(!((s[i]>='0'&&s[i]<='9')||(s[i]>='a'&&s[i]<='f')))return false;return true;}
std::string world_sha256(const std::string& value){unsigned char bytes[32];unsigned size=0;if(EVP_Digest(value.data(),value.size(),bytes,&size,EVP_sha256(),nullptr)!=1||size!=32)throw std::runtime_error("World hash unavailable.");static const char hex[]="0123456789abcdef";std::string result;for(auto x:bytes){result+=hex[x>>4];result+=hex[x&15];}return result;}
void world_private_directory(const fs::path& path){ordinary(path);if(!fs::exists(path))fs::create_directories(path);if(!fs::is_directory(path))throw std::runtime_error("World storage is not a directory.");
#ifndef _WIN32
fs::permissions(path,fs::perms::owner_all,fs::perm_options::replace);
#endif
}
std::string world_read_file(const fs::path& path,std::size_t limit){ordinary(path);if(!fs::is_regular_file(path)||fs::hard_link_count(path)!=1||fs::file_size(path)>limit)throw std::runtime_error("World file has changed.");std::ifstream in(path,std::ios::binary);std::string bytes(limit+1,'\0');in.read(bytes.data(),bytes.size());if(in.bad()||static_cast<std::size_t>(in.gcount())>limit)throw std::runtime_error("World file exceeds its limit.");bytes.resize(in.gcount());return bytes;}
void world_write_file(const fs::path& path,const std::string& bytes,std::size_t limit){ordinary(path);if(bytes.size()>limit)throw std::runtime_error("World record exceeds its limit.");if(fs::exists(path)&&(!fs::is_regular_file(path)||fs::hard_link_count(path)!=1))throw std::runtime_error("World record changed.");unsigned char nonce[16];if(RAND_bytes(nonce,sizeof(nonce))!=1)throw std::runtime_error("World storage randomness unavailable.");const auto temp=fs::path(path.string()+".partial-"+world_sha256(std::string(reinterpret_cast<char*>(nonce),sizeof(nonce))).substr(0,32));
#ifdef _WIN32
HANDLE handle=CreateFileW(temp.c_str(),GENERIC_WRITE,0,nullptr,CREATE_NEW,FILE_ATTRIBUTE_NORMAL|FILE_FLAG_WRITE_THROUGH,nullptr);if(handle==INVALID_HANDLE_VALUE)throw std::runtime_error("World record cannot be created.");try{std::size_t pos=0;while(pos<bytes.size()){DWORD written=0;const auto n=static_cast<DWORD>(std::min<std::size_t>(bytes.size()-pos,1048576));if(!WriteFile(handle,bytes.data()+pos,n,&written,nullptr)||written==0)throw std::runtime_error("World record cannot be written.");pos+=written;}if(!FlushFileBuffers(handle))throw std::runtime_error("World record cannot be synchronized.");CloseHandle(handle);handle=INVALID_HANDLE_VALUE;ordinary(path);if(!MoveFileExW(temp.c_str(),path.c_str(),MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH))throw std::runtime_error("World record cannot be committed.");}catch(...){if(handle!=INVALID_HANDLE_VALUE)CloseHandle(handle);throw;}
#else
int fd=::open(temp.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW,0600);if(fd<0)throw std::runtime_error("World record cannot be created.");try{std::size_t pos=0;while(pos<bytes.size()){const auto n=::write(fd,bytes.data()+pos,bytes.size()-pos);if(n<=0)throw std::runtime_error("World record cannot be written.");pos+=n;}if(::fsync(fd)!=0)throw std::runtime_error("World record cannot be synchronized.");::close(fd);fd=-1;ordinary(path);fs::rename(temp,path);const int parent=::open(path.parent_path().c_str(),O_RDONLY|O_DIRECTORY);if(parent<0)throw std::runtime_error("World directory cannot be synchronized.");const int result=::fsync(parent);::close(parent);if(result!=0)throw std::runtime_error("World directory cannot be synchronized.");}catch(...){if(fd>=0)::close(fd);throw;}
#endif
}
}
