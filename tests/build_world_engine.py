"""Build production full native World worker with preserved pinned host objects.

Container mounts: /client read-only, /audit old full engine foundation read-only,
/out new owned output; no network. There is no synthetic terminal fixture.
"""
from pathlib import Path
import json
import shlex
import subprocess
import sys
import hashlib
client, base, out = Path('/client'), Path('/audit'), Path('/out')
sys.path.insert(0, str(client / 'alliance_game'))
from world_worker_overlay import generate, WORLD_SOURCES
out.mkdir(parents=True, exist_ok=True)
mapping = generate(client, out / 'overlay')
flags = subprocess.check_output(['pkg-config', '--cflags', 'pangocairo', 'pangoft2', 'fontconfig', 'libcurl'], text=True).split()
objects, commands = [], []
headers = sorted((client / 'alliance_game').glob('*.hpp')) + sorted((client / 'alliance_native').glob('*.hpp'))
header_digest = hashlib.sha256(b''.join(str(path).encode() + path.read_bytes() for path in headers)).hexdigest()
receipt = out / 'header-sha256.txt'
headers_changed = not receipt.exists() or receipt.read_text() != header_digest
sources = [(name[:-4], out / 'overlay' / name) for name in mapping if name.endswith('.cpp')]
sources += [(name, client / 'alliance_game' / (name + '.cpp')) for name in (*WORLD_SOURCES, 'hero_catalog', 'hero_unit_config', 'practice_plan', 'practice_scenario')]
sources += [(name, client / 'alliance_native' / (name + '.cpp')) for name in ('transport', 'account_client', 'account_protocol')]
for name, path in sources:
    target = out / (name + '.o')
    command = ['g++', '-std=c++17', '-O0', '-g0', '-UNDEBUG', '-DHAVE_CONFIG_H', '-DBOOST_ALL_NO_LIB',
        '-I/out/overlay', '-I/client', '-I/client/engine/src', '-I/client/alliance_game', '-I/audit/engine',
        '-I/audit/prefix/include', '-I/opt/boost/include', *flags, '-c', str(path), '-o', str(target)]
    commands.append(command)
    if headers_changed or not target.exists() or target.stat().st_mtime < path.stat().st_mtime:
        subprocess.run(command, check=True)
    objects.append(str(target))
lines = (base / 'engine/build.ninja').read_text().splitlines()
index = next(i for i, line in enumerate(lines) if line.startswith('build ') and ': CXX_EXECUTABLE_LINKER__wesnoth_' in line)
originals = lines[index].split(':', 1)[1].split()[1:]
originals = originals[:originals.index('|')]
replaced = tuple('/' + path.removeprefix('src/') + '.o' for name, path in mapping.items() if name.endswith('.cpp'))
selected = [name for name in originals if not name.endswith(replaced)]
if len(originals) - len(selected) != 8: raise ValueError('Native worker object ownership differs.')
libraries = shlex.split(next(line.strip().split(' = ', 1)[1] for line in lines[index:index+25] if line.strip().startswith('LINK_LIBRARIES = ')))
command = ['g++', *selected, *objects, *libraries, '/opt/boost/lib/libboost_container.a', '-lpng', '-o', str(out / 'world-worker')]
commands.append(command)
(out / 'commands.json').write_text(json.dumps(commands, indent=2))
subprocess.run(command, cwd=base / 'engine', check=True)
receipt.write_text(header_digest)
print('FULL NATIVE WORLD WORKER BUILD PASSED')
