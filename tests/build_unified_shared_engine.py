"""Rebuild actual shared battle engine fixture using preserved upstream objects.

Run only in the cached network-disabled host-engine verification container with
/client, /audit (the prior standalone engine foundation), and a NEW /out mounted.
This fixture wraps scenario ending/logging; transport, selected hero/scaling,
shared controls, journals and replay uploads use current production sources.
The original engine and completed Android build objects are never overwritten.
"""
from pathlib import Path
import subprocess,json,shlex,sys
base=Path('/audit');out=Path('/out');client=Path('/client');out.mkdir(parents=True,exist_ok=True);sys.path.insert(0,str(client/'alliance_game'))
from shared_connection_overlay import write_overlay
from engine_overlay import generate_synced_commands_overlay, generate_config_manager_overlay
write_overlay(client/'engine/src',out/'overlay')
(out/'synced_commands.cpp').write_bytes(generate_synced_commands_overlay((client/'engine/src/synced_commands.cpp').read_bytes()))
(out/'game_config_manager.cpp').write_bytes(generate_config_manager_overlay((client/'engine/src/game_config_manager.cpp').read_bytes()))
flags=subprocess.check_output(['pkg-config','--cflags','pangocairo','pangoft2','fontconfig','libcurl'],text=True).split()
source=(client/'engine/src/game_initialization/playcampaign.cpp').read_text()
source=source.replace('#include "game_initialization/playcampaign.hpp"','#include "game_initialization/playcampaign.hpp"\n#include "alliance_game/shared_launch.hpp"\n#include "alliance_game/shared_result.hpp"\nextern bool shared_fixture_replay();\nextern bool shared_fixture_companion();')
source=source.replace('level_result::type campaign_controller::play_game()\n{','level_result::type campaign_controller::play_game()\n{\n if(shared_fixture_companion())return level_result::type::quit;\n if(shared_fixture_replay())return level_result::type::quit;\n if(alliance_game::shared_pending()) {alliance_game::run_pending_shared();return level_result::type::quit;}')
source=source.replace('\tif(is_replay_) {\n\t\tstate_.get_replay().set_pos(0);','\tif(is_replay_ || (mp_info_ && mp_info_->skip_replay && alliance_game::shared_save(state_))) {\n\t\tstate_.get_replay().set_pos(0);')
source=source.replace('\tplaymp_controller playcontroller(state_.get_starting_point(), state_, mp_info_);','\tplaymp_controller playcontroller(state_.get_starting_point(), state_, mp_info_);\n if(mp_info_ && mp_info_->skip_replay && alliance_game::shared_save(state_))playcontroller.enable_replay(true);')
needle='\t\tif(!end_level.proceed_to_next_level) {';assert source.count(needle)==1
source=source.replace(needle,'\t\talliance_game::observe_shared_completion(state_,res);\n'+needle)
(out/'playcampaign.cpp').write_text(source)
source=(client/'alliance_game/shared_launch.cpp').read_text().replace('#include "cursor.hpp"','#include "cursor.hpp"\n#include <iostream>\nextern void shared_fixture_decorate(config&);')
source=source.replace('auto scenario=make_shared_scenario(plan,*source,*era);','auto scenario=make_shared_scenario(plan,*source,*era);shared_fixture_decorate(scenario);')
source=source.replace('captain_policy(*admission,descriptor,side);','std::cout<<"FIXTURE admission captain="<<admission->captain_action<<" ai="<<admission->computer_controlled<<" side="<<side<<std::endl;captain_policy(*admission,descriptor,side);')
source=source.replace('controller.set_mp_info(&metadata);controller.play_game();','controller.set_mp_info(&metadata);std::cout<<"SHARED ENGINE STARTED"<<std::endl;if(std::getenv("ALLIANCE_SHARED_FIXTURE_DELAY"))std::this_thread::sleep_for(std::chrono::seconds(30));controller.play_game();std::cout<<"SHARED ENGINE FINISHED"<<std::endl;')
source=source.replace('}catch(const std::exception&){', '}catch(const std::exception& e){std::cout<<"SHARED ENGINE FAILED: "<<e.what()<<std::endl;')
(out/'shared_launch_fixture.cpp').write_text(source)
objects=[];commands=[]
for name,path in [('engine_probe',client/'tests/shared_engine_probe.cpp'),('wesnothd_connection',out/'overlay/wesnothd_connection.cpp'),('shared_launch_fixture',out/'shared_launch_fixture.cpp'),('playcampaign',out/'playcampaign.cpp'),('synced_commands',out/'synced_commands.cpp'),('game_config_manager',out/'game_config_manager.cpp'),*((name,client/'alliance_game'/(name+'.cpp')) for name in ('shared_scenario','shared_result','shared_controls','companion','practice_plan','practice_scenario','hero_catalog','hero_unit_config'))]:
 target=out/(name+'.o');cmd=['g++','-std=c++17','-O0','-g0','-UNDEBUG','-DHAVE_CONFIG_H','-DBOOST_ALL_NO_LIB','-I/out/overlay','-I/client','-I/client/engine/src','-I/client/alliance_game','-I/audit/engine','-I/audit/prefix/include','-I/opt/boost/include',*flags,'-c',str(path),'-o',str(target)]
 commands.append(cmd);subprocess.run(cmd,check=True);objects.append(str(target))
for name in ('transport','account_client','account_protocol'):
 path=client/'alliance_native'/(name+'.cpp');target=out/(name+'.o');cmd=['g++','-std=c++17','-O0','-g0','-I/client','-I/opt/boost/include','-c',str(path),'-o',str(target)]
 commands.append(cmd);subprocess.run(cmd,check=True);objects.append(str(target))
link=subprocess.check_output(['ninja','-C',str(base/'engine'),'-t','commands','wesnoth'],text=True).splitlines()[-1];assert link.startswith(': && ') and link.endswith(' && :')
args=shlex.split(link[5:-5]);args[args.index('-o')+1]=str(out/'shared-engine')
removed=[a for a in args if a.endswith(('/wesnothd_connection.cpp.o','/playcampaign.cpp.o','/synced_commands.cpp.o','/game_config_manager.cpp.o'))];assert len(removed)==4,removed
args=[a for a in args if a not in removed];args=[('-Wl,--dependency-file='+str(out/'engine-link.d')) if a.startswith('-Wl,--dependency-file=') else a for a in args]
args+=objects+['/opt/boost/lib/libboost_container.a','-Wl,--wrap=main'];commands.append(args)
(out/'engine-build-commands.json').write_text(json.dumps(commands,indent=2));subprocess.run(args,cwd=base/'engine',check=True)
