"""Planning source guards and actual controllers with scripted request/modal boundaries.

The C++ fixture exercises the shipped detail/rendering functions. It does not
render Android widgets, contact a live server or execute the game engine.
"""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from hero_catalog_fixture import catalogue_source

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "alliance_game/account_ui.cpp").read_text(encoding="utf-8")


def function(name, following):
    return SOURCE.split(name, 1)[1].split(following, 1)[0]


def extract(signature):
    start = SOURCE.index(signature)
    opening = SOURCE.index("\n{", start) + 1
    end, depth = opening + 1, 1
    while depth:
        depth += (SOURCE[end] == "{") - (SOURCE[end] == "}")
        end += 1
    return SOURCE[start:end]


class BattlePlanningUiTests(unittest.TestCase):
    def test_actual_refresh_and_history_controllers(self):
        compiler = shutil.which(os.environ.get("CXX", "c++"))
        self.assertIsNotNone(compiler, "A C++17 compiler is required for planning UI behavior")
        signatures = ("int select_change(", "std::string battle_validity_text(", "std::string practice_availability_text(",
                      "std::string hero_form_text(", "std::string battle_text(", "bool can_practice_plan(",
                      "void battle_detail(", "void my_battles(")
        functions = "\n\n".join(extract(signature) for signature in signatures)
        template = ROOT / "tests/battle_planning_ui_fixture.cpp.in"
        fixture = template.read_text(encoding="utf-8").replace("// @FUNCTIONS@", functions)
        audit = os.environ.get("ALLIANCE_PLANNING_UI_AUDIT")
        with tempfile.TemporaryDirectory(prefix="alliance-planning-ui-") as temporary:
            directory = Path(audit) if audit else Path(temporary)
            directory.mkdir(parents=True, exist_ok=True)
            cpp, binary = directory / "planning_ui.cpp", directory / "planning_ui"
            cpp.write_text(fixture, encoding="utf-8")
            policy = directory / "practice_policy.o"
            policy_command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-O0", "-g",
                "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-I" + str(ROOT),
                "-Dqueue_practice=unused_queue_practice", "-Dpractice_pending=unused_practice_pending",
                "-Dtake_pending_practice=unused_take_pending_practice", "-c",
                str(ROOT / "alliance_game/practice_plan.cpp"), "-o", str(policy)]
            compiled = subprocess.run(policy_command, capture_output=True, text=True, timeout=120)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            catalogue = directory / "catalogue.cpp"
            includes = catalogue_source(ROOT, catalogue)
            command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-O0", "-g",
                       "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-I" + str(ROOT),
                       str(cpp), str(policy), str(catalogue), str(ROOT / "alliance_game/team_views.cpp"), *includes, "-o", str(binary)]
            built = subprocess.run(command, capture_output=True, text=True, timeout=120)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("PASS all planning refresh and history cases", ran.stdout)
            print(ran.stdout, end="")
            if audit:
                (directory / "behavior.log").write_text(ran.stdout + ran.stderr, encoding="utf-8")
                paths = ("alliance_game/account_ui.cpp", "alliance_native/account_client.hpp",
                         "tests/battle_planning_ui_fixture.cpp.in", "tests/test_battle_planning_ui.py",
                         "alliance_game/practice_plan.cpp", "alliance_game/practice_plan.hpp")
                (directory / "source-evidence.json").write_text(json.dumps({
                    "command": command, "policy_command": policy_command, "exit_code": ran.returncode,
                    "cases": ran.stdout.count("PASS ") - 1,
                    "source_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths},
                    "limitations": __doc__,
                }, indent=2) + "\n", encoding="utf-8")

    def test_local_lineup_selection_is_bounded_unique_ordered_and_has_safe_back(self):
        body = function("bool choose_battle_lineup(", "std::string battle_validity_text(")
        self.assertIn('view.title="Choose lineup — "', body)
        self.assertIn('selected.size()==sides.size()', body)
        self.assertIn('if(picked.action==1&&selected.size()==sides.size())return true', body)
        self.assertIn('selected.erase(found)', body)
        self.assertIn('if(selected.size()>=sides.size())', body)
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
        self.assertIn('proposal_view.actions={{"Send battle proposal"}}', proposal)
        self.assertIn('show_battle_view(proposal_view).action!=0||!same_extension_actor(actor)', proposal)
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
        self.assertIn('draft && captain && !approved && all_heroes && battle.validity == "valid"', detail)
        self.assertIn('draft && selected && hero_selected && (ready || (both_approved && battle.validity == "valid"))', detail)
        self.assertIn('draft && captain ? ', detail)
        self.assertIn('show_battle_view(view)', detail)
        self.assertIn('confirmation.actions={{affirmative}}', detail)
        self.assertIn('show_battle_view(confirmation).action!=0||!same_extension_actor(actor)', detail)
        for label in ('Confirm battle approval', 'Approve saved map and lineups', 'Confirm ready',
                      'Confirm not ready', 'Confirm battle cancellation', 'Cancel this draft plan'):
            self.assertIn(label, detail)
        self.assertIn('The change was confirmed, but the latest plan could not be loaded', detail)

    def test_planning_has_no_shared_start_or_reward_mutation(self):
        body = function("bool choose_battle_lineup(", "void teams(")
        for forbidden in ('"start_battle"', '"finish_battle"', '"start_campaign"', '"advance_leader"'):
            self.assertNotIn(forbidden, body)
        self.assertIn('Practice grants no account experience, team experience or rating', body)
        self.assertIn('view.title="My battle plans"', body)
        self.assertIn('map_view.title="Choose battle map"', body)


if __name__ == "__main__":
    unittest.main()
