// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
namespace alliance_game {
// Remove the Android game task before the native process exits. No-op on desktop.
void finish_game_task() noexcept;
// Explicit keyboard recovery affects future key events, never existing text.
bool reset_android_keyboard_modifiers() noexcept;
}
