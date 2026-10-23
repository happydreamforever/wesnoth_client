"""Download a completed shared replay, then play it in a fresh native process."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('--fixture', type=Path, required=True)
parser.add_argument('--engine-output', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--binary', type=Path, required=True)
args = parser.parse_args()
os.umask(0o077)
assert args.output.is_absolute() and not args.output.exists()
f = json.loads(args.fixture.read_text())
assert f['purpose'] == 'disposable-shared-engine-fixture'
args.output.mkdir(); own = args.output / 'download'; own.mkdir()
(own / 'private').mkdir(); (own / 'userdata').mkdir()
profile = dict(f['profile']); profile['port'] = 15002
public = own / 'private/alliance-public-profile.json'; public.write_text(json.dumps(profile))
env = dict(os.environ, SDL_VIDEODRIVER='dummy', SDL_AUDIODRIVER='dummy',
    WESNOTH_ALLIANCE_PROFILE=str(public), ALLIANCE_SHARED_FIXTURE_MODE='download',
    XDG_CACHE_HOME=str(own / 'font-cache'))
common = ['--test=test', '--multiplayer', '--nogui', '--nosound', '--no-log-to-file', '--log-none=audio']
command = [str(args.binary), '--shared-seat', str(args.fixture), '0', 'shared-fixture', '/gamedata',
    '--userdata-dir', str(own / 'userdata'), *common]
result = subprocess.run(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
(own / 'engine.log').write_text(result.stdout)
assert result.returncode == 0 and 'SHARED SERVER REPLAY VERIFIED' in result.stdout
assert 'SHARED ENGINE FAILED' not in result.stdout
actor = f['players'][0]['account_id']; run_id = f['run_id']
source = args.engine_output / '0/userdata/sync/saves' / ('Alliance-battle-' + run_id + '-' + actor + '.gz')
replay = own / 'userdata/sync/saves' / ('Alliance-server-battle-' + run_id + '-' + actor + '.gz')
assert replay.read_bytes() == source.read_bytes()
before = hashlib.sha256(replay.read_bytes()).hexdigest()
fresh = args.output / 'playback'; fresh.mkdir()
env = dict(os.environ, SDL_VIDEODRIVER='dummy', SDL_AUDIODRIVER='dummy',
    ALLIANCE_SHARED_FIXTURE_REPLAY=str(replay), XDG_CACHE_HOME=str(fresh / 'font-cache'))
command = [str(args.binary), '/gamedata', '--userdata-dir', str(fresh / 'userdata'), *common]
result = subprocess.run(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
(fresh / 'engine.log').write_text(result.stdout)
assert result.returncode == 0 and 'SHARED DISK REPLAY REACHED NATIVE END' in result.stdout
assert 'SHARED REAL INITIAL CONFIG VERIFIED' in result.stdout and 'SHARED REAL NETWORK ROUND VERIFIED' in result.stdout
assert before == hashlib.sha256(replay.read_bytes()).hexdigest()
assert not (fresh / 'private').exists()
(args.output / 'validation.json').write_text(json.dumps({'status':'passed', 'native_download':True,
    'native_replay_end':True, 'canonical_scaled_hero_verified':True, 'no_account_admission_for_playback':True,
    'source_unchanged':True, 'sha256':before, 'bytes':replay.stat().st_size}, indent=2) + '\n')
print('PASS completed server replay native download and fresh engine playback through END with scaled hero snapshots', flush=True)
