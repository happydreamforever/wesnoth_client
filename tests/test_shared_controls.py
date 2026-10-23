"""Execute the production captain Lua with scripted GUI/engine boundaries.

These are consent/command semantics tests. Actual synchronized engine END and
replay behavior is covered separately by the shared-engine integration.
"""
import ctypes
import ctypes.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CaptainControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ctypes.util.find_library('lua5.4')
        if not path:
            raise RuntimeError('Lua 5.4 is required for captain control verification.')
        cls.lua = ctypes.CDLL(path)
        cls.lua.luaL_newstate.restype = ctypes.c_void_p
        cls.lua.luaL_openlibs.argtypes = [ctypes.c_void_p]
        cls.lua.luaL_loadstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        cls.lua.luaL_loadstring.restype = ctypes.c_int
        cls.lua.lua_pcallk.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_longlong, ctypes.c_void_p]
        cls.lua.lua_pcallk.restype = ctypes.c_int
        cls.lua.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        cls.lua.lua_tolstring.restype = ctypes.c_char_p
        cls.lua.lua_close.argtypes = [ctypes.c_void_p]
        source = (ROOT / 'alliance_game/shared_controls.cpp').read_text()
        cls.production = source.split('R"LUA(', 1)[1].split(')LUA"', 1)[0]

    def run_lua(self, check):
        stubs = """
local captain_side={[1]=true,[2]=true}
local side_team={[1]=1,[2]=2,[3]=2,[4]=1}
local teams={'left-team','right-team'}
choice=1; sent={}; endings={}; menus=0
wml={variables={alliance_shared_run_id='bound-run'},tag={result=function(v)return {'result',v}end}}
wesnoth={current={side=1,user_can_invoke_commands=true},sides={{is_local=true},{is_local=true},{is_local=true},{is_local=true}},custom_synced_commands={},wml_actions={},sync={}}
wesnoth.wml_actions.endlevel=function(cfg)endings[#endings+1]=cfg end
wesnoth.sync.invoke_command=function(name,cfg)
 sent[#sent+1]={name=name,action=cfg.action}
 wesnoth.custom_synced_commands[name](cfg)
end
gui={show_menu=function(rows,initial,markup)
 menus=menus+1;assert(initial==1 and markup==false)
 assert(rows[1].label=='Back - keep playing')
 if during_menu then during_menu() end
 return choice
end}
"""
        state = self.lua.luaL_newstate()
        self.assertTrue(state)
        try:
            self.lua.luaL_openlibs(state)
            code = (stubs + self.production + '\n' + check).encode()
            result = self.lua.luaL_loadstring(state, code)
            if not result:
                result = self.lua.lua_pcallk(state, 0, 0, 0, 0, None)
            self.assertEqual(result, 0, self.lua.lua_tolstring(state, -1, None))
        finally:
            self.lua.lua_close(state)

    def test_back_escape_default_never_emit_or_mutate(self):
        self.run_lua("""
for _,action in ipairs{'surrender','offer_draw','withdraw_draw'}do
 for _,answer in ipairs{0,1,-1}do choice=answer;alliance_captain_prompt(action)end
end
assert(#sent==0 and #endings==0 and not wml.variables.alliance_terminal_kind)
""")

    def test_capability_and_actor_rechecked_after_confirmation(self):
        for mutation in ('wesnoth.current.side=2', 'wesnoth.sides[1].is_local=false',
                         'wesnoth.current.user_can_invoke_commands=false'):
            with self.subTest(mutation=mutation):
                self.run_lua('choice=2;during_menu=function()' + mutation +
                             ' end;alliance_captain_prompt("surrender");assert(#sent==0 and #endings==0)')

    def test_non_captain_remote_or_locked_side_cannot_open_confirmation(self):
        for mutation in ('wesnoth.current.side=3', 'wesnoth.sides[1].is_local=false',
                         'wesnoth.current.user_can_invoke_commands=false'):
            with self.subTest(mutation=mutation):
                self.run_lua(mutation+';choice=2;alliance_captain_prompt("surrender");assert(menus==0 and #sent==0)')

    def test_confirmed_surrender_binds_opposite_team_and_native_side_results(self):
        for side, winner in ((1, 'right-team'), (2, 'left-team')):
            with self.subTest(side=side):
                self.run_lua(f"""
wesnoth.current.side={side};choice=2;alliance_captain_prompt('surrender')
assert(#sent==1 and sent[1].name=='alliance_captain_v1' and sent[1].action=='surrender')
assert(wml.variables.alliance_terminal_kind=='surrender' and wml.variables.alliance_terminal_draw==false)
assert(wml.variables.alliance_terminal_run_id=='bound-run' and wml.variables.alliance_terminal_winner_team_id=='{winner}')
assert(#endings==1 and endings[1].replay_save==false and endings[1].linger_mode==false)
for _,result in ipairs(endings[1])do
 local expected=side_team[result[2].side]==side_team[{side}] and 'defeat' or 'victory'
 assert(result[1]=='result' and result[2].result==expected)
end
assert(not pcall(wesnoth.custom_synced_commands.alliance_captain_v1,{{action='surrender'}}))
""")

    def test_draw_requires_two_captains_with_withdrawal_and_replayable_end(self):
        self.run_lua("""
choice=2;alliance_captain_prompt('offer_draw');assert(#endings==0)
alliance_captain_prompt('withdraw_draw');assert(not wml.variables.alliance_draw_offer_1)
wesnoth.current.side=2;alliance_captain_prompt('offer_draw');assert(#endings==0)
wesnoth.current.side=1;alliance_captain_prompt('offer_draw');assert(#sent==4 and #endings==1)
assert(wml.variables.alliance_terminal_kind=='draw' and wml.variables.alliance_terminal_draw==true)
assert(wml.variables.alliance_terminal_winner_team_id=='' and wml.variables.alliance_terminal_run_id=='bound-run')
for _,result in ipairs(endings[1])do assert(result[2].result=='victory')end
""")

    def test_invalid_commands_never_end_battle(self):
        self.run_lua("""
assert(not pcall(wesnoth.custom_synced_commands.alliance_captain_v1,{action='reward'}))
wesnoth.current.side=3
assert(not pcall(wesnoth.custom_synced_commands.alliance_captain_v1,{action='surrender'}))
assert(#endings==0 and not wml.variables.alliance_terminal_kind)
""")


if __name__ == '__main__':
    unittest.main()
