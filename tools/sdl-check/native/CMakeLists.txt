cmake_minimum_required(VERSION 3.22)
project(wesnoth_sdl_check VERSION 1.19.26 LANGUAGES C CXX)

if(NOT ANDROID OR NOT ANDROID_ABI STREQUAL "x86_64" OR
   NOT ANDROID_PLATFORM_LEVEL STREQUAL "23" OR NOT ANDROID_STL STREQUAL "c++_shared")
    message(FATAL_ERROR "Use the selected NDK toolchain: x86_64, android-23, c++_shared.")
endif()
if(NOT EXISTS "${SDL3_SOURCE_DIR}/include/SDL3/SDL.h")
    message(FATAL_ERROR "SDL3_SOURCE_DIR must contain the verified SDL3-3.4.2 archive sources.")
endif()

# Source extraction and integrity checks are performed by the offline builder.
# Java is compiled by Gradle from the same archive, never by CMake's JAR target.
set(SDL_SHARED ON CACHE BOOL "" FORCE)
set(SDL_STATIC OFF CACHE BOOL "" FORCE)
set(SDL_TEST_LIBRARY OFF CACHE BOOL "" FORCE)
set(SDL_TESTS OFF CACHE BOOL "" FORCE)
set(SDL_EXAMPLES OFF CACHE BOOL "" FORCE)
set(SDL_ANDROID_JAR OFF CACHE BOOL "" FORCE)
set(SDL_INSTALL OFF CACHE BOOL "" FORCE)
set(SDL_INSTALL_DOCS OFF CACHE BOOL "" FORCE)
set(SDL_HIDAPI OFF CACHE BOOL "Match the upstream Wesnoth Android configuration" FORCE)
set(SDL_CAMERA OFF CACHE BOOL "This checkpoint does not use the camera" FORCE)
set(SDL_VULKAN OFF CACHE BOOL "Exercise the OpenGL ES 2 renderer on LDPlayer" FORCE)
add_subdirectory("${SDL3_SOURCE_DIR}" sdl)

add_library(main SHARED main.cpp)
target_compile_features(main PRIVATE cxx_std_17)
target_compile_options(main PRIVATE -Wall -Wextra -Werror)
target_link_libraries(main PRIVATE SDL3::SDL3)
set_target_properties(main SDL3-shared PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/lib")
message(STATUS "SDL checkpoint: shared SDL3/main libraries for Android x86_64 / API23")
