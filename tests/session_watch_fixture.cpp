// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/session_watch.hpp"
#include "alliance_game/account_ui.hpp"
#include <atomic>
#include <cassert>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>

namespace {
using namespace std::chrono_literals;
using alliance_game::PlayerSession;
std::optional<PlayerSession> current;
std::atomic<int> calls{0}, entered{0}, completed{0}, cancellations{0};
std::atomic<bool> held{false};
std::atomic<int> mode{0};
int reads = 0, gates = 0, invalidations = 0;
bool read_error = false, metadata_changed = false;
std::string gate_mode;
alliance_game::SessionWatch* pumping = nullptr;
const std::thread::id main_thread = std::this_thread::get_id();

PlayerSession session(const std::string& token = "private-token-original")
{
    return {{"100.99.99.14", 15001, "fixture public certificate", "fixture public pin"},
            {"actor", "UniPlayer", "realm", {}}, token};
}

void reset()
{
    assert(entered == completed);
    current = session();
    calls = entered = completed = cancellations = 0;
    held = false;
    mode = 0;
    reads = gates = invalidations = 0;
    read_error = metadata_changed = false;
    pumping = nullptr;
}

template<class Predicate> void wait_for(Predicate test)
{
    const auto deadline = std::chrono::steady_clock::now() + 2s;
    while(!test()) {
        assert(std::chrono::steady_clock::now() < deadline);
        std::this_thread::sleep_for(1ms);
    }
}

void drain(alliance_game::SessionWatch& watch)
{
    wait_for([&]{ watch.process(); return reads >= 2 || gates > 0; });
}
}

namespace alliance_game {
PlayerSession::~PlayerSession()
{
    volatile char* data = token.empty() ? nullptr : &token[0];
    for(std::size_t i = 0; i < token.size(); ++i) data[i] = 0;
}
bool has_player_session() noexcept { return bool(current); }
std::optional<PlayerSession> player_session()
{
    assert(std::this_thread::get_id() == main_thread);
    ++reads;
    if(read_error) throw std::runtime_error("private-error profile metadata");
    if(metadata_changed) { current.reset(); return std::nullopt; }
    return current;
}
void invalidate_player_session()
{
    assert(std::this_thread::get_id() == main_thread);
    ++invalidations;
    current.reset();
}
bool ensure_player_session(const std::string& actor, const std::string& realm, const std::string& warning)
{
    assert(std::this_thread::get_id() == main_thread);
    assert(!current);
    assert(actor == "actor" && realm == "realm");
    assert(warning.find("local network") != std::string::npos);
    assert(warning.find("signed out") != std::string::npos);
    ++gates;
    if(pumping) {
        const auto before = reads;
        pumping->process();
        assert(before == reads); // Auth/network modal recursion cannot start another gate.
    }
    if(gate_mode == "quit") return false;
    if(gate_mode == "auth-error") throw std::runtime_error("private-error authentication");
    current = session("private-token-after-login");
    return true;
}
void finish_game_task() noexcept
{
    assert(!current && gates == 1 && invalidations == 1);
    assert(entered == completed); // Probe thread joined before activity completion.
    std::cout << "TASK FINISHED" << std::endl;
}
}

namespace alliance::account {
Reply perform_request(const TrustedProfile& trusted, const Request& input, const CancellationToken& cancel)
{
    assert(std::this_thread::get_id() != main_thread);
    assert(trusted.address == "100.99.99.14" && trusted.port == 15001);
    assert(trusted.certificate_pem == "fixture public certificate");
    assert(input.operation == "me" && input.username.empty() && input.password.empty());
    assert(input.token == "private-token-original" || input.token == "private-token-after-login" ||
           input.token == "private-token-replacement");
    ++calls;
    ++entered;
    while(held && !cancel.requested()) std::this_thread::sleep_for(1ms);
    if(cancel.requested()) { ++cancellations; ++completed; return {}; }
    const int choice = mode.load();
    Reply result;
    result.ok = choice == 0 || choice == 5 || choice == 6;
    result.profile.account_id = choice == 5 ? "different-actor" : "actor";
    result.profile.database_generation = choice == 6 ? "different-realm" : "realm";
    const char* codes[] = {"", "unavailable", "timeout", "invalid_session", "protocol"};
    if(choice >= 1 && choice <= 4) result.error_code = codes[choice];
    ++completed;
    if(choice == 7) throw std::runtime_error("private-error worker boundary");
    return result;
}
}

