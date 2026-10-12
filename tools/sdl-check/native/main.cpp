// A visible Android SDL/JNI/rendering/input checkpoint; this is not the game.
#define SDL_MAIN_USE_CALLBACKS 1
#include <SDL3/SDL.h>
#include <SDL3/SDL_main.h>

#include <atomic>
#include <mutex>
#include <string>
#include <vector>

#if !defined(__ANDROID__) || !defined(__x86_64__)
#error "This checkpoint targets Android x86_64."
#endif
static_assert(__ANDROID_API__ == 23, "The selected minimum API is 23.");
static_assert(SDL_MAJOR_VERSION == 3 && SDL_MINOR_VERSION == 4 && SDL_MICRO_VERSION == 2,
              "The SDL Java and native versions must both be 3.4.2.");

namespace {
constexpr int width = 960;
constexpr int height = 540;
SDL_Window* window = nullptr;
SDL_Renderer* renderer = nullptr;
std::string run_id = "manual";
Uint64 frames = 0;
unsigned taps = 0;
unsigned presented_taps = 0;
bool ready = false;
std::atomic<bool> background{false};
std::mutex input_mutex;
std::vector<SDL_Event> pending_inputs;

SDL_AppResult fail(const char* stage)
{
    SDL_LogError(SDL_LOG_CATEGORY_APPLICATION,
                 "WESNOTH_SDL_CHECK FAIL run_id=%s stage=%s error=%s",
                 run_id.c_str(), stage, SDL_GetError());
    return SDL_APP_FAILURE;
}

bool valid_run_id(const std::string& value)
{
    if(value == "manual") {
        return true;
    }
    return value.size() == 32 && value.find_first_not_of("0123456789abcdef") == std::string::npos;
}

bool color(Uint8 r, Uint8 g, Uint8 b)
{
    return SDL_SetRenderDrawColor(renderer, r, g, b, SDL_ALPHA_OPAQUE);
}

bool text(float x, float y, float scale, const std::string& value)
{
    // Restore unit scale before processing events so coordinate conversion uses
    // only the logical canvas/letterbox mapping, never this text magnification.
    if(!SDL_SetRenderScale(renderer, scale, scale)) {
        return false;
    }
    const bool drawn = SDL_RenderDebugText(renderer, x / scale, y / scale, value.c_str());
    const bool reset = SDL_SetRenderScale(renderer, 1.0f, 1.0f);
    return drawn && reset;
}

bool draw()
{
    const SDL_FRect panel{48.0f, 130.0f, 864.0f, 278.0f};
    const SDL_FRect target{288.0f, 198.0f, 384.0f, 124.0f};
    const SDL_FRect track{48.0f, 444.0f, 864.0f, 5.0f};
    const SDL_FRect progress{48.0f, 444.0f, static_cast<float>((frames % 240) + 1) * 3.6f, 5.0f};
    const bool alternate = taps % 2 != 0;
    return color(13, 22, 30) && SDL_RenderClear(renderer)
        && color(23, 40, 48) && SDL_RenderFillRect(renderer, &panel)
        && color(220, 235, 235) && text(48.0f, 40.0f, 3.0f, "Wesnoth SDL Check")
        && color(146, 172, 179) && text(48.0f, 88.0f, 2.0f, "Graphics and input")
        && color(alternate ? 187 : 35, alternate ? 135 : 142, alternate ? 56 : 139)
        && SDL_RenderFillRect(renderer, &target)
        && color(255, 255, 243) && text(372.0f, 240.0f, 3.0f, "TAP HERE")
        && color(198, 218, 220) && text(80.0f, 358.0f, 2.0f, "Tap count: " + std::to_string(taps))
        && text(560.0f, 358.0f, 2.0f, "Frames: " + std::to_string(frames + 1))
        && color(44, 65, 72) && SDL_RenderFillRect(renderer, &track)
        && color(77, 186, 166) && SDL_RenderFillRect(renderer, &progress)
        && color(146, 172, 179) && text(48.0f, 478.0f, 2.0f, "Tap to change the color. Back closes this check.");
}
} // namespace

