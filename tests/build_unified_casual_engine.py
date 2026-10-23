"""Link actual native casual identity fixture beside the shared audit engine.
Requires /client, /audit and the completed shared /out artifacts in the cached
network-disabled host-engine image; never rewrites the shared executable.
"""
from pathlib import Path
import json,subprocess
out=Path('/out');commands=json.loads((out/'engine-build-commands.json').read_text());built=[];record=[]
for name,source in [('casual_engine_probe','/client/tests/unified_casual_engine_probe.cpp'),('casual_sides','/client/alliance_game/casual_sides.cpp'),('casual_session','/client/alliance_game/casual_session.cpp')]:
 cmd=list(commands[0]);cmd[cmd.index('-c')+1]=source;target=str(out/(name+'.o'));cmd[cmd.index('-o')+1]=target;record.append(cmd);subprocess.run(cmd,check=True);built.append(target)
link=list(commands[-1]);link.remove('/out/engine_probe.o');link+=built;link[link.index('-o')+1]='/out/casual-identity-engine';record.append(link);(out/'casual-engine-commands.json').write_text(json.dumps(record,indent=2));subprocess.run(link,cwd='/audit/engine',check=True)
