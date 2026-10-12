cmake_minimum_required(VERSION 3.22)
project(wesnoth_android_fonts_check LANGUAGES CXX)

if(NOT ANDROID OR NOT ANDROID_ABI STREQUAL "x86_64" OR
   NOT ANDROID_PLATFORM_LEVEL STREQUAL "23" OR NOT ANDROID_STL STREQUAL "c++_shared")
    message(FATAL_ERROR "Use the selected Android x86_64/API23/shared-C++ toolchain.")
endif()
if(NOT IS_DIRECTORY "${FONTS_PREFIX}/include")
    message(FATAL_ERROR "FONTS_PREFIX must contain the verified font dependency installation.")
endif()

find_package(freetype 2.13.0 EXACT CONFIG REQUIRED)
find_package(Threads REQUIRED)
find_package(harfbuzz CONFIG REQUIRED)

add_executable(wesnoth-fonts-check probe.cpp)
target_compile_features(wesnoth-fonts-check PRIVATE cxx_std_17)
target_compile_options(wesnoth-fonts-check PRIVATE -Wall -Wextra -Werror -fexceptions)
target_link_options(wesnoth-fonts-check PRIVATE -pie)
target_link_libraries(wesnoth-fonts-check PRIVATE harfbuzz::harfbuzz Freetype::Freetype)
set_target_properties(wesnoth-fonts-check PROPERTIES
    POSITION_INDEPENDENT_CODE ON
    SKIP_BUILD_RPATH ON
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
install(TARGETS wesnoth-fonts-check RUNTIME DESTINATION bin)