SDL_AppResult SDL_AppInit(void**, int argc, char** argv)
{
    for(int i = 1; i < argc; ++i) {
        if(std::string(argv[i]) == "--run-id") {
            if(i + 1 == argc || !valid_run_id(argv[i + 1])) {
                SDL_SetError("Invalid run identifier");
                return fail("arguments");
            }
            run_id = argv[++i];
        }
    }
    SDL_SetLogPriority(SDL_LOG_CATEGORY_APPLICATION, SDL_LOG_PRIORITY_INFO);
    SDL_SetAppMetadata("Wesnoth SDL Check", "1.19.26", "org.wesnoth.offlinecheck");
    SDL_SetHint(SDL_HINT_ORIENTATIONS, "LandscapeLeft LandscapeRight");
    SDL_SetHint(SDL_HINT_TOUCH_MOUSE_EVENTS, "0");
    SDL_SetHint(SDL_HINT_MOUSE_TOUCH_EVENTS, "0");
    SDL_SetHint(SDL_HINT_MAIN_CALLBACK_RATE, "60");
    if(SDL_GetVersion() != SDL_VERSION) {
        SDL_SetError("Loaded SDL library does not match the 3.4.2 headers");
        return fail("sdl-version");
    }
    if(!SDL_Init(SDL_INIT_VIDEO)) {
        return fail("video-init");
    }
    window = SDL_CreateWindow("Wesnoth SDL Check", width, height, SDL_WINDOW_FULLSCREEN);
    if(!window) {
        return fail("window");
    }
    // OpenGL ES 2 is available on the selected Android target and avoids
    // accidentally passing this checkpoint through a software renderer.
    renderer = SDL_CreateRenderer(window, "opengles2");
    if(!renderer) {
        return fail("renderer");
    }
    if(!SDL_SetRenderLogicalPresentation(renderer, width, height, SDL_LOGICAL_PRESENTATION_LETTERBOX)) {
        return fail("logical-canvas");
    }
    return SDL_APP_CONTINUE;
}

SDL_AppResult SDL_AppEvent(void*, SDL_Event* event)
{
    if(event->type == SDL_EVENT_QUIT || event->type == SDL_EVENT_TERMINATING) {
        return SDL_APP_SUCCESS;
    }
    if(event->type == SDL_EVENT_KEY_DOWN &&
       (event->key.key == SDLK_ESCAPE || event->key.key == SDLK_AC_BACK)) {
        return SDL_APP_SUCCESS;
    }
    if(event->type == SDL_EVENT_WILL_ENTER_BACKGROUND) {
        background = true;
    } else if(event->type == SDL_EVENT_DID_ENTER_FOREGROUND) {
        background = false;
    }
    const bool finger = event->type == SDL_EVENT_FINGER_DOWN;
    const bool mouse = event->type == SDL_EVENT_MOUSE_BUTTON_DOWN &&
        event->button.button == SDL_BUTTON_LEFT && event->button.which != SDL_TOUCH_MOUSEID;
    if(finger || mouse) {
        // SDL may dispatch callbacks from a lifecycle event's posting thread.
        // Keep every renderer/window operation on SDL_AppIterate's main thread.
        const std::lock_guard<std::mutex> lock(input_mutex);
        pending_inputs.push_back(*event);
    }
    return SDL_APP_CONTINUE;
}

SDL_AppResult SDL_AppIterate(void*)
{
    std::vector<SDL_Event> inputs;
    {
        const std::lock_guard<std::mutex> lock(input_mutex);
        inputs.swap(pending_inputs);
    }
    if(background) {
        return SDL_APP_CONTINUE;
    }
    if(ready) {
        for(auto& event : inputs) {
            if(!SDL_ConvertEventToRenderCoordinates(renderer, &event)) {
                return fail("input-coordinates");
            }
            const bool finger = event.type == SDL_EVENT_FINGER_DOWN;
            const float x = finger ? event.tfinger.x : event.button.x;
            const float y = finger ? event.tfinger.y : event.button.y;
            if(x >= 0.0f && x < width && y >= 0.0f && y < height) {
                ++taps;
            }
        }
    }
    if(!draw() || !SDL_RenderPresent(renderer)) {
        return fail("render-present");
    }
    ++frames;
    if(!ready) {
        int pixels_w = 0;
        int pixels_h = 0;
        if(!SDL_GetWindowSizeInPixels(window, &pixels_w, &pixels_h) || pixels_w <= 0 || pixels_h <= 0) {
            return fail("window-size");
        }
        SDL_Log("WESNOTH_SDL_CHECK READY run_id=%s width=%d height=%d sdl=3.4.2 renderer=%s abi=x86_64 min_api=23",
                run_id.c_str(), pixels_w, pixels_h, SDL_GetRendererName(renderer));
        ready = true;
    }
    if(taps != presented_taps) {
        // Emit only once the frame containing the changed counter/color has
        // actually been submitted. A queued input alone does not pass the test.
        SDL_Log("WESNOTH_SDL_CHECK INPUT run_id=%s count=%u", run_id.c_str(), taps);
        presented_taps = taps;
    }
    return SDL_APP_CONTINUE;
}

void SDL_AppQuit(void*, SDL_AppResult)
{
    SDL_DestroyRenderer(renderer);
    SDL_DestroyWindow(window);
    SDL_Quit();
}
