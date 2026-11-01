// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "alliance_game/native_menu_theme.hpp"
#include "help/help.hpp"
#include "help/help_impl.hpp"
#include "gui/dialogs/help_browser.hpp"
namespace alliance_game::mobile_help {
inline help::section topics() {
    help::section court;court.id="emerald_court";court.title="Emerald Court";
    court.topics.emplace_back("World and Teams","court_world",
        "Open Team to explore the World. Pinch to zoom, or use +, −, Centre and List. Join or create a Team to enter an active Area. My Team opens your Team’s home Area.\n\nTap a Castle to inspect its owner and current battle. Captured Castles receive one hour of protection. Daily attack and plan limits are shown before you commit. Captains manage membership, defense templates, treasury allowances and battle proposals.");
    court.topics.emplace_back("Crowns and Shop","court_shop",
        "Your profile shows available personal Crowns. Shop contains hero unlocks, consumables and requests for more Crowns. Requests add no balance until an administrator approves them. There is no payment form in the game.\n\nReview the current fee before confirming. Available and held Crowns are separate. Team donations and Team treasury spending do not change ownership of your personal heroes. Wallet records requests and transactions.");
    court.topics.emplace_back("Rooms and fees","court_rooms",
        "Room is the individual battle dashboard. Review the map, rules and current entry fee before joining. Choose an owned hero and set Ready. A battle begins only when every human seat is occupied and Ready. Rules or lineup changes require fresh readiness.\n\nRooms do not award permanent hero XP, Team XP or Castle ownership. A cancelled waiting admission releases its held fee under the server’s rules.");
    court.topics.emplace_back("Planned Team battles","court_plans",
        "Open a Plan Castle, then Plans. A captain chooses the opposing Team, delay, map and gold. Settings changes income, turn limit, visibility, observers, hero cap and item permission. Review before sending the proposal. The other captain must approve.\n\nMembers join the scheduled battle, choose their hero and faction, and set Ready. One player cannot join overlapping active battles. Enter appears when the server is preparing your battle. History retains completed results and available replays.");
    court.topics.emplace_back("Competitions","court_competitions",
        "Competition Castles show administrator-organized knockout events. During registration, a captain reviews the shared rules and Team entry fee before entering. The bracket shows each round and opponent. All matches in a round must finish before the event advances under the administrator’s schedule.\n\nOpen the event’s Battles to join your Team’s match. A bye advances a Team without awarding a played-battle reward.");
    court.topics.emplace_back("Battle items and reconnecting","court_items",
        "When the published rules allow items, select one owned consumable before Ready. During your own turn, tap Actions, then Use Healing Draught or Use Remedy. The item acts on your living hero and can be used once in that battle. Healing restores up to 12 HP; Remedy removes poison and slow. No long press is required.\n\nIf a required local service is lost, return to Login and sign in again. Resume the same battle from its Castle or battle list. A disconnect alone never grants a win. Internet/WAN disconnection alone does not end a local session.");
    return court;
}
inline void show() {
    MenuThemeScope theme("alliance_court_help");
    const auto manager=help::help_manager::get_instance();
    auto root=manager->regenerate();
    root.sections.push_front(topics());
    gui2::dialogs::help_browser::display(root,"court_world");
}
}
