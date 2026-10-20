# Included only when the standalone transport runtime check is requested.
# No dependency download, engine build, private runtime profile or game data.
add_executable(android-alliance-check "${CMAKE_CURRENT_LIST_DIR}/android_check.cpp")
target_compile_options(android-alliance-check PRIVATE -Wall -Wextra -Werror)
target_link_libraries(android-alliance-check PRIVATE alliance_transport)
set_target_properties(android-alliance-check PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}")
install(TARGETS android-alliance-check RUNTIME DESTINATION bin)
