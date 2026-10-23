// SPDX-License-Identifier: GPL-2.0-or-later
#include "session_watch.hpp"
#include "alliance_game/account_ui.hpp"
#include "alliance_game/android_lifecycle.hpp"

#include <atomic>
#include <chrono>
#include <cstdlib>
#include <optional>
#include <string>
#include <thread>
#include <utility>

namespace alliance_game {
namespace {
using Clock = std::chrono::steady_clock;
constexpr auto interval = std::chrono::seconds(3);
// All reads/writes are on the engine thread; workers never touch this state.
unsigned foreground_requests = 0;
struct LostIdentity { std::string account_id, generation; };
std::optional<LostIdentity> pending_reauthentication;

void erase_secret(std::string& value) noexcept
{
    volatile char* bytes = value.empty() ? nullptr : &value[0];
    for(std::size_t i = 0; i < value.size(); ++i) bytes[i] = 0;
    value.clear();
}

struct Probe final {
    explicit Probe(PlayerSession&& session)
        : snapshot(std::move(session)), worker([this]() {
            alliance::account::Request input;
            struct SecretGuard {
                alliance::account::Request& request;
                ~SecretGuard() { erase_secret(request.token); erase_secret(request.password); }
            } secrets{input};
            try {
                input.operation = "me";
                input.token = snapshot.token;
                reply = alliance::account::perform_request(snapshot.trusted, input, cancelled);
            } catch(...) {
                // No exception text, identity, endpoint or credential is logged.
                reply.ok = false;
            }
            done.store(true, std::memory_order_release);
        })
    {}

    ~Probe()
    {
        cancelled.cancel();
        if(worker.joinable()) worker.join();
        erase_secret(reply.token);
        erase_secret(reply.ticket);
        erase_secret(reply.selection_ticket);
    }
    void join() { if(worker.joinable()) worker.join(); }

    PlayerSession snapshot;
    alliance::CancellationToken cancelled;
    alliance::account::Reply reply;
    std::atomic<bool> done{false};
    // All data used by the worker must be initialized before it is started.
    std::thread worker;
};

[[noreturn]] void finish_authentication_task() noexcept
{
    finish_game_task();
    std::exit(0);
}
}

AccountNetworkActivity::AccountNetworkActivity() noexcept { ++foreground_requests; }
AccountNetworkActivity::~AccountNetworkActivity() { --foreground_requests; }
void request_session_reauthentication(const std::string& account_id,
                                      const std::string& generation)
{
    pending_reauthentication = LostIdentity{account_id, generation};
}
void clear_session_reauthentication() noexcept { pending_reauthentication.reset(); }

struct SessionWatch::Impl {
    std::unique_ptr<Probe> probe;
    Clock::time_point next = Clock::now();
    bool active = false;
    // Non-authorizing fallback if rereading public connection metadata fails.
    std::string account_id, generation;

    void require_login()
    {
        probe.reset();
        invalidate_player_session();
        // Without a known resume identity, a fresh account could take over an
        // already open game or dialog. Exit instead of allowing that fallback.
        if(account_id.empty() || generation.empty()) finish_authentication_task();
        try {
            if(!ensure_player_session(account_id, generation,
                "The connection to your Windows game account was lost. You have been signed out. "
                "Check the local network and Windows services, then log in again to continue.")) {
                finish_authentication_task();
            }
        } catch(...) {
            // An auth gate must never fall through to the paused game or menus.
            finish_authentication_task();
        }
        next = Clock::now() + interval;
    }

    void process()
    {
        if(active || foreground_requests != 0) return;
        struct Guard { bool& value; Guard(bool& v):value(v){value=true;} ~Guard(){value=false;} } guard(active);
        if(pending_reauthentication) {
            account_id = std::move(pending_reauthentication->account_id);
            generation = std::move(pending_reauthentication->generation);
            pending_reauthentication.reset();
            require_login();
            return;
        }
        if(!has_player_session()) {
            probe.reset();
            account_id.clear();
            generation.clear();
            next = Clock::now();
            return;
        }
        if(probe && !probe->done.load(std::memory_order_acquire)) return;
        if(!probe && Clock::now() < next) return;

        try {
            const bool had_session = has_player_session();
            auto current = player_session();
            if(!current) {
                if(had_session) {
                    require_login();
                    return;
                }
                probe.reset();
                account_id.clear();
                generation.clear();
                next = Clock::now() + interval;
                return;
            }
            account_id = current->account.account_id;
            generation = current->account.database_generation;
            if(probe) {
                probe->join();
                const auto& old = probe->snapshot;
                if(old.token != current->token || old.account.account_id != account_id ||
                   old.account.database_generation != generation) {
                    // Logout/login may have completed while this old read ran.
                    probe.reset();
                    next = Clock::now();
                    return;
                }
                const auto& result = probe->reply;
                if(!result.ok || result.profile.account_id != account_id ||
                   result.profile.database_generation != generation) {
                    require_login();
                    return;
                }
                probe.reset();
                next = Clock::now() + interval;
                return;
            }
            probe = std::make_unique<Probe>(std::move(*current));
        } catch(...) {
            // A failed profile read or worker allocation cannot leave the
            // current game authorized by cached connection metadata.
            require_login();
        }
    }
};

SessionWatch::SessionWatch() : impl_(std::make_unique<Impl>()) {}
SessionWatch::~SessionWatch() = default;
void SessionWatch::process() { impl_->process(); }
}
