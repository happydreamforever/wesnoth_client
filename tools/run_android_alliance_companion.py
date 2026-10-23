#!/usr/bin/env python3
"""Run one explicitly authorized AI-controlled account in the existing LDPlayer.

Passwords are read from the console once. Tokens travel only through verified
TLS and a private subprocess stdin pipe; neither is stored or reported. This
does not install an APK, import a profile, change readiness, or award rewards.
"""
import argparse
import contextlib
import getpass
import gzip
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import socket
import ssl
import stat
import struct
import subprocess
import sys
import threading
import time
import uuid
import warnings
import zlib

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'org.wesnoth.offline'
VERSION = 1192616
WIRE_VERSION = 2
PLAIN_LIMIT = 256 * 1024
INT64_MAX = (1 << 63) - 1
UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
TOKEN = re.compile(r'[A-Za-z0-9_-]{43}')
LIBRARIES = ('libmain.so', 'libSDL3.so', 'libSDL3_image.so', 'libSDL3_mixer.so', 'libc++_shared.so')
START = 'ALLIANCE COMPANION STARTING: authenticated computer-controlled fixed seat'
CAPTAIN_START = 'ALLIANCE COMPANION STARTING: authenticated turn-passing captain policy'
FINISHED = 'ALLIANCE COMPANION FINISHED: replay verified; status='
STOPPED = 'ALLIANCE COMPANION STOPPED: battle did not finish with a verified acknowledged result.'
MARKERS = {START, CAPTAIN_START, FINISHED + 'acknowledged', FINISHED + 'pending', STOPPED}
CAPTAIN_ACTIONS = {'surrender': 'Surrender', 'offer_draw': 'Offer draw', 'withdraw_draw': 'Withdraw draw'}


class CompanionError(Exception):
    """Only a fixed public error code is allowed in this exception."""


def fail(code):
    raise CompanionError(code)


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail('duplicate_json_field')
        result[key] = value
    return result


def document(raw, limit=32768):
    if len(raw) > limit:
        fail('response_too_large')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_pairs)
    except (UnicodeError, ValueError, RecursionError):
        fail('invalid_json')
    if not isinstance(value, dict):
        fail('invalid_json')
    return value


def public_profile(raw):
    obj = document(raw)
    if set(obj) != {'format', 'protocol', 'server_address', 'port', 'minimum_tls', 'certificate_pem', 'certificate_sha256'}:
        fail('invalid_public_profile')
    if type(obj['format']) is not int or obj['format'] != 1 or obj['protocol'] != 'wesnoth-alliance-tls' or obj['minimum_tls'] != '1.2' or type(obj['port']) is not int or obj['port'] != 15001:
        fail('invalid_public_profile')
    try:
        host = ipaddress.IPv4Address(obj['server_address'])
        pem = obj['certificate_pem']
        if str(host) != obj['server_address'] or host.is_multicast or host.is_unspecified or int(host) >= 0xe0000000:
            fail('invalid_public_profile')
        if not isinstance(pem, str) or len(pem) > 16384 or not re.fullmatch(r'-----BEGIN CERTIFICATE-----\n[A-Za-z0-9+/=\r\n]+-----END CERTIFICATE-----\n', pem):
            fail('invalid_public_profile')
        if hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest() != obj['certificate_sha256']:
            fail('invalid_public_profile')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_verify_locations(cadata=pem)
    except (ValueError, TypeError, ssl.SSLError):
        fail('invalid_public_profile')
    return obj


def exact_read(stream, size, deadline):
    result = bytearray()
    while len(result) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            fail('account_timeout')
        stream.settimeout(remaining)
        part = stream.recv(size - len(result))
        if not part:
            fail('account_connection_closed')
        result.extend(part)
    return bytes(result)


