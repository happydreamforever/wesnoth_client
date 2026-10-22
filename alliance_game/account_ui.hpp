// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

namespace alliance_game {

// Called on the game's UI thread from the owned Alliance title-screen overlay.
// Uses the existing game widgets and delegates all network work to a worker.
// A confirmed local practice request unwinds this entire modal flow before the
// title caller checks practice_pending() and hands off to the engine loop.
void show_account_dialog();

}