int main(int argc, char** argv)
{
    assert(argc == 2);
    gate_mode = argv[1];
    reset();
    if(gate_mode != "normal") {
        mode = 1;
        alliance_game::SessionWatch watch;
        pumping = &watch;
        watch.process();
        drain(watch);
        assert(false); // Terminal auth choices may not return to this paused caller.
    }
    {
        current.reset();
        alliance_game::SessionWatch watch;
        for(int i = 0; i < 1000; ++i) watch.process();
        assert(reads == 0 && calls == 0 && gates == 0);
    }
    reset();
    {
        alliance_game::SessionWatch watch;
        watch.process();
        drain(watch);
        assert(calls == 1 && gates == 0 && reads == 2);
        for(int i = 0; i < 1000; ++i) watch.process();
        assert(reads == 2 && calls == 1); // No per-frame metadata or endpoint reads.
        std::this_thread::sleep_for(3050ms);
        watch.process();
        wait_for([]{ return calls == 2; });
    }
    for(int failure = 1; failure <= 7; ++failure) {
        reset();
        mode = failure;
        alliance_game::SessionWatch watch;
        pumping = &watch;
        watch.process();
        drain(watch);
        assert(gates == 1 && invalidations == 1 && current);
        assert(current->token == "private-token-after-login");
        watch.process();
        assert(gates == 1 && calls == 1); // No repeated warning or automatic login.
    }
    reset();
    {
        held = true;
        mode = 1;
        alliance_game::SessionWatch watch;
        watch.process();
        wait_for([]{ return entered == 1; });
        {
            alliance_game::AccountNetworkActivity outer;
            { alliance_game::AccountNetworkActivity inner; watch.process(); }
            held = false;
            wait_for([]{ return completed == 1; });
            for(int i = 0; i < 1000; ++i) watch.process();
            assert(reads == 1 && gates == 0);
        }
        drain(watch);
        assert(gates == 1); // Defer until the foreground operation has returned.
    }
    reset();
    {
        held = true;
        mode = 1;
        alliance_game::SessionWatch watch;
        watch.process();
        wait_for([]{ return entered == 1; });
        current = session("private-token-replacement");
        held = false;
        drain(watch);
        assert(gates == 0 && invalidations == 0);
        assert(current->token == "private-token-replacement");
    }
    reset();
    {
        held = true;
        alliance_game::SessionWatch watch;
        watch.process();
        wait_for([]{ return entered == 1; });
        current.reset();
        watch.process();
        assert(cancellations == 1 && completed == 1);
        assert(gates == 0 && invalidations == 0); // Explicit logout cancels promptly, without nested auth.
    }
    for(bool corrupt : {false, true}) {
        reset();
        held = true;
        alliance_game::SessionWatch watch;
        pumping = &watch;
        watch.process();
        wait_for([]{ return entered == 1; });
        read_error = corrupt;
        metadata_changed = !corrupt;
        held = false;
        drain(watch);
        assert(gates == 1 && invalidations == 1); // Retained identity survives metadata failure.
    }
    reset();
    {
        held = true;
        {
            alliance_game::SessionWatch watch;
            watch.process();
            wait_for([]{ return entered == 1; });
        }
        assert(cancellations == 1 && entered == completed && gates == 0);
    }
    std::cout << "PASS 15 session watcher groups\n";
}