def account_request(profile, operation, body, token=None):
    """One bounded native account request, strict TLS identity before secrets."""
    if operation not in ('login', 'logout', 'battle_detail'):
        fail('unsupported_operation')
    request_id = uuid.uuid4().hex
    request = {'v': WIRE_VERSION, 'id': request_id, 'op': operation, 'body': body}
    if token is not None:
        if not TOKEN.fullmatch(token):
            fail('invalid_session')
        request['token'] = token
    encoded = json.dumps(request, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    if len(encoded) > PLAIN_LIMIT:
        fail('request_too_large')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_verify_locations(cadata=profile['certificate_pem'])
    deadline = time.monotonic() + 15
    with socket.create_connection((profile['server_address'], profile['port']), timeout=10) as plain:
        plain.sendall(b'\0\0\0\1')
        if exact_read(plain, 4, deadline) != b'\0\0\0\0':
            fail('account_tls_required')
        with context.wrap_socket(plain, server_hostname=profile['server_address']) as stream:
            if hashlib.sha256(stream.getpeercert(binary_form=True)).hexdigest() != profile['certificate_sha256']:
                fail('account_certificate_pin')
            payload = gzip.compress(encoded, mtime=0)
            stream.sendall(struct.pack('!I', len(payload)) + payload)
            length = struct.unpack('!I', exact_read(stream, 4, deadline))[0]
            if not 1 <= length <= 65536:
                fail('account_frame_bound')
            compressed = exact_read(stream, length, deadline)
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        decoded = decoder.decompress(compressed, PLAIN_LIMIT + 1)
    except zlib.error:
        fail('invalid_compression')
    if len(decoded) > PLAIN_LIMIT or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        fail('invalid_compression')
    reply = document(decoded, PLAIN_LIMIT)
    if set(reply) != {'v', 'id', 'ok', 'status', 'data'} or type(reply['v']) is not int or reply['v'] != WIRE_VERSION or reply['id'] != request_id or type(reply['ok']) is not bool or type(reply['status']) is not int or not 200 <= reply['status'] <= 599 or reply['ok'] != (200 <= reply['status'] < 300) or not isinstance(reply['data'], dict):
        fail('invalid_account_response')
    if not reply['ok']:
        fail('account_rejected')
    return reply['data']


def bounded_text(value, limit):
    return isinstance(value, str) and 0 < len(value.encode('utf-8')) <= limit and all(ord(c) >= 32 and ord(c) != 127 for c in value)


def integer(value, minimum=0, maximum=INT64_MAX):
    return type(value) is int and minimum <= value <= maximum


def account_identity(account, username):
    """Validate the complete v2 owned-hero projection, without a starter hero."""
    if (not isinstance(account, dict) or set(account) != {'id', 'username', 'database_generation', 'heroes'}
            or not isinstance(account['id'], str) or not UUID.fullmatch(account['id'])
            or not isinstance(account['database_generation'], str) or not UUID.fullmatch(account['database_generation'])
            or not isinstance(account['username'], str) or not re.fullmatch(r'[A-Za-z0-9_]{3,24}', account['username'])
            or account['username'].casefold() != username.casefold()
            or not isinstance(account['heroes'], list) or len(account['heroes']) > 256):
        fail('invalid_login_identity')
    ids = set()
    for hero in account['heroes']:
        if (not isinstance(hero, dict) or set(hero) != {'hero_id', 'name', 'hero_level', 'experience', 'total_experience', 'revision', 'forms'}
                or not bounded_text(hero['hero_id'], 80) or hero['hero_id'] in ids
                or not bounded_text(hero['name'], 128) or not integer(hero['hero_level'], 1)
                or not all(integer(hero[key]) for key in ('experience', 'total_experience', 'revision'))
                or not isinstance(hero['forms'], list) or len(hero['forms']) > 128
                or any(not bounded_text(form, 100) for form in hero['forms'])
                or len(set(hero['forms'])) != len(hero['forms'])):
            fail('invalid_login_identity')
        ids.add(hero['hero_id'])
    return account


def login(profile, username, password):
    result = account_request(profile, 'login', {'username': username, 'password': password})
    if set(result) != {'token', 'account'} or not isinstance(result['token'], str) or not TOKEN.fullmatch(result['token']):
        fail('invalid_login_response')
    try:
        account = account_identity(result['account'], username)
    except (CompanionError, UnicodeError):
        # A valid issued token is revoked even if the remaining identity is bad.
        with contextlib.suppress(Exception):
            account_request(profile, 'logout', {}, result['token'])
        fail('invalid_login_identity')
    return result['token'], account


def validate_selection(selection):
    if (not isinstance(selection, dict) or set(selection) != {'hero_id', 'form_id', 'hero_level', 'hero_revision', 'unit_type', 'unit_level'}
            or not bounded_text(selection['hero_id'], 80) or not bounded_text(selection['form_id'], 100)
            or not bounded_text(selection['unit_type'], 128) or not integer(selection['hero_level'], 1)
            or not integer(selection['hero_revision']) or not integer(selection['unit_level'], 0, 20)):
        fail('battle_hero_not_selected')



def captain_policy(args):
    action, turn = getattr(args, 'captain_action', None), getattr(args, 'after_turn', None)
    if action is None and turn is None:
        return {}
    if args.retry_result or action not in CAPTAIN_ACTIONS or type(turn) is not int or not 1 <= turn <= 100:
        fail('invalid_captain_policy')
    return {'captain_action': action, 'after_turn': turn}


def selected_side(data, battle_id, account, require_captain=False):
    account_id = account['id']
    if set(data) != {'battle'} or not isinstance(data['battle'], dict):
        fail('invalid_battle_response')
    battle = data['battle']
    if battle.get('id') != battle_id or battle.get('state') not in ('draft', 'running') or battle.get('validity') != 'valid':
        fail('battle_not_available')
    teams = battle.get('teams')
    size = battle.get('map', {}).get('size')
    if type(size) is not int or size not in (2, 3, 4) or not isinstance(teams, list) or len(teams) != 2:
        fail('invalid_battle_response')
    seats, sides, found = set(), set(), []
    for team in teams:
        if not isinstance(team, dict) or team.get('approved') is not True or not isinstance(team.get('players'), list) or len(team['players']) != size:
            fail('battle_not_ready')
        for player in team['players']:
            if not isinstance(player, dict) or player.get('ready') is not True or not isinstance(player.get('id'), str) or not UUID.fullmatch(player['id']) or type(player.get('side')) is not int or player['id'] in seats or player['side'] in sides:
                fail('battle_not_ready')
            validate_selection(player.get('selection'))
            seats.add(player['id']); sides.add(player['side'])
            if player['id'] == account_id:
                selected = player['selection']
                owned = next((hero for hero in account['heroes'] if hero['hero_id'] == selected['hero_id']), None)
                if (owned is None or selected['form_id'] not in owned['forms']
                        or selected['hero_level'] != owned['hero_level'] or selected['hero_revision'] != owned['revision']):
                    fail('battle_hero_selection_changed')
                if require_captain and team.get('captain_id') != account_id:
                    fail('account_not_selected_captain')
                found.append(player['side'])
    if sides != set(range(1, size * 2 + 1)) or len(found) != 1:
        fail('account_not_selected')
    return found[0]


def regular(path):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            fail('local_path_redirected')
    if not path.is_file():
        fail('local_file_missing')
    return path


def digest(path):
    value = hashlib.sha256()
    with regular(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def file_document(path, limit):
    with regular(path).open('rb') as source:
        return document(source.read(limit + 1), limit)


def capture(command, timeout=60, limit=65536):
    # Keep output bounded and private even when ADB returns unexpected text.
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        output = bytearray()
        def drain():
            for chunk in iter(lambda: process.stdout.read(4096), b''):
                if len(output) <= limit:
                    output.extend(chunk[:limit + 1 - len(output)])
        reader = threading.Thread(target=drain, daemon=True); reader.start()
        try:
            code = process.wait(timeout=timeout)
        except BaseException:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
            raise
        reader.join(5)
        if reader.is_alive() or code or len(output) > limit:
            fail('command_failed')
        return bytes(output)


class Device:
    def __init__(self, executable, serial):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]*', serial):
            fail('invalid_serial')
        self.base = [str(regular(executable)), '-H', 'localhost', '-P', '5037', '-s', serial]

    def call(self, *arguments, timeout=60, limit=65536):
        return capture(self.base + list(arguments), timeout, limit)

    def private(self, script, **kwargs):
        return self.call('shell', '-T', 'run-as ' + PACKAGE + ' sh -c ' + shlex.quote(script), **kwargs)


def verify_build(repo, sdk, java):
    verifier = regular(repo / 'tools/build_android_account_game.py')
    output = capture([sys.executable, '-I', '-B', str(verifier), '--verify-only', '--sdk', str(sdk), '--java', str(java)], timeout=900)
    if b'ANDROID ACCOUNT GAME ARTIFACTS VERIFIED' not in output.splitlines():
        fail('build_not_verified')
    paths = {'wesnoth-engine-cli': repo / 'build/android-engine/prefix/bin/wesnoth-engine-cli'}
    paths.update({name: repo / 'build/android-account-game/app/src/main/jniLibs/x86_64' / name for name in LIBRARIES})
    account_receipt = file_document(repo / 'build/android-account-game/receipt.json', 8 * 1024 * 1024)
    engine_receipt = file_document(repo / 'build/android-engine/receipt.json', 8 * 1024 * 1024)
    payload = {}
    for name, path in paths.items():
        record = (engine_receipt['artifacts']['prefix/bin/wesnoth-engine-cli'] if name == 'wesnoth-engine-cli'
                  else account_receipt['artifacts']['app/src/main/jniLibs/x86_64/' + name])
        checked = digest(path)
        if record != {'bytes': path.stat().st_size, 'sha256': checked}: fail('payload_receipt_differs')
        payload[name] = (regular(path), checked)
    return payload


def check_device(device, apk):
    if device.call('get-state').strip() != b'device':
        fail('device_not_ready')
    for key, expected in (('sys.boot_completed', b'1'), ('ro.product.cpu.abi', b'x86_64'), ('ro.build.version.sdk', b'28')):
        if device.call('shell', 'getprop', key).strip() != expected:
            fail('device_not_verified_ldplayer')
    versions = re.findall(rb'\bversionCode=([0-9]+)\b', device.call('shell', 'dumpsys', 'package', PACKAGE))
    if versions != [str(VERSION).encode()]:
        fail('installed_version_differs')
    installed = device.call('shell', 'pm', 'path', PACKAGE).strip().decode('ascii')
    if not re.fullmatch(r'package:/data/app/[A-Za-z0-9._~+=/-]+/base\.apk', installed):
        fail('installed_apk_path_invalid')
    remote_hash = device.call('shell', 'sha256sum', installed[8:]).split()[0].decode('ascii')
    if remote_hash != digest(apk):
        fail('installed_apk_differs')
    private_root = device.private('pwd').strip().decode('ascii')
    if not re.fullmatch(r'/data/(?:user/0|data)/org\.wesnoth\.offline', private_root):
        fail('private_app_path_invalid')
    profile_path = private_root + '/files/alliance-public-profile.json'
    raw = device.private('test -f ' + shlex.quote(profile_path) + ' && test ! -L ' + shlex.quote(profile_path) + ' && head -c 32769 ' + shlex.quote(profile_path), limit=32768)
    profile = public_profile(raw)
    data = '/sdcard/Android/data/' + PACKAGE + '/files/gamedata'
    device.private('test -f ' + shlex.quote(data + '/data/_main.cfg') + ' && test -d ' + shlex.quote(data + '/images'))
    return private_root, profile_path, profile, data, hashlib.sha256(raw).hexdigest()


def launch_script(private_root, stage, account_id, battle_id, launch_id, data, hashes, profile_hash):
    # Every variable below is generated or checked, never a credential.
    owner = private_root + '/files/alliance-companions/' + account_id
    saved = owner + '/' + battle_id
    session = saved + '/' + launch_id
    original_profile = private_root + '/files/alliance-public-profile.json'
    profile = saved + '/alliance-public-profile.json'
    base = private_root + '/files/alliance-companions'
    command = ' '.join(shlex.quote(value) for value in (
        session + '/wesnoth-engine-cli', '--nogui', '--multiplayer', '--nosound', '--language', 'en_US',
        '--data-dir', data, '--userdata-dir', saved + '/userdata', '--usercache-dir', saved + '/cache', '--no-log-to-file'))
    # The shell owns its child PID for its whole lifetime. Signals target only
    # that direct child, never a PID found by image name or a separate account.
    script = f'''umask 077
base={shlex.quote(base)}
owner={shlex.quote(owner)}
session={shlex.quote(session)}
saved={shlex.quote(saved)}
test ! -L "$base" && test ! -L "$owner" || exit 71
mkdir -p "$base" "$owner" || exit 71
mkdir "$owner/active" || exit 72
slot=
child=
cleanup() {{
  if test -n "$child"; then kill -TERM "$child" 2>/dev/null; wait "$child" 2>/dev/null; fi
  rm -f "$session/launcher.pid"
  test -z "$slot" || rmdir "$slot"
  rmdir "$owner/active"
}}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM
for number in 1 2 3 4 5 6 7; do
  if mkdir "$base/slot-$number" 2>/dev/null; then slot="$base/slot-$number"; break; fi
done
test -n "$slot" || exit 73
if test -e "$saved"; then
  test -d "$saved" && test ! -L "$saved" && test ! -L "$saved/owner" || exit 74
  test "$(cat "$saved/owner")" = {shlex.quote('alliance-companion-v1 ' + account_id + ' ' + battle_id)} || exit 74
else
  mkdir "$saved" || exit 74
  (set -C; printf '%s\\n' {shlex.quote('alliance-companion-v1 ' + account_id + ' ' + battle_id)} > "$saved/owner") || exit 74
fi
mkdir "$session" || exit 74
test ! -L {shlex.quote(profile)} || exit 74
if test ! -e {shlex.quote(profile)}; then
  cp {shlex.quote(original_profile)} {shlex.quote(profile)} || exit 74
fi
test "$(sha256sum {shlex.quote(profile)} | cut -d ' ' -f 1)" = {shlex.quote(profile_hash)} || exit 74
test ! -L "$saved/userdata" && test ! -L "$saved/cache" || exit 74
mkdir -p "$saved/userdata" "$saved/cache" || exit 74
echo $$ > "$session/launcher.pid"
'''
    for name in ('wesnoth-engine-cli', *LIBRARIES):
        script += 'cp ' + shlex.quote(stage + '/' + name) + ' "$session/' + name + '" || exit 75\n'
        script += 'test "$(sha256sum "$session/' + name + '" | cut -d " " -f 1)" = ' + shlex.quote(hashes[name]) + ' || exit 75\n'
    script += f'''chmod 700 "$session/wesnoth-engine-cli" || exit 75
cd "$session" || exit 75
export LD_LIBRARY_PATH="$session"
export SDL_VIDEO_DRIVER=dummy
export SDL_AUDIO_DRIVER=dummy
export SDL_VIDEODRIVER=dummy
export SDL_AUDIODRIVER=dummy
export WESNOTH_ALLIANCE_PROFILE={shlex.quote(profile)}
export WESNOTH_ALLIANCE_COMPANION_PROFILE={shlex.quote(profile)}
exec 3<&0
{command} <&3 &
child=$!
wait "$child"
result=$?
child=
exit "$result"
'''
    return session, script


def stop_owned(device, session):
    # Refuse a stale/reused PID: the still-running wrapper's complete command
    # line must contain this unique session path before signalling it.
    script = f'''session={shlex.quote(session)}
test -f "$session/launcher.pid" || exit 0
read launcher < "$session/launcher.pid"
case "$launcher" in ''|*[!0-9]*) exit 81;; esac
test -r "/proc/$launcher/cmdline" || exit 0
tr '\\000' '\\n' < "/proc/$launcher/cmdline" | grep -F -- "$session" >/dev/null || exit 82
kill -TERM "$launcher"
'''
    device.private(script, timeout=15)


def run_native(device, script, admission, session, emit=print):
    command = device.base + ['shell', '-T', 'run-as ' + PACKAGE + ' sh -c ' + shlex.quote(script)]
    seen = []
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        try:
            process.stdin.write(json.dumps(admission, separators=(',', ':')).encode('ascii') + b'\n')
            process.stdin.flush(); process.stdin.close()
            while True:
                line = process.stdout.readline(4097)
                if not line:
                    break
                # No raw engine/ADB output, even on failure, enters the report.
                text = line.decode('ascii', errors='ignore').rstrip('\r\n')
                if text in MARKERS:
                    seen.append(text); emit(text, flush=True)
            code = process.wait(timeout=10)
        except BaseException:
            with contextlib.suppress(Exception): stop_owned(device, session)
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
            raise
    expected_start = CAPTAIN_START if 'captain_action' in admission else START
    if code == 0 and seen == [expected_start, FINISHED + 'acknowledged']:
        return 'acknowledged'
    if code == 2 and seen == [expected_start, FINISHED + 'pending']:
        return 'pending'
    known = {71: 'companion_storage_redirected', 72: 'companion_account_already_running',
             73: 'companion_seven_process_limit', 74: 'companion_owned_storage_or_profile_differs',
             75: 'companion_payload_copy_failed'}
    if code in known:
        fail(known[code])
    fail('companion_stopped')


def execute(args, report):
    policy = captain_policy(args)
    if policy:
        report['captain_policy'] = policy.copy()
    report['step'] = 'C01_verify_build'
    payload = verify_build(ROOT, args.sdk, args.java)
    print('C01 PASS: current account APK and companion payload verified.', flush=True)
    device = Device(args.adb, args.serial)
    if re.fullmatch(r'127\.0\.0\.1:[0-9]+', args.serial):
        capture([str(regular(args.adb)), '-H', 'localhost', '-P', '5037', 'connect', args.serial])
    report['step'] = 'C02_verify_device'
    private_root, _, profile, data, profile_hash = check_device(device, ROOT / 'build/android-account-game/app/build/outputs/apk/debug/app-debug.apk')
    print('C02 PASS: installed APK, selected LDPlayer and saved public profile verified.', flush=True)
    report['step'] = 'C03_account_consent'
    print('Selected account: ' + args.username + '; battle: ' + args.battle_id)
    if args.retry_result:
        print('Send only this account\'s retained result and replay for the selected battle. This does not start gameplay or change readiness. The native engine verifies the retained evidence before upload.')
    elif policy:
        print('This captain seat will end its turns without moving or recruiting. On its own first turn at or after turn ' + str(policy['after_turn']) + ', it will request ' + CAPTAIN_ACTIONS[policy['captain_action']] + ' once. It will then continue ending its turns if the battle remains active.')
        print('Surrender ends the team\'s battle. A draw needs both teams\' offers; withdrawing a draw only removes this team\'s offer. Results still require captain review. This is a shared battle, not local practice.')
    else:
        print('This computer will control only this account\'s assigned side in the selected shared battle. Other selected players must be Ready and both captains must approve. Results require captain review; this is not local practice.')
    print('Use an account you own and authorize. Keep the main LDPlayer game logged in as a DIFFERENT selected player. No password or token is saved.')
    consent = ('RETRY ' if args.retry_result else 'CONTROL ') + args.username
    if policy:
        consent += ' ' + policy['captain_action'] + ' TURN ' + str(policy['after_turn'])
    if input('Type ' + consent + ' to continue, or press Enter to cancel: ') != consent:
        fail('cancelled_before_login')
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        password = getpass.getpass('Player password (hidden; one login attempt): ')
    if not 1 <= len(password) <= 128 or len(password.encode('utf-8')) > 512:
        fail('invalid_password_length')
    token = None
    try:
        token, account = login(profile, args.username, password)
        account_id = account['id']
        password = None
        report['step'] = 'C04_check_seat'
        report['account_id'] = account_id
        report['database_generation'] = account['database_generation']
        if args.retry_result:
            print('C04: authenticated result retry only; the native engine will verify this account\'s retained result and replay.', flush=True)
        else:
            side = selected_side(account_request(profile, 'battle_detail', {'battle_id': args.battle_id}, token), args.battle_id, account, bool(policy))
            report['side'] = side
            print('C04 PASS: authenticated account controls fixed side ' + str(side) + '; the fixed lineups are Ready.', flush=True)
        stage = '/data/local/tmp/wesnoth-alliance-companion-' + uuid.uuid4().hex
        device.call('shell', 'mkdir', '-m', '755', stage)
        report['step'] = 'C05_transfer'
        for name, (path, expected) in payload.items():
            if digest(path) != expected: fail('payload_changed')
            device.call('push', str(path), stage + '/' + name, timeout=300)
            if device.call('shell', 'sha256sum', stage + '/' + name).split()[0].decode('ascii') != expected:
                fail('transferred_payload_differs')
            device.call('shell', 'chmod', '644', stage + '/' + name)
        session, script = launch_script(private_root, stage, account_id, args.battle_id, uuid.uuid4().hex, data, {name: item[1] for name, item in payload.items()}, profile_hash)
        # Recheck only; never silently ready a player or confirm a captain.
        if not args.retry_result:
            selected_side(account_request(profile, 'battle_detail', {'battle_id': args.battle_id}, token), args.battle_id, account, bool(policy))
        report['step'] = 'C06_run'
        report['result'] = run_native(device, script, {'run_id': args.battle_id, 'account_id': account_id, 'token': token, 'intent': 'result_retry' if args.retry_result else 'play', **policy}, session)
        report['outcome'] = 'completed' if report['result'] == 'acknowledged' else 'result_pending'
        print('C06 COMPLETE: native result=' + report['result'] + '. This does not mean captain settlement has completed.', flush=True)
    finally:
        password = None
        if token:
            try:
                reply = account_request(profile, 'logout', {}, token)
                if reply != {'status': 'logged_out'}: fail('logout_response_invalid')
                report['session_logout'] = 'confirmed'
                print('C07 PASS: this companion session was revoked.', flush=True)
            except Exception:
                report['session_logout'] = 'unconfirmed'
                print('C07 INCOMPLETE: session revocation could not be confirmed. Existing account credentials were not changed.', flush=True)
            token = None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--username', required=True)
    parser.add_argument('--battle-id', required=True)
    parser.add_argument('--serial', default='127.0.0.1:5555')
    parser.add_argument('--adb', type=Path, required=True)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--java', type=Path, required=True)
    parser.add_argument('--retry-result', action='store_true', help='Upload only this account\'s retained result; do not enter gameplay.')
    parser.add_argument('--captain-action', choices=tuple(CAPTAIN_ACTIONS), help='Request one fixed captain action, then end this side\'s turns. Requires --after-turn.')
    parser.add_argument('--after-turn', type=int, help='First own turn numbered at least this value (1-100); requires --captain-action.')
    args = parser.parse_args(argv)
    report = {'format': 'alliance-android-companion-v1', 'version': VERSION, 'outcome': 'failed', 'step': 'C00_inputs', 'wan_status': 'not_recorded'}
    destination = None
    try:
        if os.name != 'nt': fail('windows_required')
        if not re.fullmatch(r'[A-Za-z0-9_]{3,24}', args.username) or not UUID.fullmatch(args.battle_id): fail('invalid_selection')
        diagnostics = ROOT / 'diagnostics'
        diagnostics.mkdir(exist_ok=True)
        for path in (diagnostics, *diagnostics.parents):
            if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400: fail('report_path_redirected')
        destination = diagnostics / ('android-companion-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex + '.json')
        report['battle_id'] = args.battle_id
        execute(args, report)
        return 0 if report['outcome'] == 'completed' and report.get('session_logout') == 'confirmed' else 2
    except KeyboardInterrupt:
        report['error'] = 'interrupted'
        print('COMPANION INTERRUPTED. Only the owned companion process was requested to stop. Retained game data was preserved.')
        return 130
    except Exception as error:
        report['error'] = str(error) if isinstance(error, CompanionError) else 'operation_failed'
        print('COMPANION FAILED: ' + report['error'] + '; step=' + report['step'])
        return 1
    finally:
        if destination:
            with destination.open('x', encoding='utf-8') as output:
                json.dump(report, output, indent=2); output.write('\n')
            print('Sanitized report: ' + str(destination))


if __name__ == '__main__':
    raise SystemExit(main())
