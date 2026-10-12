cmake_minimum_required(VERSION 3.22)
project(wesnoth_android_native_bootstrap VERSION 1.19.26 LANGUAGES C CXX)

if(NOT ANDROID OR NOT CMAKE_SYSTEM_NAME STREQUAL "Android")
    message(FATAL_ERROR "This bootstrap must use the Android NDK CMake toolchain.")
endif()
# NDK r26d's default legacy toolchain sets CMAKE_SYSTEM_VERSION to 1;
# ANDROID_PLATFORM_LEVEL is the effective API used in its compiler target.
if(NOT ANDROID_ABI STREQUAL "x86_64" OR NOT ANDROID_PLATFORM_LEVEL STREQUAL "23")
    message(FATAL_ERROR "The verified first target is Android x86_64 with minimum API 23.")
endif()
if(NOT ANDROID_STL STREQUAL "c++_shared")
    message(FATAL_ERROR "Use ANDROID_STL=c++_shared to test the app's intended C++ runtime.")
endif()
if(NOT IS_DIRECTORY "${BZIP2_SOURCE_DIR}")
    message(FATAL_ERROR "BZIP2_SOURCE_DIR must point to verified bzip2-1.0.8 source.")
endif()

# These are the seven library sources from bzip2 1.0.8's upstream Makefile.
# The Python orchestrator verifies/extracts the pinned archive before CMake runs.
set(BZIP2_SOURCES blocksort.c huffman.c crctable.c randtable.c compress.c decompress.c bzlib.c)
list(TRANSFORM BZIP2_SOURCES PREPEND "${BZIP2_SOURCE_DIR}/")
foreach(source IN LISTS BZIP2_SOURCES)
    if(NOT EXISTS "${source}")
        message(FATAL_ERROR "Missing bzip2 library source: ${source}")
    endif()
endforeach()

add_library(bz2 STATIC ${BZIP2_SOURCES})
target_include_directories(bz2 PUBLIC "${BZIP2_SOURCE_DIR}")
target_compile_definitions(bz2 PRIVATE _FILE_OFFSET_BITS=64)
set_target_properties(bz2 PROPERTIES
    POSITION_INDEPENDENT_CODE ON
    ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/lib"
    OUTPUT_NAME bz2)

# Android provides libz; use longstanding public APIs available at API 23.
# Its runtime version belongs to the device and may differ from NDK headers.
find_library(ANDROID_ZLIB z REQUIRED)
add_executable(wesnoth-native-check probe.cpp)
target_compile_features(wesnoth-native-check PRIVATE cxx_std_17)
target_compile_options(wesnoth-native-check PRIVATE -fexceptions -frtti)
target_link_options(wesnoth-native-check PRIVATE -pie)
target_link_libraries(wesnoth-native-check PRIVATE bz2 "${ANDROID_ZLIB}")
set_target_properties(wesnoth-native-check PROPERTIES
    POSITION_INDEPENDENT_CODE ON
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")

message(STATUS "Native bootstrap: Android ${ANDROID_ABI}, min API ${ANDROID_PLATFORM_LEVEL}, ${ANDROID_STL}")
message(STATUS "Outputs: bin/wesnoth-native-check and lib/libbz2.a; this is not a Wesnoth APK.")
