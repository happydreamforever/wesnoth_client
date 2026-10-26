"""Production v3 native client versus disposable TLS peers; no live accounts."""
import copy
import unittest
from test_battle_client_responses import BattleClientResponseTests

class FlexibleClientTests(BattleClientResponseTests):
    @classmethod
    def flexible(cls):
        battle=cls.battle()
        battle.update(format=2,revision=1,settings=dict(gold=100,base_income=2,turns=-1,fog=True,shroud=False,observers=False),ranked=False,room_state='',generation='',engine_host_account_id='')
        for team in battle['teams']:
            team['slots']=[dict(side=p['side'],kind='open',account_id='',ai_faction='',ai_leader='') for p in team['players']]
            team['players']=[]
        return battle

    def test_incomplete_publish_roundtrip(self):
        battle=self.flexible()
        fields=dict(team_id=battle['teams'][0]['id'],opponent_team_id=battle['teams'][1]['id'],map_id=battle['map']['id'],settings=battle['settings'],slots=[s for t in battle['teams'] for s in t['slots']])
        output,received=self.exercise(self.team_reply({'battle':battle},201),self.team_request('publish_plan',**fields))
        self.assertTrue(output['ok']);self.assertEqual(received['v'],3);self.assertEqual(received['body'],fields)

    def test_open_human_and_computer_and_closed_projection(self):
        old=self.battle();battle=self.flexible()
        for t in range(2):
            player=copy.deepcopy(old['teams'][t]['players'][0]);player['ready']=True
            battle['teams'][t]['players']=[player];battle['teams'][t]['approved']=True
            slot=battle['teams'][t]['slots'][1];slot.update(kind='ai',ai_faction='Loyalists',ai_leader='Lieutenant')
        battle.update(room_state='preparing',generation='12345678-1234-4321-8765-0123456789ab',engine_host_account_id=battle['teams'][0]['players'][0]['id'])
        output,_=self.exercise(self.team_reply({'battle':battle}),self.team_request('battle_detail',battle_id=battle['id']))
        self.assertTrue(output['ok'])
        for t in range(2):battle['teams'][t]['slots'][1].update(kind='closed',ai_faction='',ai_leader='')
        output,_=self.exercise(self.team_reply({'battle':battle}),self.team_request('battle_detail',battle_id=battle['id']))
        self.assertTrue(output['ok'])

    def test_reject_slot_identity_and_rule_corruption(self):
        cases=[]
        for key,val in [('gold',1001),('base_income',-1),('base_income',21),('turns',0),('observers',1)]:
            b=self.flexible();b['settings'][key]=val;cases.append(b)
        b=self.flexible();b['teams'][0]['slots'][0]['kind']='guest';cases.append(b)
        b=self.flexible();b['teams'][0]['slots'][0]['side']=2;cases.append(b)
        b=self.flexible();b['teams'][0]['slots'][0]['account_id']=self.battle()['teams'][0]['players'][0]['id'];cases.append(b)
        b=self.flexible();b['teams'][0]['slots'][0].update(kind='ai',ai_faction='Loyalists',ai_leader='Ancient Lich');cases.append(b)
        for b in cases:
            with self.subTest(case=b):
                output,_=self.exercise(self.team_reply({'battle':b}),self.team_request('battle_detail',battle_id=b['id']))
                self.assertFalse(output['ok']);self.assertEqual(output['error_code'],'protocol')

    def test_version2_reply_rejected(self):
        def old(request):return {'v':2,'id':request['id'],'ok':True,'status':200,'data':{'battle':self.flexible()}}
        output,_=self.exercise(old,self.team_request('battle_detail',battle_id=self.flexible()['id']))
        self.assertFalse(output['ok']);self.assertEqual(output['error_code'],'protocol')

def load_tests(loader, tests, pattern):
    names=('test_incomplete_publish_roundtrip','test_open_human_and_computer_and_closed_projection',
           'test_reject_slot_identity_and_rule_corruption','test_version2_reply_rejected')
    return unittest.TestSuite(FlexibleClientTests(name) for name in names)

if __name__=='__main__':unittest.main()
