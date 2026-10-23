// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/android_lifecycle.hpp"
#ifdef __ANDROID__
#include <SDL3/SDL_system.h>
#include <SDL3/SDL_keyboard.h>
#include <jni.h>
#endif

namespace alliance_game {
namespace {
bool activity_call(const char* method) noexcept
{
#ifdef __ANDROID__
    auto* env = static_cast<JNIEnv*>(SDL_GetAndroidJNIEnv());
    if(!env) return false;
    jobject activity = static_cast<jobject>(SDL_GetAndroidActivity());
    if(!activity) return false;
    jclass type = env->GetObjectClass(activity);
    bool success = false;
    if(type) {
        jmethodID callback = env->GetMethodID(type, method, "()V");
        if(callback) { env->CallVoidMethod(activity, callback); success = !env->ExceptionCheck(); }
        if(env->ExceptionCheck()) env->ExceptionClear();
        env->DeleteLocalRef(type);
    }
    if(env->ExceptionCheck()) env->ExceptionClear();
    env->DeleteLocalRef(activity);
    return success;
#else
    (void)method;
    return false;
#endif
}
}
void finish_game_task() noexcept { activity_call("finishGameTask"); }
bool reset_android_keyboard_modifiers() noexcept
{
#ifdef __ANDROID__
    SDL_ResetKeyboard();
    SDL_SetModState(SDL_KMOD_NONE);
#endif
    return activity_call("resetKeyboardModifiers");
}
}
