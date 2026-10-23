#!/usr/bin/env python3
"""Use one additional owned account as an AI seat in the existing LDPlayer.

The player reviews the actual live room and confirms Join and Ready separately.
Credentials travel only through verified TLS and the owned child's stdin pipe.
This helper never creates heroes, changes passwords, or grants permanent XP.
"""
import argparse
import contextlib
import getpass
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid
import warnings

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('individual_companion_common', ROOT / 'tools/run_android_alliance_companion.py')
C = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(C)
REVIEW = 'INDIVIDUAL COMPANION REVIEW '
JOINING = 'INDIVIDUAL COMPANION JOINING: authenticated player; no permanent rewards'
STARTED = 'INDIVIDUAL COMPANION STARTED: computer controls only its selected side'
FINISHED = 'INDIVIDUAL COMPANION FINISHED: native battle ended; no permanent rewards'
MARKERS = {JOINING, STARTED, FINISHED}


def safe_text(value, maximum=256):
    return isinstance(value, str) and len(value) <= maximum and all(ord(c) >= 32 and ord(c) != 127 for c in value)


def choose_hero(account):
    choices = []
    catalog = C.file_document(ROOT / 'alliance_game/hero_catalog.json', 2 * 1024 * 1024)
    names = {form['id']: form['name'] for form in catalog['forms']}
    for hero in account['heroes']:
        for form in hero['forms']:
            if form not in names or not safe_text(hero['name'], 128) or not safe_text(names[form], 128):
                C.fail('owned_hero_catalogue_differs')
            choices.append((hero['hero_id'], form, hero['name'], names[form], hero['hero_level']))
    if not choices:
        print('This player has no unlocked heroes. Play and win a Campaign scenario with this account first.')
        C.fail('no_owned_heroes')
    print('Choose this account\'s hero and form:')
    for index, (_, _, hero, form, level) in enumerate(choices, 1):
        print(f'{index}. {hero} — {form}; permanent level {level}')
    selected = input('Hero number, or Enter to cancel: ')
    if not selected.isascii() or not selected.isdecimal() or not 1 <= int(selected) <= len(choices):
        C.fail('hero_selection_cancelled')
    hero, form, *_ = choices[int(selected) - 1]
    return hero, form


def reviewed_message(raw, room_id):
    value = C.document(raw.encode('utf-8'), 8192)
    settings = {'era', 'fog', 'shroud', 'observers', 'timer', 'mp_countdown_init_time', 'mp_countdown_reservoir_time',
                'mp_countdown_turn_bonus', 'mp_countdown_action_bonus', 'sides'}
    if (set(value) != {'action', 'room_id', 'room_revision', 'name', 'map', 'members', 'capacity', 'gold', 'turns', 'side'} | settings
            or value['action'] not in ('join', 'ready') or type(value['room_id']) is not int or value['room_id'] != room_id
            or type(value['room_revision']) is not int or not 1 <= value['room_revision'] <= 2147483647
            or type(value['capacity']) is not int or not 2 <= value['capacity'] <= 8
            or type(value['members']) is not int or not 1 <= value['members'] <= value['capacity']
            or type(value['side']) is not int or not 0 <= value['side'] <= value['capacity']
            or any(not safe_text(value[key]) for key in ('name', 'map', 'gold', 'turns', 'era'))
            or any(type(value[key]) is not bool for key in ('fog', 'shroud', 'observers', 'timer'))
            or any(type(value[key]) is not int or not 0 <= value[key] <= 100000 for key in settings if key.startswith('mp_countdown_'))
            or not isinstance(value['sides'], list) or len(value['sides']) != value['capacity']
            or value['action'] == 'join' and (value['side'] != 0 or value['members'] >= value['capacity'])
            or value['action'] == 'ready' and value['side'] == 0):
        C.fail('invalid_room_review')
    for index, side in enumerate(value['sides'], 1):
        if (not isinstance(side, dict) or set(side) != {'side', 'team', 'player', 'gold', 'income', 'village_gold', 'village_support', 'fog', 'shroud'}
                or type(side['side']) is not int or side['side'] != index or not safe_text(side['team'])
                or not safe_text(side['player'], 24) or any(type(side[key]) is not int or not -1000 <= side[key] <= 1000 for key in ('gold', 'income', 'village_gold', 'village_support'))
                or type(side['fog']) is not bool or type(side['shroud']) is not bool):
            C.fail('invalid_room_review')
    return value


