cmake_minimum_required(VERSION 3.22)
project(wesnoth_android_account_engine LANGUAGES C CXX)
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
set(ACCOUNT_STAGE "" CACHE PATH "Owned account engine stage")
if(NOT EXISTS "${ACCOUNT_STAGE}/objects.cmake" OR NOT EXISTS "${ACCOUNT_STAGE}/config.h")
 message(FATAL_ERROR "Use build_android_account_game.py with verified engine prerequisites")
endif()
include("${ACCOUNT_STAGE}/objects.cmake")
set_source_files_properties(${account_base_objects} PROPERTIES EXTERNAL_OBJECT TRUE GENERATED TRUE)
configure_file("${ACCOUNT_STAGE}/config.h" "${CMAKE_BINARY_DIR}/config.h" COPYONLY)
add_library(main SHARED ${account_base_objects}
 "${ACCOUNT_STAGE}/title_screen.cpp"
 "${ACCOUNT_STAGE}/wesnoth.cpp"
 "${ACCOUNT_STAGE}/game_launcher.cpp"
 "${ACCOUNT_STAGE}/playcampaign.cpp"
 "${ACCOUNT_STAGE}/campaign_selection.cpp"
 "${ACCOUNT_STAGE}/modal_dialog.cpp"
 "${ACCOUNT_STAGE}/gui_definition.cpp"
 "${ACCOUNT_STAGE}/preferences_dialog.cpp"
 "${ACCOUNT_STAGE}/wesnothd_connection.cpp"
 "${ACCOUNT_STAGE}/synced_commands.cpp"
 "${ACCOUNT_STAGE}/game_config_manager.cpp"
 "${ACCOUNT_STAGE}/attack.cpp"
 "${ACCOUNT_STAGE}/advancement.cpp"
 "${ACCOUNT_STAGE}/lua_unit.cpp"
 "${ACCOUNT_STAGE}/playmp_controller.cpp"
 "${ACCOUNT_STAGE}/multiplayer.cpp"
 "${ACCOUNT_STAGE}/lobby.cpp"
 "${REPO}/alliance_game/session_watch.cpp"
 "${REPO}/alliance_game/android_lifecycle.cpp"
 "${ACCOUNT_STAGE}/connect_engine.cpp"
 "${ACCOUNT_STAGE}/mp_staging.cpp"
 "${ACCOUNT_STAGE}/mp_join_game.cpp"
 "${ACCOUNT_STAGE}/mp_create_game.cpp"
 "${REPO}/alliance_game/hero_catalog.cpp"
 "${REPO}/alliance_game/hero_unit_config.cpp"
 "${REPO}/alliance_game/story_progression.cpp"
 "${REPO}/alliance_game/casual_session.cpp"
 "${REPO}/alliance_game/casual_sides.cpp"
 "${REPO}/alliance_game/individual_rooms.cpp"
 "${REPO}/alliance_game/battle_ui.cpp"
 "${REPO}/alliance_game/team_views.cpp"
 "${REPO}/alliance_game/individual_companion.cpp"
 "${REPO}/alliance_game/shared_launch.cpp"
 "${REPO}/alliance_game/shared_scenario.cpp"
 "${REPO}/alliance_game/shared_result.cpp"
 "${REPO}/alliance_game/replay_launch.cpp"
 "${REPO}/alliance_game/companion.cpp"
 "${REPO}/alliance_game/world_launch.cpp"
 "${REPO}/alliance_game/world_scenario.cpp"
 "${REPO}/alliance_game/world_result.cpp"
 "${REPO}/alliance_game/world_storage.cpp"
 "${REPO}/alliance_game/world_worker.cpp"

 "${REPO}/alliance_game/shared_controls.cpp"
 "${REPO}/alliance_game/progression_plan.cpp"
 "${REPO}/alliance_game/progression_scenario.cpp"
 "${REPO}/alliance_game/progression_result.cpp"
 "${REPO}/alliance_game/progression_launch.cpp"
 "${REPO}/alliance_game/battle_launch.cpp"
 "${REPO}/alliance_game/practice_plan.cpp"
 "${REPO}/alliance_game/practice_scenario.cpp"
 "${REPO}/alliance_game/account_ui.cpp"
 "${REPO}/alliance_native/account_protocol.cpp"
 "${REPO}/alliance_native/account_client.cpp"
 "${REPO}/alliance_native/transport.cpp")
target_include_directories(main PRIVATE "${ACCOUNT_STAGE}" "${ENGINE}/src" "${CMAKE_BINARY_DIR}" "${REPO}")
# Upstream requires assertions even for Release builds.
target_compile_options(main PRIVATE -O2 -UNDEBUG -fexceptions -pthread)
target_compile_definitions(main PRIVATE BOOST_ERROR_CODE_HEADER_ONLY HAVE_CONFIG_H HAS_RELATIVE_LOCALEDIR=1 BOOST_ALL_NO_LIB BOOST_ALL_NO_EMBEDDED_GDB_SCRIPTS BOOST_CHARCONV_NO_QUADMATH U_STATIC_IMPLEMENTATION)
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
