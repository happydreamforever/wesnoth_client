"""Source contracts for mutation consent; runtime networking is covered by TLS tests.

These guard the default action and actor binding without claiming a GUI run.
"""
from pathlib import Path
import unittest


SOURCE = (Path(__file__).resolve().parents[1] / "alliance_game/account_ui.cpp").read_text(encoding="utf-8")


def function(name, following):
    return SOURCE.split(name, 1)[1].split(following, 1)[0]


class AccountManagementUiTests(unittest.TestCase):
    def test_change_picker_defaults_to_back_and_requires_positive_selection(self):
        picker = function("int select_change(", "void application_decisions(")
        self.assertIn('std::vector<std::string> rows{"Back without changes"}', picker)
        self.assertIn("rows.insert(rows.end(), actions.begin(), actions.end())", picker)
        self.assertIn("return selected > 0 ? selected - 1 : -1", picker)
        selector = function("int select(", "int select_change(")
        self.assertIn("menu.set_selected_index(0)", selector)
        self.assertIn("menu.set_allow_plugin_skip(false)", selector)

    def test_captain_decisions_use_safe_picker_at_both_action_and_confirmation(self):
        review = function("void review_applications(", "void browse_teams(")
        self.assertIn('select_change("Review application", preview, {"Approve application", "Reject application"})', review)
        self.assertIn('select_change(approve ? "Confirm approval" : "Confirm rejection"', review)
        self.assertIn('const bool approve = action == 0', review)
        self.assertIn('if(action < 0 || action > 1) continue', review)

    def test_transfer_targets_current_other_members_by_name_and_binds_ids_privately(self):
        transfer = function("void transfer_captain(", "void leave_team(")
        self.assertIn("load_management_team(profile, team)", transfer)
        self.assertIn("team.captain_id != actor", transfer)
        self.assertIn("if(member.id == actor) continue", transfer)
        self.assertIn('select_change("Transfer captain"', transfer)
        self.assertIn('select_change("Confirm captain transfer"', transfer)
        self.assertIn('"\\n\\nNew captain: " + target.username', transfer)
        self.assertNotIn('"\\nPlayer ID: "', transfer)
        self.assertIn("input.team_id = team.id", transfer)
        self.assertIn("input.account_id = target.id", transfer)

    def test_leave_requires_archive_warning_and_binds_actor_and_server_consent(self):
        leave = function("void leave_team(", "void team_actions(")
        self.assertIn("if(captain && team.members.size() > 1)", leave)
        self.assertIn("Leaving will archive this team and clear its pending applications", leave)
        self.assertIn('select_change("Confirm leaving team"', leave)
        self.assertIn('"Leave and archive this team" : "Leave this team"', leave)
        self.assertIn("input.allow_archive = captain && team.members.size() == 1", leave)
        mutation = function("bool membership_request(", "bool load_management_team(")
        self.assertIn('operation == "leave_team") && output.account_id != actor', mutation)
        self.assertIn("membership_recovery(operation)", mutation)

    def test_success_and_failed_refresh_are_distinguished_and_rankings_are_top_twenty(self):
        refresh = function("void refresh_confirmed_team(", "void transfer_captain(")
        self.assertIn("The change was confirmed, but the new My team view could not be loaded", refresh)
        self.assertIn("Do not repeat the change to refresh it", refresh)
        rankings = function("void area_rankings(", "void create_team(")
        self.assertIn("Top 20 active teams by rating, highest first", rankings)
        self.assertIn("Use Browse areas and teams to see the full directory", rankings)
        self.assertIn('detail.operation = "team_detail"', rankings)
        self.assertNotIn("input.after", rankings)
        self.assertNotIn("captain-management actions will be added", SOURCE)


if __name__ == "__main__":
    unittest.main()