def launch_script(private_root, stage, account_id, room_id, launch_id, data, hashes, profile_hash):
    session, script = C.launch_script(private_root, stage, account_id, 'room-' + str(room_id), launch_id, data, hashes, profile_hash)
    # Reuse the checked private app-directory/PID/payload scaffolding. The
    # individual mode has its own storage/locks and its own native entry point.
    old = 'export WESNOTH_ALLIANCE_COMPANION_PROFILE='
    if script.count(old) != 1:
        C.fail('launcher_contract_changed')
    script = script.replace(old, 'export WESNOTH_INDIVIDUAL_COMPANION_PROFILE=')
    return session.replace('/alliance-companions/', '/individual-companions/'), script.replace('/alliance-companions', '/individual-companions').replace('alliance-companion-v1 ', 'individual-companion-v1 ')


def run_native(device, script, admission, session, username, emit=print):
    command = device.base + ['shell', '-T', 'run-as ' + C.PACKAGE + ' sh -c ' + C.shlex.quote(script)]
    seen = []
    reviews = 0
    joined = False
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        try:
            process.stdin.write(json.dumps(admission, separators=(',', ':')).encode('ascii') + b'\n')
            process.stdin.flush()
            while True:
                raw = process.stdout.readline(8193)
                if not raw:
                    break
                if len(raw) > 8192:
                    continue  # Raw engine/ADB text is never logged.
                text = raw.decode('utf-8', errors='replace').rstrip('\r\n')
                if text.startswith(REVIEW):
                    reviews += 1
                    if reviews > 1000:
                        C.fail('too_many_room_changes')
                    value = reviewed_message(text[len(REVIEW):], admission['room_id'])
                    if value['action'] == 'join':
                        if joined or seen:
                            C.fail('unexpected_join_review')
                        joined = True
                    elif not joined or STARTED in seen:
                        C.fail('unexpected_ready_review')
                    emit(f"Room {value['room_id']}: {value['name']} — {value['map']}", flush=True)
                    emit(f"Players {value['members']}/{value['capacity']}; gold {value['gold']}; turns {value['turns']}; revision {value['room_revision']}", flush=True)
                    emit(f"Era {value['era']}; fog {value['fog']}; shroud {value['shroud']}; observers {value['observers']}; timer {value['timer']}", flush=True)
                    if value['timer']:
                        emit(f"Timer seconds: initial {value['mp_countdown_init_time']}, reserve {value['mp_countdown_reservoir_time']}, per turn {value['mp_countdown_turn_bonus']}, per action {value['mp_countdown_action_bonus']}", flush=True)
                    for seat in value['sides']:
                        emit(f"Side {seat['side']}: {seat['player'] or 'open'}; team {seat['team']}; gold {seat['gold']}; income {seat['income']}; village gold/support {seat['village_gold']}/{seat['village_support']}; fog/shroud {seat['fog']}/{seat['shroud']}", flush=True)
                    if value['action'] == 'ready':
                        emit(f"This account owns side {value['side']}. Ready may start the battle when every seat is filled and Ready.", flush=True)
                    expected = value['action'].upper() + ' ' + username
                    if input('Type ' + expected + ' to confirm these settings, or Enter to cancel: ') != expected:
                        C.fail('room_confirmation_cancelled')
                    answer = {'action': value['action'], 'revision': value['room_revision']}
                    process.stdin.write(json.dumps(answer, separators=(',', ':')).encode('ascii') + b'\n')
                    process.stdin.flush()
                elif text in MARKERS:
                    seen.append(text)
                    emit(text, flush=True)
            code = process.wait(timeout=10)
        except BaseException:
            with contextlib.suppress(Exception): C.stop_owned(device, session)
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
            raise
        finally:
            process.stdin.close()
    if code != 0 or seen != [JOINING, STARTED, FINISHED] or reviews < 2:
        C.fail('individual_companion_stopped')
    return {'result': 'completed', 'room_reviews': reviews}


