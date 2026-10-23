"""Exercise production C++ extension schema through real pinned TLS connections."""
from copy import deepcopy
import json
import os
import unittest
from test_account_client_responses import AccountClientResponseTests as Fixtures

@unittest.skipUnless(os.environ.get('ALLIANCE_ACCOUNT_RESPONSE_PROBE'),'Requires native account response probe')
class ExtensionTests(unittest.TestCase):
    setUpClass=classmethod(Fixtures.setUpClass.__func__)
    tearDownClass=classmethod(Fixtures.tearDownClass.__func__)
    profile=Fixtures.profile
    invoke=Fixtures.invoke
    peer=Fixtures.peer
    exercise=Fixtures.exercise
    login_request=staticmethod(Fixtures.login_request)
    success=classmethod(Fixtures.success.__func__)
    account=staticmethod(Fixtures.account)
    identifier='12345678-1234-4321-8765-0123456789ab'
    def request(self,op,body):return {'operation':op,'token':'b'*43,'progression_json':json.dumps(body)}
    def response(self,body):
        return lambda request:{'v':1,'id':request['id'],'ok':True,'status':200,'data':body}
    def run_record(self,state='running',history=False):
        return dict(id=self.identifier,account_id=self.identifier,username='Player',mission_id='alliance_training_1',
            mission_name='Training',mission_version='progression-campaign-v1',ruleset_version='alliance-v1',
            state=state,unit_type='Spearman',leader_level=1,nonce='a'*64 if state=='running' and not history else '',
            created_at='2026-10-23T01:02:03Z',finished_at='' if state=='running' else '2026-10-23T01:03:03Z',
            outcome='win' if state=='completed' else '',experience_awarded=20 if state=='completed' else 0)
    def profile_record(self):
        return dict(id=self.identifier,username='Player',starter_id='spearman',unit_type='Spearman',level=1,
            experience=20,total_experience=20,advancement_choices=[])
    def cases(self):
        order=dict(id=self.identifier,author_id=self.identifier,author_name='Player',text='Hold north\nGuard the captain.',created_at='2026-10-23T01:02:03Z')
        review=dict(run_id=self.identifier,state='running',review_state='awaiting_captains',candidate_digest='a'*64,
            winner_team_id=self.identifier,draw=False,reports_received=4,required_reports=4,captains_approved=1,revision=3)
        return [
          ('progression_catalog',{},dict(profile=self.profile_record(),missions=[dict(id=f'alliance_training_{i}',name=f'Mission {i}',
              version='progression-campaign-v1',unlocked=i==1,completed=False,victory_experience=20) for i in (1,2,3)],active_run=None)),
          ('progression_start',{'mission_id':'alliance_training_1'},{'run':self.run_record()}),
          ('progression_status',{'run_id':self.identifier},{'run':self.run_record()}),
          ('progression_cancel',{'run_id':self.identifier},{'run':self.run_record('cancelled')}),
          ('progression_finish',dict(run_id=self.identifier,mission_id='alliance_training_1',mission_version='progression-campaign-v1',
              nonce='a'*64,outcome='win',replay_sha256='b'*64,format='alliance-native-campaign-result-v1'),
              dict(run=self.run_record('completed'),profile=self.profile_record(),experience_awarded=20)),
          ('progression_advance',{'unit_type':'Swordsman'},{'profile':self.profile_record()}),
          ('progression_history',{},dict(runs=[self.run_record(history=True)],has_more=False)),
          ('coordination_orders',{'team_id':self.identifier},dict(team_id=self.identifier,team_name='Team',orders=[order],has_more=False)),
          ('coordination_post',{'team_id':self.identifier,'text':order['text']},{'order':order}),
          ('shared_review',{'run_id':self.identifier},review),
          ('shared_confirm',{'run_id':self.identifier,'candidate_digest':'a'*64},review)]
    def test_all_extensions_roundtrip_and_exact_bodies(self):
        for op,body,result in self.cases():
            with self.subTest(op=op):
                output,request=self.exercise(self.response(result),self.request(op,body))
                self.assertTrue(output['ok']);self.assertEqual(request['op'],op);self.assertEqual(request['body'],body)
    def test_response_extra_fields_rejected(self):
        for op,body,result in self.cases():
            result=deepcopy(result);result['unexpected_secret']='must-not-be-projected'
            with self.subTest(op=op):
                output,_=self.exercise(self.response(result),self.request(op,body))
                self.assertFalse(output['ok']);self.assertEqual(output['error_code'],'protocol')
    def test_request_extra_fields_never_connect(self):
        for op,body,_ in self.cases():
            body=deepcopy(body);body['award_experience']=9999
            with self.subTest(op=op),self.peer() as (port,state):
                output=self.invoke(self.profile(port),self.request(op,body))
                self.assertFalse(output['ok']);self.assertEqual(state['connections'],0)
    def test_invalid_rewards_nonces_history_and_review_bounds(self):
        invalid=[('progression_finish',dict(run=self.run_record('completed'),profile=self.profile_record(),experience_awarded=1000)),
                 ('progression_history',dict(runs=[self.run_record()],has_more=False))]
        review=deepcopy(self.cases()[-1][2]);review['captains_approved']=3;invalid.append(('shared_review',review))
        for op,result in invalid:
            body=next(body for name,body,_ in self.cases() if name==op)
            output,_=self.exercise(self.response(result),self.request(op,body));self.assertFalse(output['ok'])

if __name__=='__main__':unittest.main()
