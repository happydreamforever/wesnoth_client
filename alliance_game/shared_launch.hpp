// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "alliance_native/transport.hpp"
#include <optional>
#include <string>
namespace alliance_game {
inline constexpr int shared_title_result=18103;
struct SharedAdmission {
    alliance::TrustedProfile profile;
    std::string run_id,account_id,token;
    bool upload_only=false;
    bool download_only=false;
    std::string captain_action; // Explicit headless test policy, never ordinary GUI admission.
    int after_turn=0;
    bool computer_controlled=false; // Explicit authenticated companion; ordinary UI leaves this false.
};
void queue_shared(SharedAdmission);
bool shared_pending() noexcept;
std::optional<SharedAdmission> take_pending_shared();
void run_pending_shared();
}
