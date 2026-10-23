// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include "events.hpp"
#include <memory>

namespace alliance_game {

// Main-thread scope for foreground account requests. A background read may
// finish meanwhile, but it cannot open authentication inside that request.
class AccountNetworkActivity final {
public:
    AccountNetworkActivity() noexcept;
    ~AccountNetworkActivity();
    AccountNetworkActivity(const AccountNetworkActivity&) = delete;
    AccountNetworkActivity& operator=(const AccountNetworkActivity&) = delete;
};

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