def execute(args, report):
    report['step'] = 'I01_verify_build'
    payload = C.verify_build(ROOT, args.sdk, args.java)
    device = C.Device(args.adb, args.serial)
    if re.fullmatch(r'127\.0\.0\.1:[0-9]+', args.serial):
        C.capture([str(C.regular(args.adb)), '-H', 'localhost', '-P', '5037', 'connect', args.serial])
    report['step'] = 'I02_verify_device'
    private_root, _, profile, data, profile_hash = C.check_device(device, ROOT / 'build/android-account-game/app/build/outputs/apk/debug/app-debug.apk')
    print('I02 PASS: current APK, existing LDPlayer and saved trusted server verified.', flush=True)
    print('Use an account you own. Keep the visible LDPlayer game signed in as a DIFFERENT player.')
    print('This extra account will review a global individual room, choose its own hero, and play its side using the computer. It grants no permanent XP or rating.')
    if input('Type CONTROL ' + args.username + ' to log in once, or Enter to cancel: ') != 'CONTROL ' + args.username:
        C.fail('cancelled_before_login')
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        password = getpass.getpass('Player password (hidden; one login attempt): ')
    token = None
    try:
        report['step'] = 'I03_login_and_hero'
        token, account = C.login(profile, args.username, password)
        password = None
        hero, form = choose_hero(account)
        report.update(account_id=account['id'], database_generation=account['database_generation'], hero_id=hero, form_id=form)
        report['step'] = 'I04_transfer'
        stage = '/data/local/tmp/wesnoth-individual-companion-' + uuid.uuid4().hex
        device.call('shell', 'mkdir', '-m', '755', stage)
        for name, (path, expected) in payload.items():
            if C.digest(path) != expected: C.fail('payload_changed')
            device.call('push', str(path), stage + '/' + name, timeout=300)
            if device.call('shell', 'sha256sum', stage + '/' + name).split()[0].decode('ascii') != expected:
                C.fail('transferred_payload_differs')
            device.call('shell', 'chmod', '644', stage + '/' + name)
        session, script = launch_script(private_root, stage, account['id'], args.room_id, uuid.uuid4().hex, data,
            {name: item[1] for name, item in payload.items()}, profile_hash)
        report['step'] = 'I05_review_join_ready_play'
        report.update(run_native(device, script, {'intent': 'individual_play', 'account_id': account['id'], 'token': token,
            'room_id': args.room_id, 'hero_id': hero, 'form_id': form}, session, account['username']))
        report['outcome'] = 'completed'
        print('I05 COMPLETE: the real native individual battle ended. Permanent experience and rating are unchanged.', flush=True)
    finally:
        password = None
        if token:
            try:
                if C.account_request(profile, 'logout', {}, token) != {'status': 'logged_out'}:
                    C.fail('logout_response_invalid')
                report['session_logout'] = 'confirmed'
                print('I06 PASS: this companion session was revoked.', flush=True)
            except Exception:
                report['session_logout'] = 'unconfirmed'
                print('I06 INCOMPLETE: session revocation could not be confirmed.', flush=True)
            token = None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--username', required=True)
    parser.add_argument('--room-id', required=True, type=int)
    parser.add_argument('--serial', default='127.0.0.1:5555')
    parser.add_argument('--adb', type=Path, required=True)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--java', type=Path, required=True)
    args = parser.parse_args(argv)
    report = {'format': 'individual-android-companion-v1', 'version': C.VERSION, 'room_id': args.room_id,
              'outcome': 'failed', 'step': 'I00_inputs', 'wan_status': 'not_recorded'}
    destination = None
    try:
        if os.name != 'nt': C.fail('windows_required')
        if not re.fullmatch(r'[A-Za-z0-9_]{3,24}', args.username) or not 1 <= args.room_id <= 2147483647:
            C.fail('invalid_selection')
        diagnostics = ROOT / 'diagnostics'; diagnostics.mkdir(exist_ok=True)
        for path in (diagnostics, *diagnostics.parents):
            if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
                C.fail('report_path_redirected')
        destination = diagnostics / ('android-individual-companion-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex + '.json')
        execute(args, report)
        return 0 if report['outcome'] == 'completed' and report.get('session_logout') == 'confirmed' else 2
    except KeyboardInterrupt:
        report['error'] = 'interrupted'
        print('INDIVIDUAL COMPANION INTERRUPTED. Only this helper\'s owned native process was requested to stop.')
        return 130
    except Exception as error:
        report['error'] = str(error) if isinstance(error, C.CompanionError) else 'operation_failed'
        print('INDIVIDUAL COMPANION FAILED: ' + report['error'] + '; step=' + report['step'])
        return 1
    finally:
        if destination:
            with destination.open('x', encoding='utf-8') as output:
                json.dump(report, output, indent=2); output.write('\n')
            print('Sanitized report: ' + str(destination))


if __name__ == '__main__':
    raise SystemExit(main())
