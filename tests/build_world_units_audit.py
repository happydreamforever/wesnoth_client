"""Create a separate full-engine native-table audit binary from the passing worker.

Only this test binary inserts a synthetic XP audit after native map creation.
It exits before combat/result submission and never replaces the real worker.
"""
from pathlib import Path
import json
import subprocess

out=Path('/out')
commands=json.loads((out/'commands.json').read_text())
source=Path('/client/alliance_game/world_result.cpp').read_text()
source='#include "alliance_game/hero_catalog.hpp"\n#include "actions/advancement.hpp"\n#include "team.hpp"\n'+source
anchor='void record_world_experience(unit&u,int earned)noexcept{try{'
assert source.count(anchor)==1
source=source.replace(anchor,'#include "/client/tests/world_native_units_probe.hpp"\n'+anchor+'native_world_units_probe(u);')
target=out/'world_result_unit_audit.cpp';target.write_text(source)
command=next(list(c) for c in commands if '-c' in c and c[c.index('-c')+1]=='/client/alliance_game/world_result.cpp')
command[command.index('-c')+1]=str(target)
command[command.index('-o')+1]=str(out/'world_result_unit_audit.o')
subprocess.run(command,check=True)
link=list(commands[-1]);link[link.index('/out/world_result.o')]='/out/world_result_unit_audit.o'
link[link.index('-o')+1]='/out/world-unit-audit'
subprocess.run(link,cwd='/audit/engine',check=True)
print('SEPARATE NATIVE UNIT AUDIT BUILD PASSED')
