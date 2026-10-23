// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/replay_launch.hpp"
#include "alliance_game/progression_result.hpp"
#include "alliance_game/shared_result.hpp"
#include <stdexcept>
namespace alliance_game {
namespace {
struct Request {AllianceReplay kind;std::string account,run;};
std::optional<Request> pending;
std::string verify(const Request& value) {
    return value.kind==AllianceReplay::campaign?verified_progression_replay(value.account,value.run):verified_shared_replay(value.account,value.run);
}
}
void queue_alliance_replay(AllianceReplay kind,const std::string& account,const std::string& run) {
    if(pending) throw std::runtime_error("A replay is already queued.");
    Request request{kind,account,run};(void)verify(request);pending=std::move(request);
}
bool replay_pending() noexcept {return pending.has_value();}
std::optional<std::string> take_verified_alliance_replay() {
    if(!pending) return std::nullopt;
    auto request=std::move(*pending);pending.reset();return verify(request);
}
}
