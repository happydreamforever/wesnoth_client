"""Production v3 error handling over disposable TLS; no live account state."""
import unittest

from test_flexible_client_responses import FlexibleClientTests


class ProductProtocolErrorTests(FlexibleClientTests):
    def test_plan_errors_retain_actionable_codes_without_expiring_session(self):
        battle = self.flexible()
        fields = dict(team_id=battle['teams'][0]['id'],
                      opponent_team_id=battle['teams'][1]['id'],
                      map_id=battle['map']['id'], settings=battle['settings'],
                      slots=[slot for team in battle['teams'] for slot in team['slots']])
        for code in ('human_required', 'limit_reached', 'stale_revision',
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
        'test_plan_errors_retain_actionable_codes_without_expiring_session',
        'test_unauthorized_plan_error_still_invalidates_session'))


if __name__ == '__main__':
    unittest.main()
