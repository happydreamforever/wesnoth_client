"""Link an isolated actual native engine fixture using preserved upstream objects.

Requires cached host-engine image, /client, /audit foundation, /previous unified
shared-engine build and a fresh /out. No completed production stage is changed.
"""
from pathlib import Path
import json
import subprocess

previous=Path('/previous');out=Path('/out')
commands=json.loads((previous/'engine-build-commands.json').read_text())
built=[];record=[]
for name,source in [('engine_probe','/client/tests/individual_engine_probe.cpp'),
        *((name,'/client/alliance_game/'+name+'.cpp') for name in
          ('companion','casual_sides','casual_session','individual_rooms','battle_ui','team_views')),
        ('account_client','/client/alliance_native/account_client.cpp')]:
    command=list(commands[0]);command[command.index('-I/out/overlay')]='-I/previous/overlay'
    command[command.index('-c')+1]=source;target=str(out/(name+'.o'));command[command.index('-o')+1]=target
    record.append(command);subprocess.run(command,check=True);built.append(target)
link=[item.replace('/out/','/previous/') for item in commands[-1]]
link=[item for item in link if item not in ('/previous/engine_probe.o','/previous/companion.o','/previous/account_client.o')]
link[link.index('-o')+1]=str(out/'individual-engine')
link=[('-Wl,--dependency-file='+str(out/'engine-link.d')) if item.startswith('-Wl,--dependency-file=') else item for item in link]
link+=built;record.append(link)
(out/'engine-build-commands.json').write_text(json.dumps(record,indent=2)+'\n')
subprocess.run(link,cwd='/audit/engine',check=True)
