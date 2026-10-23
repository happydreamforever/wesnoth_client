// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
namespace alliance_game {
// Private-stdin, explicitly selected headless player used by the Windows
// one-LDPlayer test helper. It uses the ordinary authenticated lobby on 15000.
bool individual_companion_requested() noexcept;
int run_individual_companion() noexcept;
}
