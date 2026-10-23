// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include "events.hpp"
#include <memory>
#include <string>

namespace alliance_game {

// Foreground server/session failures record their old identity before clearing
// the token. The next unblocked pump must authenticate before it returns.
void request_session_reauthentication(const std::string& account_id,
                                      const std::string& generation);
// Explicit logout and a completed fresh authentication discard older notices.
void clear_session_reauthentication() noexcept;

// Main-thread scope for foreground account requests. A background read may
// finish meanwhile, but it cannot open authentication inside that request.
class AccountNetworkActivity final {
public:
    AccountNetworkActivity() noexcept;
    ~AccountNetworkActivity();
    AccountNetworkActivity(const AccountNetworkActivity&) = delete;
    AccountNetworkActivity& operator=(const AccountNetworkActivity&) = delete;
};

// GUI transitions must wait until foreground authentication/request work and
// any latched recovery have completed. This performs no I/O.
bool account_session_interaction_active() noexcept;

// Own one for the interactive engine lifetime, outside individual dialogs.
// The worker only reads the saved LAN account endpoint. WAN is never probed.
class SessionWatch final : public events::pump_monitor {
public:
    SessionWatch();
    ~SessionWatch() override;
    void process() override;
    SessionWatch(const SessionWatch&) = delete;
    SessionWatch& operator=(const SessionWatch&) = delete;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
