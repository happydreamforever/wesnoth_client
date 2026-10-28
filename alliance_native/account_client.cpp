// SPDX-License-Identifier: GPL-2.0-or-later
#include "account_client.hpp"
#include "account_protocol.hpp"
#include "progression_protocol.hpp"
#include "unified_protocol.hpp"
#include "flexible_protocol.hpp"
#include "world_protocol.hpp"
#include <openssl/rand.h>
#include <algorithm>
#include <array>
#include <chrono>
#include <fstream>
#include <initializer_list>
#include <limits>
#include <stdexcept>
#include <set>
#include <sys/stat.h>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <memory>
#endif

namespace alliance::account {
namespace {
namespace json = boost::json;
#ifdef _WIN32
struct ProfileHandleCloser {
    void operator()(void* handle) const noexcept { CloseHandle(handle); }
};

std::string read_windows_profile(const std::string& path) {
    // Preserve the Windows narrow-path convention used by getenv/ifstream:
    // interpret paths with the active ANSI code page, never truncate at NUL.
    if(path.empty() || path.size() > 4096 || path.find('\0') != std::string::npos)
        throw ProfileReadError(ProfileReadFailure::file_unavailable);
    const auto length = MultiByteToWideChar(CP_ACP, MB_ERR_INVALID_CHARS,
                                          path.data(), static_cast<int>(path.size()), nullptr, 0);
    if(length == 0) throw ProfileReadError(ProfileReadFailure::file_unavailable);
    std::wstring filename(static_cast<std::size_t>(length), L'\0');
    if(MultiByteToWideChar(CP_ACP, MB_ERR_INVALID_CHARS, path.data(),
                         static_cast<int>(path.size()), filename.data(), length) != length)
        throw ProfileReadError(ProfileReadFailure::file_unavailable);

    // Match the POSIX reader's final-component link/type/size policy. Checking
    // attributes first also avoids opening devices and named pipes as streams.
    WIN32_FILE_ATTRIBUTE_DATA attributes{};
    constexpr DWORD rejected = FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DEVICE;
    if(!GetFileAttributesExW(filename.c_str(), GetFileExInfoStandard, &attributes)
       || (attributes.dwFileAttributes & rejected) || attributes.nFileSizeHigh != 0
       || attributes.nFileSizeLow == 0 || attributes.nFileSizeLow > max_json_bytes)
        throw ProfileReadError(ProfileReadFailure::file_unavailable);
    const auto opened = CreateFileW(filename.c_str(), GENERIC_READ, FILE_SHARE_READ,
                                   nullptr, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
    if(opened == INVALID_HANDLE_VALUE)
        throw ProfileReadError(ProfileReadFailure::file_read);
    const std::unique_ptr<void, ProfileHandleCloser> handle(opened);
    BY_HANDLE_FILE_INFORMATION before{};
    if(GetFileType(handle.get()) != FILE_TYPE_DISK
       || !GetFileInformationByHandle(handle.get(), &before)
       || (before.dwFileAttributes & rejected) || before.nFileSizeHigh != 0
       || before.nFileSizeLow == 0 || before.nFileSizeLow > max_json_bytes)
        throw ProfileReadError(ProfileReadFailure::file_unavailable);

    // Read and validate the same handle, denying concurrent writers/renames.
    // The extra byte detects growth even on filesystems with weak timestamps.
    std::string raw(max_json_bytes + 1, '\0');
    std::size_t used = 0;
    while(used < raw.size()) {
        DWORD count = 0;
        if(!ReadFile(handle.get(), raw.data() + used,
                     static_cast<DWORD>(raw.size() - used), &count, nullptr))
            throw ProfileReadError(ProfileReadFailure::file_read);
        if(count == 0) break;
        used += count;
    }
    BY_HANDLE_FILE_INFORMATION after{};
    if(!GetFileInformationByHandle(handle.get(), &after) || used != before.nFileSizeLow
       || used > max_json_bytes || before.dwVolumeSerialNumber != after.dwVolumeSerialNumber
       || before.nFileIndexHigh != after.nFileIndexHigh || before.nFileIndexLow != after.nFileIndexLow
       || before.nFileSizeHigh != after.nFileSizeHigh || before.nFileSizeLow != after.nFileSizeLow
       || before.dwFileAttributes != after.dwFileAttributes || before.nNumberOfLinks != after.nNumberOfLinks
       || CompareFileTime(&before.ftLastWriteTime, &after.ftLastWriteTime) != 0)
        throw ProfileReadError(ProfileReadFailure::file_read);
    raw.resize(used);
    return raw;
}
#endif
[[noreturn]] void invalid() { throw std::runtime_error("Invalid Alliance account response."); }
void keys(const json::object& obj, std::initializer_list<const char*> expected) {
    if(obj.size() != expected.size()) invalid();
    for(const auto* key : expected) if(!obj.contains(key)) invalid();
}
std::string string(const json::object& obj, const char* key, std::size_t maximum) {
    const auto* value = obj.if_contains(key);
    if(!value || !value->is_string()) invalid();
    const auto& text = value->as_string();
    if(text.empty() || text.size() > maximum
       || std::find(text.begin(), text.end(), '\0') != text.end()) invalid();
    return {text.data(), text.size()};
}
std::int64_t wide_integer(const json::object& obj, const char* key, std::int64_t minimum = 0) {
    const auto* v = obj.if_contains(key);
    if(!v || !v->is_int64() || v->as_int64() < minimum) invalid();
    return v->as_int64();
}
int integer(const json::object& obj, const char* key, int minimum, int maximum) {
    const auto* value = obj.if_contains(key);
    if(!value || !value->is_int64() || value->as_int64() < minimum
       || value->as_int64() > maximum) invalid();
    return static_cast<int>(value->as_int64());
}
const json::object& object(const json::object& obj, const char* key) {
    const auto* value = obj.if_contains(key);
    if(!value || !value->is_object()) invalid();
    return value->as_object();
}
bool uuid_valid(const std::string& id) {
    if(id.size() != 36) return false;
    for(std::size_t i = 0; i < id.size(); ++i) {
        const char c = id[i];
        if(i == 8 || i == 13 || i == 18 || i == 23) { if(c != '-') return false; }
        else if(!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
    }
    return true;
}
bool cursor_valid(const std::string& value) {
    return value.size() <= 64 && std::all_of(value.begin(), value.end(), [](char c) {
        return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
               (c >= '0' && c <= '9') || c == '_' || c == '-';
    });
}
bool utc_timestamp_valid(const std::string& value) {
    if(value.size() != 20 || value[4] != '-' || value[7] != '-' ||
       value[10] != 'T' || value[13] != ':' || value[16] != ':' || value[19] != 'Z') return false;
    for(std::size_t i = 0; i < value.size(); ++i) {
        if(i == 4 || i == 7 || i == 10 || i == 13 || i == 16 || i == 19) continue;
        if(value[i] < '0' || value[i] > '9') return false;
    }
    const auto number = [&](std::size_t start, std::size_t length) {
        int result = 0;
        for(std::size_t i = start; i < start + length; ++i) result = result * 10 + value[i] - '0';
        return result;
    };
    const int year = number(0, 4), month = number(5, 2), day = number(8, 2);
    if(year == 0 || month < 1 || month > 12 || day < 1 || number(11, 2) > 23 ||
       number(14, 2) > 59 || number(17, 2) > 59) return false;
    constexpr int days[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    const bool leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    return day <= days[month - 1] + (month == 2 && leap ? 1 : 0);
}
bool membership_mutation(const std::string& op) {
    return op == "apply_team" || op == "withdraw_application" ||
           op == "approve_application" || op == "reject_application";
}
bool flexible_operation(const std::string& op) {
    return op == "publish_plan" || op == "edit_plan" || op == "join_plan" || op == "leave_plan" ||
           op == "approve_plan" || op == "ready_plan" || op == "select_plan_hero";
}
bool battle_operation(const std::string& op) {
    return flexible_operation(op) || op == "battle_maps" || op == "my_battles" || op == "battle_detail" ||
        op == "propose_battle" || op == "approve_battle" || op == "battle_ready" || op == "battle_select_hero" || op == "cancel_battle";
}
bool team_operation(const std::string& op) {
    return op == "areas" || op == "teams" || op == "my_team" ||
           op == "create_team" || op == "team_detail" || op == "my_applications" ||
           op == "team_applicants" || op == "application_decisions" || op == "ack_decision" ||
           op == "leave_team" || op == "transfer_captain" || op == "standings" || membership_mutation(op) || battle_operation(op);
}
Reply failure(const std::string& code, bool is_team = false) {
    Reply result;
    result.error_code = code;
    if(code == "update_required") result.error_message = "Update the game and local server to the same release.";
    else if(code == "invalid_credentials") result.error_message = "Invalid username or password.";
    else if(code == "conflict") result.error_message = "That username is already in use.";
    else if(code == "invalid_session" || code == "forbidden" || code == "suspended")
        result.error_message = "This session is no longer valid. Please log in again.";
    else if(code == "rate_limited")
        result.error_message = "Too many login attempts. Try again in 15 minutes.";
    else if(code == "invalid_input" || code == "invalid_password")
        result.error_message = "Check your username and password.";
    else if(code == "cancelled")
        result.error_message = is_team ? "Team request cancelled." :
            "Request cancelled. If you were creating an account, try logging in to check whether it was created.";
    else if(code == "timeout")
        result.error_message = is_team ? "The server did not reply in time. Check the Windows Alliance service." :
            "The server did not reply in time. If you were creating an account, try logging in before registering again.";
    else if(code == "identity")
        result.error_message = "Server identity verification failed. Check the imported public profile.";
    else if(code == "protocol")
        result.error_message = is_team ? "The server returned an unsupported team response." :
            "The server returned an unsupported account response.";
    else result.error_message = "The Alliance service is unavailable. Check the Windows database, service and account bridge.";
    return result;
}
Reply transport_failure(const Result& result, bool is_team) {
    if(result.error == Error::cancelled) return failure("cancelled", is_team);
    if(result.error == Error::timeout) return failure("timeout", is_team);
    if(result.stage == Stage::profile || result.stage == Stage::preamble_read
       || result.stage == Stage::tls_handshake || result.stage == Stage::pin)
        return failure("identity");
    return failure("unavailable");
}
Reply team_failure(const std::string& code) {
    Reply result;
    result.error_code = code;
    if(code == "human_required" || code == "limit_reached" || code == "hero_locked" || code == "form_locked" || code == "unbalanced_sides") result.error_message = progression_protocol::public_error(code);
    else if(code == "stale_revision") result.error_message = "This plan changed. Refresh and try again.";
    else if(code == "slot_taken") result.error_message = "That seat is already occupied.";
    else if(code == "update_required") result.error_message = "Update the game and local server to the same release.";
    else if(code == "conflict") result.error_message = "The team name, membership or application conflicts with the current server state. Refresh the team or applications before trying again.";
    else if(code == "already_member") result.error_message = "This player already belongs to a team. Refresh My team or the applicant list.";
    else if(code == "busy") result.error_message = "This player is already preparing or playing another battle.";
    else if(code == "not_found") result.error_message = "That area, team or decision is no longer available. Open the list again.";
    else if(code == "forbidden") result.error_message = "This team action is not available to your player account.";
    else if(code == "invalid_input") result.error_message = "Check the selected area, team, application or decision.";
    else if(code == "capacity") result.error_message = "The team or its application queue is full. Refresh the roster or applications.";
    else if(code == "not_requested") result.error_message = "That application is no longer pending. Refresh the roster and applications before trying another action.";
    else if(code == "not_member") result.error_message = "That player is no longer a member of this team. Refresh My team before trying another action.";
    else if(code == "captain_transfer_required") result.error_message = "Transfer captain to another current member before leaving this team.";
    else if(code == "planning_exists") result.error_message = "One of these teams already has a draft battle plan. Open My battle plans before proposing another.";
    else if(code == "invalid_map") result.error_message = "This map is not currently available in the selected area. Load the map list again.";
    else if(code == "invalid_lineup") result.error_message = "The selected lineups no longer match the map and teams. Load the rosters again.";
    else if(code == "different_area") result.error_message = "Both teams must belong to the same area.";
    else if(code == "stale_proposal") result.error_message = "This plan no longer matches the current teams or leaders. Open its detail; a current captain can cancel the draft and prepare a new plan.";
    else if(code == "advancement_required") result.error_message = "A selected player must choose their available leader advancement in Campaign progression before preparing this battle.";
    else if(code == "hero_required") result.error_message = "Choose one of your unlocked heroes and forms before becoming ready.";
    else if(code == "approval_required") result.error_message = "Both current captains must approve this saved plan before players can become ready.";
    else if(code == "invalid_state") result.error_message = "This battle plan is no longer a draft. Refresh My battle plans before making another change.";
    else { result.error_code = "team_failed"; result.error_message = "The team request could not be completed. Try viewing My team."; }
    return result;
}
std::string uuid(const json::object& data, const char* key) {
    auto value = string(data, key, 36);
    if(!uuid_valid(value)) invalid();
    return value;
}
std::string area_id(const json::object& data, const char* key) {
    auto value = string(data, key, 64);
    if(!cursor_valid(value)) invalid();
    return value;
}
std::string name(const json::object& data, const char* key) {
    auto value = string(data, key, 40);
    if(!team_name_valid(value)) invalid();
    return value;
}
const json::array& array(const json::object& data, const char* key, std::size_t maximum) {
    const auto* value = data.if_contains(key);
    if(!value || !value->is_array() || value->as_array().size() > maximum) invalid();
    return value->as_array();
}
bool boolean(const json::object& data, const char* key) {
    const auto* value = data.if_contains(key);
    if(!value || !value->is_bool()) invalid();
    return value->as_bool();
}
BattleMap known_map(const std::string& id) {
    if(id == "isars-cross") return {id, "Isar's Cross", "multiplayer_Isars_Cross", 2, {{1, 4}, {2, 3}}};
    if(id == "hexcake") return {id, "Hexcake", "multiplayer_Hexcake", 3, {{1, 4, 5}, {2, 3, 6}}};
    if(id == "mokena-prairie") return {id, "Mokena Prairie", "multiplayer_8p_Mokena_Prairie", 4, {{1, 3, 5, 8}, {2, 4, 6, 7}}};
    invalid();
}
BattleMap battle_map(const json::object& data) {
    keys(data, {"id", "name", "scenario_id", "size", "sides"});
    auto result = known_map(string(data, "id", 32));
    if(string(data, "name", 40) != result.name || string(data, "scenario_id", 64) != result.scenario_id ||
       integer(data, "size", 2, 4) != result.size) invalid();
    const auto& sides = array(data, "sides", 2);
    if(sides.size() != 2) invalid();
    for(std::size_t i = 0; i < sides.size(); ++i) {
        if(!sides[i].is_array() || sides[i].as_array().size() != result.sides[i].size()) invalid();
        const auto& values = sides[i].as_array();
        for(std::size_t j = 0; j < values.size(); ++j)
            if(!values[j].is_int64() || values[j].as_int64() != result.sides[i][j]) invalid();
    }
    return result;
}
std::string battle_state(const json::object& data) {
    auto result = string(data, "state", 16);
    if(result != "draft" && result != "running" && result != "completed" && result != "cancelled") invalid();
    return result;
}
std::string battle_created(const json::object& data) {
    auto result = string(data, "created_at", 20);
    if(!utc_timestamp_valid(result)) invalid();
    return result;
}
Battle battle(const json::object& data) {
    const bool flexible = data.contains("format");
    if(flexible) keys(data, {"id", "area_id", "state", "created_at", "map", "validity", "teams",
        "format", "revision", "settings", "ranked", "room_state", "generation", "engine_host_account_id"});
    else keys(data, {"id", "area_id", "state", "created_at", "map", "validity", "teams"});
    Battle result;
    if(flexible) {
        flexible_protocol::battle(data);
        result.format = integer(data,"format",2,2); result.revision = wide_integer(data,"revision");
        const auto& rules=object(data,"settings");
        keys(rules,{"gold","base_income","turns","fog","shroud","observers"});
        result.settings.gold=integer(rules,"gold",0,1000);
        result.settings.base_income=integer(rules,"base_income",0,20);
        result.settings.turns=integer(rules,"turns",-1,1000);
        if(result.settings.turns==0)invalid();
        result.settings.fog=boolean(rules,"fog");result.settings.shroud=boolean(rules,"shroud");
        result.settings.observers=boolean(rules,"observers");result.ranked=boolean(data,"ranked");
        result.room_state=progression_protocol::text(data,"room_state",32,true);
        result.generation=progression_protocol::text(data,"generation",64,true);
        result.engine_host_account_id=progression_protocol::text(data,"engine_host_account_id",36,true);
        if(!result.engine_host_account_id.empty()&&!uuid_valid(result.engine_host_account_id))invalid();
    }
    result.id = uuid(data, "id");
    result.area_id = area_id(data, "area_id");
    result.state = battle_state(data);
    result.created_at = battle_created(data);
    result.map = battle_map(object(data, "map"));
    result.validity = string(data, "validity", 32);
    const std::set<std::string> validities{"valid", "roster_changed", "area_changed", "map_unavailable",
        "leader_changed", "player_unavailable", "team_unavailable", "busy", "rules_changed", "unbalanced_sides"};
    if(!validities.count(result.validity)) invalid();
    std::set<std::string> team_ids, player_ids, captain_ids;
    const auto& teams = array(data, "teams", 2);
    if(teams.size() != 2) invalid();
    for(std::size_t i = 0; i < teams.size(); ++i) {
        if(!teams[i].is_object()) invalid();
        const auto& source = teams[i].as_object();
        if(flexible) keys(source, {"id", "name", "captain_id", "approved", "players", "slots"});
        else keys(source, {"id", "name", "captain_id", "approved", "players"});
        BattleTeam team;
        team.id = uuid(source, "id");
        team.name = name(source, "name");
        if(!team_ids.insert(team.id).second) invalid();
        const auto* captain = source.if_contains("captain_id");
        if(!captain || !captain->is_string()) invalid();
        team.captain_id = std::string(captain->as_string());
        team.approved = boolean(source, "approved");
        if(team.captain_id.empty()) { if(team.approved) invalid(); }
        else if(!uuid_valid(team.captain_id) || !captain_ids.insert(team.captain_id).second) invalid();
        const auto& players = array(source, "players", 4);
        if(!flexible && players.size() != static_cast<std::size_t>(result.map.size)) invalid();
        std::set<int> occupied;
        if(flexible) {
            const auto& slots=array(source,"slots",4);
            if(slots.size()!=static_cast<std::size_t>(result.map.size))invalid();
            std::set<int> positions;
            for(const auto& v:slots) {
                if(!v.is_object())invalid();
                const auto& row=v.as_object();
                keys(row,{"side","kind","account_id","ai_faction","ai_leader"});
                BattleSlot slot;slot.side=integer(row,"side",1,8);slot.kind=string(row,"kind",16);
                slot.account_id=progression_protocol::text(row,"account_id",36,true);
                slot.ai_faction=progression_protocol::text(row,"ai_faction",64,true);
                slot.ai_leader=progression_protocol::text(row,"ai_leader",128,true);
                if(!positions.insert(slot.side).second || std::find(result.map.sides[i].begin(),result.map.sides[i].end(),slot.side)==result.map.sides[i].end())invalid();
                if(slot.kind!="open"&&slot.kind!="reserved"&&slot.kind!="ai"&&slot.kind!="closed")invalid();
                if(!slot.account_id.empty()&&!uuid_valid(slot.account_id))invalid();
                if(slot.kind=="reserved"&&slot.account_id.empty())invalid();
                if(slot.kind=="ai") { if(!slot.account_id.empty()||slot.ai_faction.empty()||slot.ai_leader.empty())invalid(); }
                else if(!slot.ai_faction.empty()||!slot.ai_leader.empty()||(slot.kind=="closed"&&!slot.account_id.empty()))invalid();
                team.slots.push_back(std::move(slot));
            }
        }
        for(std::size_t j = 0; j < players.size(); ++j) {
            if(!players[j].is_object()) invalid();
            const auto& entry = players[j].as_object();
            keys(entry, {"id", "username", "selection", "side", "ready"});
            BattlePlayer player;
            player.id = uuid(entry, "id");
            player.username = string(entry, "username", 24);
            const auto& selected = entry.at("selection");
            if(!selected.is_null()) {
                if(!selected.is_object()) invalid();
                const auto& choice = selected.as_object();
                keys(choice, {"hero_id", "form_id", "hero_level", "hero_revision", "unit_type", "unit_level"});
                player.hero_id = string(choice, "hero_id", 80);
                player.form_id = string(choice, "form_id", 100);
                player.unit_type = string(choice, "unit_type", 128);
                player.hero_level = wide_integer(choice, "hero_level", 1);
                player.hero_revision = wide_integer(choice, "hero_revision", 0);
                player.level = integer(choice, "unit_level", 0, 20);
                if(!catalog_id_valid(player.hero_id) || !catalog_id_valid(player.form_id)) invalid();
                player.selection_present = true;
            }
            player.side = integer(entry, "side", 1, 8);
            player.ready = boolean(entry, "ready");
            if(!player_ids.insert(player.id).second || !username_valid(player.username) ||
               (player.ready && !player.selection_present) || !occupied.insert(player.side).second ||
               (!flexible && player.side != result.map.sides[i][j])) invalid();
            if(flexible) {
                const auto slot=std::find_if(team.slots.begin(),team.slots.end(),[&](const auto& x){return x.side==player.side;});
                if(slot==team.slots.end() || (slot->kind!="open"&&slot->kind!="reserved") || (slot->kind=="reserved"&&slot->account_id!=player.id))invalid();
            }
            team.players.push_back(std::move(player));
        }
        result.teams.push_back(std::move(team));
    }
    return result;
}
void battle_response(const json::object& data, const Request& input, Reply& reply) {
    const auto& op = input.operation;
    if(op == "battle_maps") {
        keys(data, {"area_id", "maps"});
        if(area_id(data, "area_id") != input.area_id) invalid();
        std::set<std::string> ids;
        for(const auto& entry : array(data, "maps", 3)) {
            if(!entry.is_object()) invalid();
            auto map = battle_map(entry.as_object());
            if(!ids.insert(map.id).second) invalid();
            reply.maps.push_back(std::move(map));
        }
    } else if(op == "my_battles") {
        keys(data, {"battles", "next"});
        std::string previous = input.after;
        for(const auto& entry : array(data, "battles", 20)) {
            if(!entry.is_object()) invalid();
            const auto& source = entry.as_object();
            keys(source, {"id", "area_id", "map_id", "state", "created_at", "teams"});
            BattleSummary summary;
            summary.id = uuid(source, "id");
            summary.area_id = area_id(source, "area_id");
            summary.map_id = known_map(string(source, "map_id", 32)).id;
            summary.state = battle_state(source);
            summary.created_at = battle_created(source);
            const auto& teams = array(source, "teams", 2);
            if(teams.size() != 2 || summary.id <= previous) invalid();
            for(const auto& value : teams) {
                if(!value.is_object()) invalid();
                const auto& team = value.as_object();
                keys(team, {"id", "name"});
                summary.teams.push_back({uuid(team, "id"), name(team, "name")});
            }
            if(summary.teams[0].id == summary.teams[1].id) invalid();
            previous = summary.id;
            reply.battles.push_back(std::move(summary));
        }
        const auto* next = data.if_contains("next");
        if(!next || !next->is_string()) invalid();
        reply.next = std::string(next->as_string());
        if(!reply.next.empty() && (!uuid_valid(reply.next) || reply.battles.size() != 20 || reply.next != previous)) invalid();
    } else {
        keys(data, {"battle"});
        reply.battle = battle(object(data, "battle"));
        const auto& result = *reply.battle;
        if(flexible_operation(op)) {
            if(result.format!=2)invalid();
            if(op=="publish_plan") {
                if(result.state!="draft"||result.map.id!=input.map_id||result.teams[0].id!=input.team_id||result.teams[1].id!=input.opponent_team_id)invalid();
            } else if(result.id!=input.battle_id||result.revision<input.revision)invalid();
        } else if(op == "propose_battle") {
            if(result.state != "draft" || result.validity != "valid" || result.map.id != input.map_id ||
               result.teams[0].id != input.team_id || result.teams[1].id != input.opponent_team_id) invalid();
            for(std::size_t i = 0; i < result.teams.size(); ++i) {
                if(result.teams[i].approved) invalid();
                for(std::size_t j = 0; j < result.teams[i].players.size(); ++j)
                    if(result.teams[i].players[j].id != input.lineups[i].account_ids[j] || result.teams[i].players[j].ready) invalid();
            }
        } else {
            if(result.id != input.battle_id) invalid();
            if(op == "cancel_battle" && result.state != "cancelled") invalid();
            if((op == "approve_battle" || op == "battle_ready" || op == "battle_select_hero") && result.state != "draft") invalid();
            if((op == "approve_battle" || (op == "battle_ready" && input.ready)) && result.validity != "valid") invalid();
            if(op == "battle_ready" && input.ready && (!result.teams[0].approved || !result.teams[1].approved)) invalid();
        }
    }
}
Member member(const json::object& source) {
    keys(source, {"id", "username", "hero_count"});
    Member result;
    result.id = uuid(source, "id");
    result.username = string(source, "username", 24);
    result.hero_count = integer(source, "hero_count", 0, 256);
    if(!username_valid(result.username)) invalid();
    return result;
}
TeamSummary team_summary(const json::object& source) {
    keys(source, {"id", "name", "area_id", "captain_id", "level", "experience", "rating", "member_count"});
    TeamSummary entry;
    entry.id = uuid(source, "id");
    entry.name = name(source, "name");
    entry.area_id = area_id(source, "area_id");
    entry.captain_id = uuid(source, "captain_id");
    entry.level = integer(source, "level", 1, std::numeric_limits<int>::max());
    entry.experience = integer(source, "experience", 0, std::numeric_limits<int>::max());
    entry.rating = integer(source, "rating", std::numeric_limits<int>::min(), std::numeric_limits<int>::max());
    entry.member_count = integer(source, "member_count", 1, 8);
    return entry;
}
Team team(const json::object& data) {
    keys(data, {"id", "name", "area_id", "area_name", "captain_id", "level", "experience",
                "rating", "capacity", "members"});
    Team result;
    result.id = uuid(data, "id");
    result.name = name(data, "name");
    result.area_id = area_id(data, "area_id");
    result.area_name = name(data, "area_name");
    result.captain_id = uuid(data, "captain_id");
    result.level = integer(data, "level", 1, std::numeric_limits<int>::max());
    result.experience = integer(data, "experience", 0, std::numeric_limits<int>::max());
    result.rating = integer(data, "rating", std::numeric_limits<int>::min(), std::numeric_limits<int>::max());
    result.capacity = integer(data, "capacity", 8, 8);
    std::set<std::string> ids;
    for(const auto& value : array(data, "members", 8)) {
        if(!value.is_object()) invalid();
        auto entry = member(value.as_object());
        if(!ids.insert(entry.id).second) invalid();
        result.members.push_back(std::move(entry));
    }
    if(ids.count(result.captain_id) != 1) invalid();
    return result;
}
void page_cursor(const json::object& data, Reply& reply, std::size_t size,
                 const std::string& last_id) {
    const auto* next = data.if_contains("next");
    if(!next || !next->is_string()) invalid();
    reply.next = std::string(next->as_string());
    if(!cursor_valid(reply.next) || (!reply.next.empty() &&
       (size != 20 || reply.next != last_id))) invalid();
}
void team_response(const json::object& data, const Request& input, Reply& reply) {
    const auto& op = input.operation;
    if(op == "areas") {
        keys(data, {"areas", "next"});
        std::string previous = input.after;
        for(const auto& value : array(data, "areas", 20)) {
            if(!value.is_object()) invalid();
            const auto& source = value.as_object();
            keys(source, {"id", "name"});
            Area entry{area_id(source, "id"), name(source, "name")};
            if(entry.id <= previous) invalid();
            previous = entry.id;
            reply.areas.push_back(std::move(entry));
        }
        page_cursor(data, reply, reply.areas.size(), previous);
    } else if(op == "teams" || op == "my_applications") {
        keys(data, {"teams", "next"});
        std::string previous = input.after;
        for(const auto& value : array(data, "teams", 20)) {
            if(!value.is_object()) invalid();
            auto entry = team_summary(value.as_object());
            if((op == "teams" && entry.area_id != input.area_id) || entry.id <= previous) invalid();
            previous = entry.id;
            reply.teams.push_back(std::move(entry));
        }
        page_cursor(data, reply, reply.teams.size(), previous);
    } else if(op == "standings") {
        keys(data, {"area_id", "teams", "has_more"});
        if(area_id(data, "area_id") != input.area_id || !data.at("has_more").is_bool()) invalid();
        reply.has_more = data.at("has_more").as_bool();
        std::set<std::string> ids;
        for(const auto& value : array(data, "teams", 20)) {
            if(!value.is_object()) invalid();
            auto entry = team_summary(value.as_object());
            if(entry.area_id != input.area_id || !ids.insert(entry.id).second) invalid();
            if(!reply.teams.empty()) {
                const auto& previous = reply.teams.back();
                if(entry.rating > previous.rating ||
                   (entry.rating == previous.rating && entry.id <= previous.id)) invalid();
            }
            reply.teams.push_back(std::move(entry));
        }
        if(reply.has_more && reply.teams.size() != 20) invalid();
    } else if(op == "team_applicants") {
        keys(data, {"team_id", "applicants", "next"});
        reply.team_id = uuid(data, "team_id");
        if(reply.team_id != input.team_id) invalid();
        std::string previous = input.after;
        for(const auto& value : array(data, "applicants", 20)) {
            if(!value.is_object()) invalid();
            auto entry = member(value.as_object());
            if(entry.id <= previous) invalid();
            previous = entry.id;
            reply.applicants.push_back(std::move(entry));
        }
        page_cursor(data, reply, reply.applicants.size(), previous);
    } else if(op == "application_decisions") {
        keys(data, {"decisions"});
        std::set<std::string> ids;
        for(const auto& value : array(data, "decisions", 50)) {
            if(!value.is_object()) invalid();
            const auto& source = value.as_object();
            keys(source, {"id", "team_id", "team_name", "status", "created_at", "unread"});
            Decision entry;
            entry.id = uuid(source, "id");
            entry.team_id = uuid(source, "team_id");
            entry.team_name = name(source, "team_name");
            entry.status = string(source, "status", 8);
            entry.created_at = string(source, "created_at", 20);
            const auto& unread = source.at("unread");
            if(!ids.insert(entry.id).second || !unread.is_bool() ||
               (entry.status != "approved" && entry.status != "rejected") ||
               !utc_timestamp_valid(entry.created_at)) invalid();
            entry.unread = unread.as_bool();
            // Keep committed server order. Equal or adjusted host-clock times
            // do not establish a different ordering of committed decisions.
            reply.decisions.push_back(std::move(entry));
        }
    } else if(op == "ack_decision") {
        keys(data, {"decision_id", "status"});
        reply.decision_id = uuid(data, "decision_id");
        reply.status = string(data, "status", 4);
        if(reply.decision_id != input.decision_id || reply.status != "read") invalid();
    } else if(membership_mutation(op) || op == "leave_team" || op == "transfer_captain") {
        if(op == "leave_team") {
            keys(data, {"team_id", "account_id", "status", "archived"});
            if(!data.at("archived").is_bool()) invalid();
            reply.archived = data.at("archived").as_bool();
            if(reply.archived && !input.allow_archive) invalid();
        } else keys(data, {"team_id", "account_id", "status"});
        reply.team_id = uuid(data, "team_id");
        reply.account_id = uuid(data, "account_id");
        reply.status = string(data, "status", 16);
        const std::string expected = op == "apply_team" ? "applied" :
            op == "withdraw_application" ? "withdrawn" :
            op == "approve_application" ? "approved" :
            op == "leave_team" ? "left" : op == "transfer_captain" ? "transferred" : "rejected";
        if(reply.team_id != input.team_id || reply.status != expected ||
           (!input.account_id.empty() && reply.account_id != input.account_id)) invalid();
    } else {
        keys(data, {"team"});
        const auto& value = data.at("team");
        if(op == "my_team" && value.is_null()) return;
        if(!value.is_object()) invalid();
        reply.team = team(value.as_object());
        if(op == "team_detail" && reply.team->id != input.team_id) invalid();
        if(op == "create_team" && (reply.team->name != input.name || reply.team->area_id != input.area_id)) invalid();
    }
}
Profile profile(const json::object& data) {
    const auto& value = object(data, "account");
    keys(value, {"id", "username", "database_generation", "heroes"});
    Profile result;
    result.account_id = uuid(value, "id");
    result.username = string(value, "username", 24);
    result.database_generation = uuid(value, "database_generation");
    if(!username_valid(result.username)) invalid();
    std::set<std::string> ids;
    for(const auto& item : array(value, "heroes", 256)) {
        if(!item.is_object()) invalid();
        const auto& entry = item.as_object();
        keys(entry, {"hero_id", "name", "hero_level", "experience", "total_experience", "revision", "forms"});
        Hero hero;
        hero.hero_id = string(entry, "hero_id", 80);
        hero.name = string(entry, "name", 128);
        hero.hero_level = wide_integer(entry, "hero_level", 1);
        hero.experience = wide_integer(entry, "experience");
        hero.total_experience = wide_integer(entry, "total_experience");
        hero.revision = wide_integer(entry, "revision");
        if(!catalog_id_valid(hero.hero_id) || !ids.insert(hero.hero_id).second || hero.experience > hero.total_experience) invalid();
        std::set<std::string> forms;
        for(const auto& form : array(entry, "forms", 128)) {
            if(!form.is_string()) invalid();
            const std::string id(form.as_string());
            if(id.size()>100 || !catalog_id_valid(id) || !forms.insert(id).second) invalid();
            hero.forms.push_back(id);
        }
        if(hero.forms.empty()) invalid();
        result.heroes.push_back(std::move(hero));
    }
    return result;
}
}

Battle parse_shared_battle_descriptor(const std::string& raw) { return battle(parse_document(raw)); }

bool catalog_id_valid(const std::string& value) {
    return !value.empty() && value.size() <= 128 && std::all_of(value.begin(), value.end(), [](unsigned char c) {
        return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '-' || c == '.';
    });
}

bool team_name_valid(const std::string& value) {
    auto alnum = [](char c) { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9'); };
    return value.size() >= 3 && value.size() <= 40 && alnum(value.front()) && value.back() != ' ' &&
        std::all_of(value.begin(), value.end(), [&](char c) { return alnum(c) || c == ' ' || c == '_' || c == '-'; });
}

const char* ProfileReadError::what() const noexcept {
    switch(failure_) {
    case ProfileReadFailure::file_unavailable:
        return "Alliance could not access its saved connection profile. [profile-file]";
    case ProfileReadFailure::file_read:
        return "Alliance could not read its saved connection profile. [profile-read]";
    case ProfileReadFailure::document_invalid:
        return "Alliance could not validate its saved connection profile format. [profile-format]";
    case ProfileReadFailure::identity_invalid:
        return "Alliance could not validate its saved server identity. [profile-identity]";
    case ProfileReadFailure::pin_mismatch:
        return "Alliance could not match its saved server certificate fingerprint. [profile-pin]";
    case ProfileReadFailure::certificate_rejected:
        return "Alliance could not verify its saved server certificate. [profile-certificate]";
    case ProfileReadFailure::crypto_failure:
        return "Alliance could not complete its local certificate checks. [profile-crypto]";
    case ProfileReadFailure::internal_failure:
        break;
    }
    return "Alliance could not check its saved connection profile. [profile-internal]";
}

TrustedProfile read_public_profile(const std::string& path) {
#ifdef _WIN32
    const auto raw = read_windows_profile(path);
#else
    // lstat rejects a final symlink in the trusted, app-private destination.
    struct stat info{};
    if(path.empty() || path.size() > 4096 || lstat(path.c_str(), &info) != 0
       || !S_ISREG(info.st_mode) || info.st_size < 1
       || static_cast<std::uint64_t>(info.st_size) > max_json_bytes)
        throw ProfileReadError(ProfileReadFailure::file_unavailable);
    std::ifstream stream(path, std::ios::binary);
    std::string raw(max_json_bytes + 1, '\0');
    stream.read(raw.data(), static_cast<std::streamsize>(raw.size()));
    raw.resize(static_cast<std::size_t>(stream.gcount()));
    if(stream.bad() || raw.empty() || raw.size() > max_json_bytes)
        throw ProfileReadError(ProfileReadFailure::file_read);
#endif
    TrustedProfile result;
    try {
        auto document = parse_document(raw);
        keys(document, {"format", "protocol", "server_address", "port", "minimum_tls",
                        "certificate_pem", "certificate_sha256"});
        if(integer(document, "format", 1, 1) != 1
           || string(document, "protocol", 32) != "wesnoth-alliance-tls"
           || string(document, "minimum_tls", 8) != "1.2") invalid();
        result.address = string(document, "server_address", 64);
        result.port = static_cast<std::uint16_t>(integer(document, "port", 1, 65535));
        result.certificate_pem = string(document, "certificate_pem", max_certificate_bytes);
        result.certificate_sha256 = string(document, "certificate_sha256", 64);
    } catch(const std::runtime_error&) {
        // Only the parser/schema validators' rejection is classified here.
        // Unexpected exceptions propagate to the UI's fixed fallback message.
        throw ProfileReadError(ProfileReadFailure::document_invalid);
    }
    const auto validation = validate_profile(result);
    switch(validation.error) {
    case Error::none: return result;
    case Error::invalid_profile:
        throw ProfileReadError(ProfileReadFailure::identity_invalid);
    case Error::pin_mismatch:
        throw ProfileReadError(ProfileReadFailure::pin_mismatch);
    case Error::certificate_rejected:
        throw ProfileReadError(ProfileReadFailure::certificate_rejected);
    case Error::internal_error:
        throw ProfileReadError(ProfileReadFailure::crypto_failure);
    default:
        throw ProfileReadError(ProfileReadFailure::internal_failure);
    }
}

Reply perform_request(const TrustedProfile& trusted, const Request& request,
                      const CancellationToken& cancel) {
    try {
        json::object body;
        const auto& op = request.operation;
        const bool is_world = world_protocol::operation(op);
        const bool is_progression = progression_protocol::operation(op);
        const bool is_team = team_operation(op);
        const bool is_casual = op == "casual_ticket" || op == "casual_select";
        const bool is_unified = unified_protocol::operation(op) && !battle_operation(op) && !is_casual;
        const bool is_extension = is_progression || is_unified;
        const bool is_battle = battle_operation(op);
        if(op != "select_plan_hero" && op != "battle_select_hero" && op != "casual_select" && (!request.hero_id.empty() || !request.form_id.empty())) return failure("invalid_input");
        if(op != "casual_select" && (!request.game_id.empty() || (op != "join_plan" && request.side != 0))) return failure("invalid_input");
        if(op != "leave_team" && request.allow_archive) return failure("invalid_input", is_team);
        if(op != "ready_plan" && op != "battle_ready" && request.ready) return failure("invalid_input", is_team);
        if(!is_battle && (!request.battle_id.empty() || !request.opponent_team_id.empty() ||
                         !request.map_id.empty() || !request.lineups.empty())) return failure("invalid_input", is_team);
        if(!is_team && (!request.name.empty() || !request.area_id.empty() ||
                       !request.team_id.empty() || !request.account_id.empty() || !request.after.empty() ||
                       !request.decision_id.empty())) return failure("invalid_input");
        if(!is_extension && !request.progression_json.empty()) return failure("invalid_input");
        if(!is_world && !request.world_json.empty()) return failure("invalid_input");
        if(is_world) {
            if(!token_valid(request.token) || !request.username.empty() || !request.password.empty() ||
               !request.starter_id.empty() || request.world_json.empty() || request.world_json.size()>65536) return failure("invalid_input");
            body=parse_document(request.world_json);
            world_protocol::request(op,body);
        } else if(is_casual) {
            if(!token_valid(request.token) || !request.username.empty() || !request.password.empty() || !request.starter_id.empty()) return failure("invalid_input");
            if(op == "casual_select") {
                if(!uuid_valid(request.game_id) ||
                   request.side < 1 || request.side > 99 || request.hero_id.size()>80 || request.form_id.size()>100 || !catalog_id_valid(request.hero_id) || !catalog_id_valid(request.form_id)) return failure("invalid_input");
                body = {{"game_id",request.game_id},{"side",request.side},{"hero_id",request.hero_id},{"form_id",request.form_id}};
            }
        } else if(is_extension) {
            if(!token_valid(request.token) || !request.username.empty() || !request.password.empty() ||
               !request.starter_id.empty() || request.progression_json.empty()) return failure("invalid_input");
            body = parse_document(request.progression_json);
            if(is_unified) unified_protocol::request(op, body);
            else progression_protocol::request(op, body);
        } else if(flexible_operation(op)) {
            if(!token_valid(request.token)||!request.username.empty()||!request.password.empty()||!request.starter_id.empty()||
               !request.name.empty()||!request.area_id.empty()||!request.after.empty()||!request.account_id.empty()||!request.decision_id.empty()||!request.lineups.empty())return team_failure("invalid_input");
            const bool publish=op=="publish_plan",edit=op=="edit_plan";
            if(publish) {
                if(!uuid_valid(request.team_id)||!uuid_valid(request.opponent_team_id)||request.team_id==request.opponent_team_id||!request.battle_id.empty())return team_failure("invalid_input");
                body["team_id"]=request.team_id;body["opponent_team_id"]=request.opponent_team_id;
            } else {
                if(!uuid_valid(request.battle_id)||request.revision<0||!request.team_id.empty()||!request.opponent_team_id.empty())return team_failure("invalid_input");
                body["battle_id"]=request.battle_id;body["revision"]=request.revision;
            }
            if(publish||edit) {
                const auto map=known_map(request.map_id);const auto& r=request.settings;
                if(r.gold<0||r.gold>1000||r.base_income<0||r.base_income>20||(r.turns!=-1&&(r.turns<1||r.turns>1000))||request.slots.empty()||request.slots.size()>8)return team_failure("invalid_input");
                body["map_id"]=map.id;body["settings"]=json::object{{"gold",r.gold},{"base_income",r.base_income},{"turns",r.turns},{"fog",r.fog},{"shroud",r.shroud},{"observers",r.observers}};
                json::array slots;std::set<int> sides;
                for(const auto& slot:request.slots) {
                    if(slot.side<1||slot.side>map.size*2||!sides.insert(slot.side).second||
                       (slot.kind!="open"&&slot.kind!="reserved"&&slot.kind!="ai"&&slot.kind!="closed")||
                       (!slot.account_id.empty()&&!uuid_valid(slot.account_id))||
                       (slot.kind=="reserved"&&slot.account_id.empty())||slot.ai_faction.size()>64||slot.ai_leader.size()>128)return team_failure("invalid_input");
                    if(slot.kind=="ai") {if(!slot.account_id.empty()||slot.ai_faction.empty()||slot.ai_leader.empty())return team_failure("invalid_input");}
                    else if(!slot.ai_faction.empty()||!slot.ai_leader.empty()||(slot.kind=="closed"&&!slot.account_id.empty()))return team_failure("invalid_input");
                    slots.push_back(json::object{{"side",slot.side},{"kind",slot.kind},{"account_id",slot.account_id},{"ai_faction",slot.ai_faction},{"ai_leader",slot.ai_leader}});
                }
                body["slots"]=std::move(slots);
            } else if(!request.map_id.empty()||!request.slots.empty())return team_failure("invalid_input");
            if(op=="join_plan") {if(request.side<1||request.side>8)return team_failure("invalid_input");body["side"]=request.side;}
            if(op=="ready_plan")body["ready"]=request.ready;
            if(op=="select_plan_hero") {
                if(!catalog_id_valid(request.hero_id)||request.hero_id.size()>80||!catalog_id_valid(request.form_id)||request.form_id.size()>100)return team_failure("invalid_input");
                body["hero_id"]=request.hero_id;body["form_id"]=request.form_id;
            }
        } else if(is_battle) {
            if(!token_valid(request.token) || !request.username.empty() || !request.password.empty() ||
               !request.starter_id.empty() || !request.name.empty() || !request.account_id.empty() ||
               !request.decision_id.empty()) return team_failure("invalid_input");
            const bool propose = op == "propose_battle";
            const bool uses_id = op == "battle_detail" || op == "approve_battle" ||
                op == "battle_ready" || op == "battle_select_hero" || op == "cancel_battle";
            if(op != "battle_maps" && !request.area_id.empty()) return team_failure("invalid_input");
            if(op != "my_battles" && !request.after.empty()) return team_failure("invalid_input");
            if(!uses_id && !request.battle_id.empty()) return team_failure("invalid_input");
            if(!propose && (!request.team_id.empty() || !request.opponent_team_id.empty() ||
                           !request.map_id.empty() || !request.lineups.empty())) return team_failure("invalid_input");
            if(op == "battle_select_hero") {
                if(request.hero_id.size()>80 || request.form_id.size()>100 || !catalog_id_valid(request.hero_id) || !catalog_id_valid(request.form_id)) return team_failure("invalid_input");
                body["hero_id"] = request.hero_id; body["form_id"] = request.form_id;
            }
            if(op == "battle_maps") {
                if(request.area_id.empty() || !cursor_valid(request.area_id)) return team_failure("invalid_input");
                body["area_id"] = request.area_id;
            }
            if(op == "my_battles") {
                if(!request.after.empty() && !uuid_valid(request.after)) return team_failure("invalid_input");
                body["after"] = request.after;
            }
            if(uses_id) {
                if(!uuid_valid(request.battle_id)) return team_failure("invalid_input");
                body["battle_id"] = request.battle_id;
            }
            if(op == "battle_ready") body["ready"] = request.ready;
            if(propose) {
                if(!uuid_valid(request.team_id) || !uuid_valid(request.opponent_team_id) ||
                   request.team_id == request.opponent_team_id || request.lineups.size() != 2 ||
                   (request.map_id != "isars-cross" && request.map_id != "hexcake" && request.map_id != "mokena-prairie"))
                    return team_failure("invalid_input");
                const auto map = known_map(request.map_id);
                json::array lineups;
                std::set<std::string> players;
                for(std::size_t i = 0; i < request.lineups.size(); ++i) {
                    const auto& lineup = request.lineups[i];
                    if(lineup.team_id != (i == 0 ? request.team_id : request.opponent_team_id) ||
                       lineup.account_ids.size() != static_cast<std::size_t>(map.size)) return team_failure("invalid_input");
                    json::array ids;
                    for(const auto& id : lineup.account_ids) {
                        if(!uuid_valid(id) || !players.insert(id).second) return team_failure("invalid_input");
                        ids.push_back(json::value(id));
                    }
                    lineups.push_back(json::object{{"team_id", lineup.team_id}, {"account_ids", std::move(ids)}});
                }
                body["team_id"] = request.team_id;
                body["opponent_team_id"] = request.opponent_team_id;
                body["map_id"] = request.map_id;
                body["lineups"] = std::move(lineups);
            }
        } else if(is_team) {
            if(!token_valid(request.token) || !request.username.empty() || !request.password.empty() ||
               !request.starter_id.empty() || !cursor_valid(request.after)) return team_failure("invalid_input");
            if(op != "create_team" && !request.name.empty()) return team_failure("invalid_input");
            if(op != "teams" && op != "create_team" && op != "standings" && !request.area_id.empty()) return team_failure("invalid_input");
            const bool uses_team = op == "team_detail" || op == "team_applicants" ||
                op == "leave_team" || op == "transfer_captain" || membership_mutation(op);
            const bool uses_account = op == "approve_application" || op == "reject_application" || op == "transfer_captain";
            const bool uses_after = op == "areas" || op == "teams" || op == "my_applications" || op == "team_applicants";
            if(!uses_team && !request.team_id.empty()) return team_failure("invalid_input");
            if(!uses_account && !request.account_id.empty()) return team_failure("invalid_input");
            if(!uses_after && !request.after.empty()) return team_failure("invalid_input");
            if(op != "ack_decision" && !request.decision_id.empty()) return team_failure("invalid_input");
            if(op == "ack_decision") {
                if(!uuid_valid(request.decision_id)) return team_failure("invalid_input");
                body["decision_id"] = request.decision_id;
            }
            if(op == "leave_team") body["allow_archive"] = request.allow_archive;
            if(uses_after) body["after"] = request.after;
            if(uses_account) {
                if(!uuid_valid(request.account_id)) return team_failure("invalid_input");
                body["account_id"] = request.account_id;
            }
            if(op == "teams" || op == "create_team" || op == "standings") {
                if(request.area_id.empty() || !cursor_valid(request.area_id)) return team_failure("invalid_input");
                body["area_id"] = request.area_id;
            }
            if(op == "create_team") {
                if(!team_name_valid(request.name)) return team_failure("invalid_input");
                body["name"] = request.name;
            }
            if(uses_team) {
                if(!uuid_valid(request.team_id)) return team_failure("invalid_input");
                body["team_id"] = request.team_id;
            }
        } else if(op == "register" || op == "login") {
            if(!username_valid(request.username) || !password_valid(request.password, op == "register")
               || !request.token.empty()) return failure("invalid_input");
            body["username"] = request.username;
            body["password"] = request.password;
            if(!request.starter_id.empty()) return failure("invalid_input");
        } else if(op == "me" || op == "logout") {
            if(!token_valid(request.token) || !request.username.empty()
               || !request.password.empty() || !request.starter_id.empty()) return failure("invalid_input");
        } else return failure("invalid_input");

        if(flexible_operation(op)) flexible_protocol::request(op,body);
        std::array<unsigned char, 16> random{};
        if(RAND_bytes(random.data(), random.size()) != 1) return failure("unavailable");
        constexpr char hex[] = "0123456789abcdef";
        std::string id;
        for(auto c : random) { id += hex[c >> 4]; id += hex[c & 15]; }
        json::object message{{"v", 4}, {"id", id}, {"op", op}, {"body", std::move(body)}};
        if(op == "me" || op == "logout" || is_team || is_extension || is_casual || is_world) message["token"] = request.token;
        auto frame = encode_message(message);
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
        auto remaining = [&]() {
            return std::chrono::duration_cast<std::chrono::milliseconds>(
                deadline - std::chrono::steady_clock::now());
        };
        Client client;
        auto result = client.connect(trusted, remaining(), cancel);
        if(!result) return transport_failure(result, is_team);
        if(remaining().count() <= 0) return failure("timeout", is_team);
        result = client.send_frame(frame, remaining(), cancel);
        if(!result) return transport_failure(result, is_team);
        if(remaining().count() <= 0) return failure("timeout", is_team);
        result = client.receive_frame(remaining(), cancel);
        if(!result) return transport_failure(result, is_team);
        auto response = decode_message(result.frame);
        keys(response, {"v", "id", "ok", "status", "data"});
        integer(response, "v", 4, 4);
        if(string(response, "id", 32) != id || !response.at("ok").is_bool()) invalid();
        int status = integer(response, "status", 200, 599);
        bool ok = response.at("ok").as_bool();
        if(ok != (status >= 200 && status < 300)) invalid();
        const auto& data = object(response, "data");
        if(!ok) {
            keys(data, {"code", "message"});
            auto code = string(data, "code", 64);
            string(data, "message", 256);
            if(is_extension || is_world) {
                auto reply = failure(status == 401 ? "invalid_session" : code);
                const auto public_message=is_world ? world_protocol::public_error(code) : progression_protocol::public_error(code);
                if(status!=401&&!public_message.empty()) reply.error_message=public_message;
                return reply;
            }
            if(is_team) {
                if(status == 401) return failure("invalid_session");
                if(status == 403) return team_failure("forbidden");
                return team_failure(code);
            }
            if(status == 401 || status == 403) {
                if(op == "me" || op == "logout" || code != "invalid_credentials")
                    code = "invalid_session";
            }
            return failure(code);
        }
        Reply reply;
        if(is_world) {
            if(status!=200) invalid();
            world_protocol::response(data);
            reply.world_json=json::serialize(data);
        } else if(is_casual) {
            if(status != 200) invalid();
            if(op == "casual_ticket") {
                keys(data, {"ticket", "username", "account_id", "database_generation", "expires_at"});
                reply.ticket = string(data, "ticket", 256);
                reply.username = string(data, "username", 24);
                reply.account_id = uuid(data, "account_id");
                reply.database_generation = uuid(data, "database_generation");
                reply.expires_at = string(data, "expires_at", 40);
                if(!username_valid(reply.username) || !token_valid(reply.ticket)) invalid();
            } else {
                unified_protocol::response(op,data);
                reply.selection_ticket = string(data, "selection_ticket", 43);
                if(!token_valid(reply.selection_ticket)) invalid();
                const auto& choice=object(data,"selection");
                BattlePlayer selected;
                selected.hero_id=string(choice,"hero_id",80);selected.form_id=string(choice,"form_id",100);
                selected.unit_type=string(choice,"unit_type",128);selected.level=integer(choice,"unit_level",0,20);
                selected.hero_level=wide_integer(choice,"hero_level",1);selected.hero_revision=wide_integer(choice,"hero_revision");
                selected.selection_present=true;
                if(selected.hero_id!=request.hero_id||selected.form_id!=request.form_id)invalid();
                reply.selection=std::move(selected);
            }
        } else if(is_extension) {
            if(status != 200) invalid();
            if(is_unified) unified_protocol::response(op, data);
            else progression_protocol::response(op, data);
            reply.progression_json = json::serialize(data);
        } else if(is_team) {
            if(status != (op == "create_team" || op == "propose_battle" || op == "publish_plan" ? 201 : 200)) invalid();
            if(is_battle) battle_response(data, request, reply);
            else team_response(data, request, reply);
        } else if(op == "logout") {
            keys(data, {"status"});
            if(status != 200 || string(data, "status", 16) != "logged_out") invalid();
        } else {
            if(op == "login") {
                keys(data, {"token", "account"});
                reply.token = string(data, "token", 43);
                if(!token_valid(reply.token)) invalid();
            } else keys(data, {"account"});
            if(status != (op == "register" ? 201 : 200)) invalid();
            reply.profile = profile(data);
        }
        reply.ok = true;
        return reply;
    } catch(...) { return failure("protocol", team_operation(request.operation)); }
}
}
