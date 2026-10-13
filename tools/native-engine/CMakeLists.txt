cmake_minimum_required(VERSION 3.22)
project(wesnoth_android_engine LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(CMAKE_SKIP_RPATH ON)
set(CMAKE_SUPPRESS_REGENERATION ON)
set(REPO "" CACHE PATH "Verified client repository")
if(NOT ANDROID OR NOT ANDROID_ABI STREQUAL "x86_64" OR NOT ANDROID_PLATFORM STREQUAL "android-23" OR NOT EXISTS "${REPO}/engine/source-manifest.json")
 message(FATAL_ERROR "Use the verified Wesnoth x86_64/API23 offline builder")
endif()
set(CMAKE_OBJECT_PATH_MAX 240)
set(ENGINE "${REPO}/engine")
set(game_sources "${ENGINE}/src/wesnoth.cpp")
foreach(list_name wesnoth libwesnoth_core libwesnoth libwesnoth_sdl libwesnoth_widgets)
 file(STRINGS "${ENGINE}/source_lists/${list_name}" paths)
 foreach(path IN LISTS paths)
  list(APPEND game_sources "${ENGINE}/src/${path}")
 endforeach()
endforeach()
list(REMOVE_DUPLICATES game_sources)
file(STRINGS "${ENGINE}/source_lists/lua" paths)
foreach(path IN LISTS paths)
 list(APPEND lua_sources "${ENGINE}/src/${path}")
endforeach()
set_source_files_properties(${lua_sources} PROPERTIES LANGUAGE CXX COMPILE_OPTIONS "-include;${ENGINE}/src/wesnoth_lua_config.h;-Wno-parentheses-equality;-Wno-string-plus-int" COMPILE_DEFINITIONS LUA_USE_POSIX)
include(CheckIncludeFiles)
include(CheckFunctionExists)
check_include_files(sys/sendfile.h HAVE_SYS_SENDFILE_H)
check_function_exists(sendfile HAVE_SENDFILE)
configure_file(config.h.in "${CMAKE_BINARY_DIR}/config.h")
# Keep all widget registration objects in the shared library. Do not put them
# in a normal static archive, which would discard unreferenced registrars.
add_library(main SHARED ${game_sources} ${lua_sources})
target_include_directories(main PRIVATE "${ENGINE}/src" "${CMAKE_BINARY_DIR}")
# Upstream requires assertions even for Release builds.
target_compile_options(main PRIVATE -O2 -UNDEBUG -fexceptions -pthread)
target_compile_definitions(main PRIVATE HAVE_CONFIG_H HAS_RELATIVE_LOCALEDIR=1 BOOST_ALL_NO_LIB BOOST_ALL_NO_EMBEDDED_GDB_SCRIPTS BOOST_CHARCONV_NO_QUADMATH U_STATIC_IMPLEMENTATION)
target_include_directories(main SYSTEM PRIVATE "${REPO}/build/android-media/prefix/include")
target_include_directories(main SYSTEM PRIVATE "${REPO}/build/android-fonts/prefix/include")
target_include_directories(main SYSTEM PRIVATE "${REPO}/build/android-text-base/prefix/include")
target_include_directories(main SYSTEM PRIVATE "${REPO}/build/android-glib-deps/prefix/include")
target_include_directories(main SYSTEM PRIVATE "${REPO}/build/android-glib/prefix/include")
target_include_directories(main SYSTEM PRIVATE "${REPO}/build/android-text-render/prefix/include")
target_include_directories(main SYSTEM PRIVATE "${REPO}/build/android-text-layout/prefix/include")
target_include_directories(main SYSTEM PRIVATE "${REPO}/build/android-icu/prefix/include")
target_include_directories(main SYSTEM PRIVATE "${REPO}/build/android-boost/prefix/include")
target_include_directories(main SYSTEM PRIVATE "${REPO}/build/android-network/prefix/include")
target_include_directories(main SYSTEM PRIVATE "${REPO}/build/android-fonts/prefix/include/freetype2")
target_include_directories(main SYSTEM PRIVATE "${REPO}/build/android-fonts/prefix/include/harfbuzz")
target_include_directories(main SYSTEM PRIVATE "${REPO}/build/android-text-base/prefix/include/pixman-1")
target_include_directories(main SYSTEM PRIVATE "${REPO}/build/android-glib/prefix/include/glib-2.0")
target_include_directories(main SYSTEM PRIVATE "${REPO}/build/android-glib/prefix/lib/glib-2.0/include")
target_include_directories(main SYSTEM PRIVATE "${REPO}/build/android-text-render/prefix/include/cairo")
target_include_directories(main SYSTEM PRIVATE "${REPO}/build/android-text-layout/prefix/include/pango-1.0")
target_include_directories(main SYSTEM PRIVATE "${REPO}/build/android-text-layout/prefix/include/fribidi")
target_link_libraries(main PRIVATE
 "${REPO}/build/android-media/prefix/lib/libSDL3.so"
 "${REPO}/build/android-media/prefix/lib/libSDL3_image.so"
 "${REPO}/build/android-media/prefix/lib/libSDL3_mixer.so"
 -Wl,--start-group
 "${REPO}/build/android-media/prefix/lib/libcpufeatures-webp.a"
 "${REPO}/build/android-media/prefix/lib/libogg.a"
 "${REPO}/build/android-media/prefix/lib/libpng16.a"
 "${REPO}/build/android-media/prefix/lib/libsharpyuv.a"
 "${REPO}/build/android-media/prefix/lib/libvorbis.a"
 "${REPO}/build/android-media/prefix/lib/libvorbisenc.a"
 "${REPO}/build/android-media/prefix/lib/libvorbisfile.a"
 "${REPO}/build/android-media/prefix/lib/libwebp.a"
 "${REPO}/build/android-media/prefix/lib/libwebpdecoder.a"
 "${REPO}/build/android-media/prefix/lib/libwebpdemux.a"
 "${REPO}/build/android-media/prefix/lib/libwebpmux.a"
 "${REPO}/build/android-fonts/prefix/lib/libfreetype.a"
 "${REPO}/build/android-fonts/prefix/lib/libharfbuzz.a"
 "${REPO}/build/android-text-base/prefix/lib/libcharset.a"
 "${REPO}/build/android-text-base/prefix/lib/libiconv.a"
 "${REPO}/build/android-text-base/prefix/lib/libpixman-1.a"
 "${REPO}/build/android-glib-deps/prefix/lib/libffi.a"
 "${REPO}/build/android-glib-deps/prefix/lib/libintl.a"
 "${REPO}/build/android-glib-deps/prefix/lib/libpcre2-8.a"
 "${REPO}/build/android-glib/prefix/lib/libgio-2.0.a"
 "${REPO}/build/android-glib/prefix/lib/libglib-2.0.a"
 "${REPO}/build/android-glib/prefix/lib/libgmodule-2.0.a"
 "${REPO}/build/android-glib/prefix/lib/libgobject-2.0.a"
 "${REPO}/build/android-glib/prefix/lib/libgthread-2.0.a"
 "${REPO}/build/android-text-render/prefix/lib/libcairo.a"
 "${REPO}/build/android-text-render/prefix/lib/libexpat.a"
 "${REPO}/build/android-text-render/prefix/lib/libfontconfig.a"
 "${REPO}/build/android-text-layout/prefix/lib/libfribidi.a"
 "${REPO}/build/android-text-layout/prefix/lib/libpango-1.0.a"
 "${REPO}/build/android-text-layout/prefix/lib/libpangocairo-1.0.a"
 "${REPO}/build/android-text-layout/prefix/lib/libpangoft2-1.0.a"
 "${REPO}/build/android-icu/prefix/lib/libicudata.a"
 "${REPO}/build/android-icu/prefix/lib/libicui18n.a"
 "${REPO}/build/android-icu/prefix/lib/libicuuc.a"
 "${REPO}/build/android-boost/prefix/lib/libboost_atomic.a"
 "${REPO}/build/android-boost/prefix/lib/libboost_charconv.a"
 "${REPO}/build/android-boost/prefix/lib/libboost_chrono.a"
 "${REPO}/build/android-boost/prefix/lib/libboost_context.a"
 "${REPO}/build/android-boost/prefix/lib/libboost_coroutine.a"
 "${REPO}/build/android-boost/prefix/lib/libboost_filesystem.a"
 "${REPO}/build/android-boost/prefix/lib/libboost_iostreams.a"
 "${REPO}/build/android-boost/prefix/lib/libboost_locale.a"
 "${REPO}/build/android-boost/prefix/lib/libboost_process.a"
 "${REPO}/build/android-boost/prefix/lib/libboost_program_options.a"
 "${REPO}/build/android-boost/prefix/lib/libboost_random.a"
 "${REPO}/build/android-boost/prefix/lib/libboost_thread.a"
 "${REPO}/build/android-boost/prefix/lib/libbz2.a"
 "${REPO}/build/android-network/prefix/lib/libcrypto.a"
 "${REPO}/build/android-network/prefix/lib/libcurl.a"
 "${REPO}/build/android-network/prefix/lib/libssl.a"
 -Wl,--end-group android log GLESv1_CM GLESv2 z atomic dl m)
target_link_options(main PRIVATE -Wl,--no-undefined "-Wl,-Map,${CMAKE_BINARY_DIR}/main.map")
add_executable(wesnoth-engine-cli runner.cpp)
target_link_libraries(wesnoth-engine-cli PRIVATE main)
target_link_options(wesnoth-engine-cli PRIVATE -Wl,--no-undefined)
