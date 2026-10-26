"""Production v3 error handling over disposable TLS; no live account state."""
import unittest
from pathlib import Path
import re
import zipfile

from test_flexible_client_responses import FlexibleClientTests


class ProductProtocolErrorTests(FlexibleClientTests):
    def test_all_pinned_default_ai_leaders_are_accepted(self):
        archive = Path(__file__).resolve().parents[1] / 'build/android-game-data/main.1192600.org.wesnoth.offline.obb'
        choices = []
        with zipfile.ZipFile(archive) as data:
            for name in sorted(data.namelist()):
                if name.startswith('data/multiplayer/factions/') and name.endswith('-default.cfg'):
                    source = data.read(name).decode('utf-8')
                    faction = re.search(r'^\s*id=([^\n]+)$', source, re.MULTILINE).group(1).strip()
                    leaders = re.search(r'^\s*leader=([^\n]+)$', source, re.MULTILINE).group(1).strip().split(',')
                    choices.extend((faction, leader.strip()) for leader in leaders)
        self.assertEqual(len(choices), 43)
        self.assertEqual(len({faction for faction, _ in choices}), 7)
        for faction, leader in choices:
            with self.subTest(faction=faction, leader=leader):
                battle = self.flexible()
                battle['teams'][0]['slots'][0].update(kind='ai', ai_faction=faction, ai_leader=leader)
                output, _ = self.exercise(self.team_reply({'battle': battle}),
                    self.team_request('battle_detail', battle_id=battle['id']))
                self.assertTrue(output['ok'])
                self.assertTrue(output['has_battle'])

    def test_ai_leader_from_different_faction_is_rejected(self):
        battle = self.flexible()
        battle['teams'][0]['slots'][0].update(kind='ai', ai_faction='Loyalists', ai_leader='Dwarvish Steelclad')
        output, _ = self.exercise(self.team_reply({'battle': battle}),
            self.team_request('battle_detail', battle_id=battle['id']))
        self.assertFalse(output['ok'])
        self.assertEqual(output['error_code'], 'protocol')

    def test_plan_errors_retain_actionable_codes_without_expiring_session(self):
        battle = self.flexible()
        fields = dict(team_id=battle['teams'][0]['id'],
                      opponent_team_id=battle['teams'][1]['id'],
                      map_id=battle['map']['id'], settings=battle['settings'],
                      slots=[slot for team in battle['teams'] for slot in team['slots']])
        for code in ('human_required', 'limit_reached', 'stale_revision', 'unbalanced_sides',
                     'slot_taken', 'update_required', 'invalid_lineup', 'hero_locked', 'form_locked'):
            with self.subTest(code=code):
                def rejected(request):
                    return {'v': 3, 'id': request['id'], 'ok': False, 'status': 409,
                            'data': {'code': code, 'message': 'untrusted-private-server-text'}}
                output, received = self.exercise(rejected, self.team_request('publish_plan', **fields))
                self.assertEqual(received['v'], 3)
                self.assertFalse(output['ok'])
                self.assertEqual(output['error_code'], code)
                self.assertFalse(output['has_token'])

    def test_unauthorized_plan_error_still_invalidates_session(self):
        def rejected(request):
            return {'v': 3, 'id': request['id'], 'ok': False, 'status': 401,
                    'data': {'code': 'human_required', 'message': 'untrusted-private-server-text'}}
        output, _ = self.exercise(rejected, self.team_request('battle_detail', battle_id=self.flexible()['id']))
        self.assertFalse(output['ok'])
        self.assertEqual(output['error_code'], 'invalid_session')


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(ProductProtocolErrorTests(name) for name in (
        'test_all_pinned_default_ai_leaders_are_accepted',
        'test_ai_leader_from_different_faction_is_rejected',
        'test_plan_errors_retain_actionable_codes_without_expiring_session',
        'test_unauthorized_plan_error_still_invalidates_session'))


if __name__ == '__main__':
    unittest.main()
