"""Play a retained v1 replay with the current real engine, without networking.

Run inside the cached host-engine container with /gamedata and its shared
libraries mounted. The source replay is opened read-only and hashed again.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('--replay', type=Path, required=True)
parser.add_argument('--binary', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
os.umask(0o077)
assert args.output.is_absolute() and not args.output.exists()
source = args.replay.read_bytes()
assert b'alliance-shared-v1' in gzip.decompress(source)
before = hashlib.sha256(source).hexdigest()
args.output.mkdir()
env = dict(os.environ, SDL_VIDEODRIVER='dummy', SDL_AUDIODRIVER='dummy',
           ALLIANCE_SHARED_FIXTURE_REPLAY=str(args.replay),
           XDG_CACHE_HOME=str(args.output / 'font-cache'))
result = subprocess.run([
    str(args.binary), '/gamedata', '--userdata-dir', str(args.output / 'userdata'),
    '--test=test', '--multiplayer', '--nogui', '--nosound', '--no-log-to-file',
    '--log-none=audio'], env=env, stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT, text=True, timeout=120)
(args.output / 'engine.log').write_text(result.stdout)
record = dict(source_sha256=before,
              source_unchanged=before == hashlib.sha256(args.replay.read_bytes()).hexdigest(),
              exit=result.returncode,
              native_end='SHARED DISK REPLAY REACHED NATIVE END' in result.stdout,
              legacy_format='alliance-shared-v1')
(args.output / 'validation.json').write_text(json.dumps(record, indent=2) + '\n')
assert record['exit'] == 0 and record['native_end'] and record['source_unchanged'], record
print('PASS retained v1 shared replay reaches native END; source bytes preserved')
