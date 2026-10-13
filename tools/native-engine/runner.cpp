// Invoke the real game entry point. Device checks supply isolated user/cache
// directories and --version, so SDLActivity and game data are not required.
extern "C" int SDL_main(int argc, char** argv);
int main(int argc, char** argv) { return SDL_main(argc, argv); }
