// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "alliance_native/account_client.hpp"

namespace alliance_game {
inline constexpr int practice_title_result = 18101;
void queue_practice(const alliance::account::Battle&, const alliance::account::Profile&);
bool practice_pending() noexcept;
// Called through the ordinary game launcher, after all title/account dialogs
// have unwound. Stock save/load, quit and engine-error handling remain active.
void run_pending_practice();
}
