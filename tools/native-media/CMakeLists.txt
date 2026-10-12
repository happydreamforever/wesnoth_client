cmake_minimum_required(VERSION 3.22)
project(wesnoth_android_media_check LANGUAGES CXX)

if(NOT ANDROID OR NOT ANDROID_ABI STREQUAL "x86_64" OR
   NOT ANDROID_PLATFORM_LEVEL STREQUAL "23" OR NOT ANDROID_STL STREQUAL "c++_shared")
    message(FATAL_ERROR "Use the selected Android x86_64/API23/shared-C++ toolchain.")
endif()
if(NOT IS_DIRECTORY "${MEDIA_PREFIX}/include")
    message(FATAL_ERROR "MEDIA_PREFIX must contain the verified native media dependency installation.")
endif()

find_package(SDL3 3.4.2 EXACT CONFIG REQUIRED)
find_package(SDL3_image 3.4.0 EXACT CONFIG REQUIRED)
find_package(SDL3_mixer 3.2.0 EXACT CONFIG REQUIRED)
find_package(Ogg CONFIG REQUIRED)
find_package(Vorbis CONFIG REQUIRED)
find_package(WebP CONFIG REQUIRED)

add_executable(wesnoth-media-check probe.cpp)
target_compile_features(wesnoth-media-check PRIVATE cxx_std_17)
target_compile_options(wesnoth-media-check PRIVATE -Wall -Wextra -Werror -fexceptions)
target_link_options(wesnoth-media-check PRIVATE -pie)
target_link_libraries(wesnoth-media-check PRIVATE
    SDL3::SDL3 SDL3_image::SDL3_image SDL3_mixer::SDL3_mixer
    Vorbis::vorbisfile Vorbis::vorbisenc WebP::webp)
set_target_properties(wesnoth-media-check PROPERTIES
    POSITION_INDEPENDENT_CODE ON
    SKIP_BUILD_RPATH ON
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
install(TARGETS wesnoth-media-check RUNTIME DESTINATION bin)
