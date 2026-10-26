"""Execute the production Team hub/list controllers with scripted account and GUI boundaries."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def extract(source, signature):
    start = source.index(signature)
    opening = source.index('\n{', start) + 1
    end, depth = opening + 1, 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


class TeamHub(unittest.TestCase):
    def test_filtered_history_pagination_navigation_and_session_fences(self):
        source = (ROOT / 'alliance_game/account_ui.cpp').read_text()
        production = '\n'.join(extract(source, name) for name in (
            'bool my_team(', 'void my_battles(', 'void battle_plans(',
            'void battle_history(', 'void team_options_hub(', 'void teams('))
        fixture = r'''
#include "alliance_native/account_client.hpp"
#include "alliance_game/battle_ui.hpp"
#include <algorithm>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
namespace account=alliance::account;
namespace alliance_game {
void need(bool value,const char* label){if(!value)throw std::runtime_error(label);}
struct Session{std::string token;account::Profile profile;}session;
Session& current_session(){return session;}
std::vector<std::string> requests,opened,actions;
std::vector<BattleView> views;
std::function<account::Reply(const account::Request&)> transport;
std::function<BattleResult(const BattleView&)> modal;
bool pending=false;
bool team_request(const alliance::TrustedProfile&,const account::Request& input,const std::string&,account::Reply& output){
 need(input.operation=="my_team"||input.operation=="my_battles","Unexpected mutation/request");
 requests.push_back(input.operation+":"+input.after);output=transport(input);return output.ok;
}
BattleResult show_battle_view(const BattleView& view){need(view.actions.size()<=8,"Too many hub actions");views.push_back(view);return modal(view);}
BattleView team_roster_view(const account::Team& team,const std::string&){BattleView view;view.title="Roster "+team.name;view.rows={{"Member","","","",{}}};return view;}
void message(const std::string&){}
void battle_detail(const alliance::TrustedProfile&,const std::string& id){opened.push_back(id);pending=true;}
bool practice_pending(){return false;}bool progression_pending(){return false;}
bool shared_pending(){return pending;}bool replay_pending(){return false;}
void action(const char* label){actions.push_back(label);pending=true;}
void captain_orders(const alliance::TrustedProfile&){action("orders");}
void team_applications_hub(const alliance::TrustedProfile&,bool captain){action(captain?"captain-applications":"member-applications");}
void browse_areas(const alliance::TrustedProfile&){action("find");}
void area_rankings(const alliance::TrustedProfile&){action("rankings");}
void team_management_hub(const alliance::TrustedProfile&,const account::Team&){action("manage");}
void create_team(const alliance::TrustedProfile&){action("create");}
void propose_battle(const alliance::TrustedProfile&){action("publish");}
// PRODUCTION
void reset(){session={};session.token="token";session.profile.account_id="actor";session.profile.database_generation="database";requests.clear();opened.clear();actions.clear();views.clear();pending=false;}
account::BattleSummary battle(const char* id,const char* state){account::BattleSummary b;b.id=id;b.state=state;b.created_at="2026-10-26T00:00:00Z";b.teams={{"a","North"},{"b","South"}};return b;}
account::Reply page(){account::Reply r;r.ok=true;r.battles={battle("done","completed"),battle("draft","draft"),battle("live","running"),battle("cancelled","cancelled")};return r;}
account::Reply member(bool captain=true){account::Reply r;r.ok=true;account::Team t;t.id="a";t.name="North";t.captain_id=captain?"actor":"captain";r.team=t;return r;}
void check_labels(const BattleView& v){const std::vector<std::string> expected={"Plans","History","Roster","New battle","More","Refresh"};need(v.actions.size()==expected.size(),"Six explicit hub actions");for(std::size_t i=0;i<expected.size();++i)need(v.actions[i].label==expected[i],"Hub label differs");}
}
int main(){using namespace alliance_game;try{
 reset();transport=[](const auto&){return page();};modal=[](const auto& v){need(v.title=="Team plans"&&v.rows.size()==2,"Active filter");return BattleResult{1,0};};my_battles({});need(opened==std::vector<std::string>{"live"},"Active filtered row mapping");
 reset();transport=[](const auto&){return page();};modal=[](const auto& v){need(v.title=="Team history"&&v.rows.size()==2,"History filter");return BattleResult{1,0};};battle_history({});need(opened==std::vector<std::string>{"cancelled"},"Cancelled history row mapping");
 reset();transport=[](const auto& q){auto r=page();if(q.after.empty()){r.battles={battle("draft","draft")};r.next="cursor";}return r;};int step=0;
 modal=[&](const auto& v){++step;if(step==1||step==3){need(!v.actions[0].enabled&&v.actions[2].enabled,"Filtered empty page lost Next");need(v.rows[0].title=="No history on this page","False global empty message");return BattleResult{0,2};}if(step==2){need(v.actions[1].enabled,"Missing Previous");return BattleResult{0,1};}return BattleResult{0,0};};
 battle_history({});need(requests==std::vector<std::string>{"my_battles:","my_battles:cursor","my_battles:","my_battles:cursor"},"History cursors");need(opened==std::vector<std::string>{"done"},"History after filtered page");
 reset();transport=[](const auto& q){auto r=page();if(q.after.empty())r.next="cursor";return r;};step=0;modal=[&](const auto&){++step;return BattleResult{0,step==1?2:step==2?3:-1};};my_battles({});need(requests==std::vector<std::string>{"my_battles:","my_battles:cursor","my_battles:cursor"},"Refresh changed current cursor");
 reset();transport=[](const auto& q){if(q.operation=="my_battles")return page();account::Reply r;r.ok=true;return r;};step=0;
 modal=[&](const auto& v){if(++step==1){check_labels(v);need(!v.actions[2].enabled&&!v.actions[3].enabled,"Nonmember roster/create battle");return BattleResult{0,1};}return BattleResult{0,0};};teams({});need(opened==std::vector<std::string>{"done"},"Former participant cannot open history");
 reset();transport=[](const auto&){return member();};step=0;modal=[&](const auto& v){++step;if(step==1){check_labels(v);return BattleResult{0,2};}if(step==2)need(v.title=="Roster North","Roster action route");return BattleResult{0,-1};};teams({});need(requests.size()==3&&views.size()==3,"Roster did not return to fresh hub");
 reset();transport=[](const auto&){return member();};step=0;modal=[&](const auto& v){if(++step==1){check_labels(v);return BattleResult{0,4};}need(v.title=="Team options"&&v.actions[0].label=="Captain orders","More lost orders");return BattleResult{0,0};};teams({});need(actions==std::vector<std::string>{"orders"},"Orders dispatch");
 reset();transport=[](const auto&){account::Reply r;r.ok=true;return r;};step=0;modal=[&](const auto& v){if(++step==1)return BattleResult{0,4};need(!v.actions[0].enabled&&v.actions[4].label=="Create team","Nonmember More permissions");return BattleResult{0,4};};teams({});need(actions==std::vector<std::string>{"create"},"Create team route");
 reset();transport=[](const auto&){return member(false);};step=0;modal=[&](const auto& v){need(!v.actions[3].enabled,"Member publish enabled");return BattleResult{0,++step==1?3:-1};};teams({});need(actions.empty(),"Disabled publish dispatched");
 for(int mode=0;mode<3;++mode){reset();transport=[mode](const auto&){if(mode==0)session.token="rotated";if(mode==1)session.profile.account_id="other";if(mode==2)session.profile.database_generation="other";return page();};modal=[](const auto&){throw std::runtime_error("Stale response displayed");return BattleResult{};};my_battles({});need(views.empty(),"Request identity fence");}
 for(int mode=0;mode<3;++mode){reset();transport=[](const auto&){return page();};modal=[mode](const auto&){if(mode==0)session.token="rotated";if(mode==1)session.profile.account_id="other";if(mode==2)session.profile.database_generation="other";return BattleResult{0,0};};battle_history({});need(opened.empty(),"Modal identity fence");}
 reset();transport=[](const auto&){return member();};modal=[](const auto&){session.token="rotated";return BattleResult{0,3};};teams({});need(actions.empty(),"Hub actor changed");
 reset();modal=[](const auto&){session.profile.database_generation="other";return BattleResult{0,4};};auto team=member();team_options_hub({},&*team.team);need(actions.empty(),"More actor changed");
 std::cout<<"PASS 17 Team hub controller groups: filtered rows, empty-page pagination, history, roster, More and session fences\n";return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
'''.replace('// PRODUCTION', production)
        compiler = shutil.which(os.environ.get('CXX', 'g++'))
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix='alliance-team-hub-') as temporary:
            directory = Path(os.environ.get('ALLIANCE_TEAM_HUB_AUDIT', temporary))
            directory.mkdir(parents=True, exist_ok=True)
            cpp, binary = directory/'team_hub.cpp', directory/'team_hub'
            cpp.write_text(fixture)
            built = subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                '-fsanitize=address,undefined', '-g', '-I'+str(ROOT), str(cpp), '-o', str(binary)],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(built.returncode, 0, built.stdout+built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            (directory/'behavior.log').write_text(ran.stdout+ran.stderr)
            self.assertEqual(ran.returncode, 0, ran.stdout+ran.stderr)
            print(ran.stdout, end='')


if __name__ == '__main__':
    unittest.main()
