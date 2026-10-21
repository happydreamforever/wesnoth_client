"""Consent and scope guards in the shipped game source; not GUI runtime tests."""
from pathlib import Path
import unittest

SOURCE = (Path(__file__).resolve().parents[1] / "alliance_game/account_ui.cpp").read_text(encoding="utf-8")


def function(name, following):
    return SOURCE.split(name, 1)[1].split(following, 1)[0]


class BattlePlanningUiTests(unittest.TestCase):
    def test_local_lineup_selection_is_bounded_unique_ordered_and_has_safe_back(self):
        body = function("bool choose_battle_lineup(", "std::string battle_validity_text(")
        self.assertIn('select_change("Choose lineup — "', body)
        self.assertIn('selected.size() == sides.size()', body)
        self.assertIn('if(choice == use) return true', body)
        self.assertIn('selected.erase(it)', body)
        self.assertIn('if(selected.size() >= sides.size())', body)
        self.assertIn('std::to_string(sides[i])', body)
        self.assertNotIn('request(', body)

    def test_proposal_uses_same_area_opponent_and_explicit_both_lineup_confirmation(self):
        choose = function("bool choose_battle_opponent(", "void propose_battle(")
        self.assertIn('input.area_id = own.area_id', choose)
        self.assertIn('team.id == own.id || team.member_count < map.size', choose)
        self.assertIn('loaded.team->area_id != own.area_id', choose)
        proposal = function("void propose_battle(", "void battle_plans(")
        self.assertIn('own.captain_id != current_session().profile.account_id', proposal)
        self.assertIn('choose_battle_lineup(own, map.sides[0]', proposal)
        self.assertIn('choose_battle_lineup(opponent, map.sides[1]', proposal)
        self.assertIn('select_change("Confirm battle proposal", preview, {"Send battle proposal"})', proposal)
        self.assertIn('Both captains must then review and explicitly approve', proposal)
        self.assertIn('confirmed.area_id != own.area_id', proposal)

    def test_mutation_acknowledgements_bind_proposer_captain_and_own_readiness(self):
        mutation = function("bool battle_mutation(", "void battle_detail(")
        self.assertIn('const std::string actor = current_session().profile.account_id', mutation)
        self.assertIn('operation == "propose_battle"', mutation)
        self.assertIn('output.battle->teams[0].captain_id == actor', mutation)
        self.assertIn('team.captain_id == actor && team.approved', mutation)
        self.assertIn('player.id == actor && player.ready == ready', mutation)
        self.assertIn('No battle request was automatically retried', mutation)
        self.assertNotIn('while(', mutation)

    def test_detail_actions_and_confirmations_default_back_and_require_draft_roles(self):
        detail = function("void battle_detail(", "void my_battles(")
        self.assertIn('draft && captain && !approved && battle.validity == "valid"', detail)
        self.assertIn('draft && selected && (ready || (both_approved && battle.validity == "valid"))', detail)
        self.assertIn('draft && captain ? ', detail)
        self.assertIn('select_change("Battle plan"', detail)
        self.assertIn('select_change(title, battle_text(battle)', detail)
        for label in ('Confirm battle approval', 'Approve saved map and lineups', 'Confirm ready',
                      'Confirm not ready', 'Confirm battle cancellation', 'Cancel this draft plan'):
            self.assertIn(label, detail)
        self.assertIn('The change was confirmed, but the latest plan could not be loaded', detail)

    def test_planning_has_no_start_combat_or_reward_mutation(self):
        body = function("bool choose_battle_lineup(", "void teams(")
        for forbidden in ('"start_battle"', '"finish_battle"', '"start_campaign"', '"advance_leader"'):
            self.assertNotIn(forbidden, body)
        self.assertIn('No battle starts and no experience or rating is awarded', body)
        self.assertIn('"My battle plans", "Propose battle"', body)


if __name__ == "__main__":
    unittest.main()
