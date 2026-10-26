#!/usr/bin/env python3
"""Build public-only startup assets. Never execute or package environment files."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('public_profile_validator', ROOT / 'tools/validate_android_account_profile.py')
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)
CONFIG_PATH = None
OPENSSL_PATH = None
PROFILE_ASSET = 'alliance-public-profile.json'
UI_MANIFEST = 'alliance-ui-manifest.json'
STARTUP = {
    'startup/titlescreen.webp': ('data/core/images/maps/titlescreen.webp', 1608774, '482437e8173e837a2ac9d50d8be367685c19815a00c3dcf3d9797104893282cd'),
    'startup/logo.png': ('images/misc/logo.png', 47379, '48545292b3b2ccfa2d8b83589a87d5f7506793af26514b9152a4e012ec20eef0'),
}


def record(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode('ascii')


def regular_bytes(path, limit):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Public build inputs cannot be links or reparse points.')
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
        raise ValueError('Public build input is empty, oversized or not a regular file.')
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    current = path.stat()
    identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    if identity(before) != identity(after) or identity(before) != identity(current) or len(data) != before.st_size:
        raise ValueError('Public build input changed while being read.')
    return data


def configured_profile(repo, config=None):
    """Read one literal public path; ignore all other assignment values."""
    selected = Path(config or CONFIG_PATH or Path(repo) / '.env').absolute()
    try:
        text = regular_bytes(selected, 8192).decode('utf-8-sig')
        values = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, separator, value = line.partition('=')
            key = key.strip()
            if separator != '=' or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
                raise ValueError()
            if key != 'WESNOTH_PUBLIC_PROFILE_PATH':
                continue
            if values:
                raise ValueError()
            value = value.strip()
            if value[:1] in ('"', "'"):
                if len(value) < 3 or value[-1:] != value[:1]:
                    raise ValueError()
                value = value[1:-1]
            if not value or any(c in value for c in '\x00\r\n$`') or value.startswith('~'):
                raise ValueError()
            values[key.strip()] = value
        if set(values) != {'WESNOTH_PUBLIC_PROFILE_PATH'}:
            raise ValueError()
        path = Path(values['WESNOTH_PUBLIC_PROFILE_PATH'])
        return path if path.is_absolute() else selected.parent / path
    except (OSError, UnicodeError, ValueError):
        raise ValueError('Client configuration must contain one literal WESNOTH_PUBLIC_PROFILE_PATH pointing to the existing public-profile.json. Configuration contents were not printed.') from None


def public_profile(repo, config=None, openssl=None):
    try:
        path = configured_profile(repo, config)
        profile, original = V.validated_bytes(path.absolute())
        executable = openssl or OPENSSL_PATH or shutil.which('openssl')
        if not executable:
            raise ValueError('An existing OpenSSL 3 executable is required for public certificate verification.')
        # Verify only a PUBLIC certificate, with no network or system trust store.
        with tempfile.TemporaryDirectory(prefix='wesnoth-public-cert-') as directory:
            certificate = Path(directory) / 'public.pem'
            certificate.write_text(profile['certificate_pem'], encoding='ascii')
            result = subprocess.run([str(executable), 'verify', '-no-CAfile', '-no-CApath', '-no-CAstore',
                '-partial_chain', '-x509_strict', '-purpose', 'sslserver', '-verify_ip', profile['server_address'],
                '-trusted', str(certificate), str(certificate)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=15, check=False)
            # A trusted leaf's CA status needs an explicit check as well.
            detail = subprocess.run([str(executable), 'x509', '-in', str(certificate), '-noout', '-ext', 'basicConstraints'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=False)
            if result.returncode or detail.returncode or b'CA:TRUE' in detail.stdout:
                raise ValueError()
        if regular_bytes(path, 32768) != original:
            raise ValueError()
        data = json_bytes(profile)
        return data, {**record(data), 'address': profile['server_address'], 'port': profile['port'],
                      'certificate_sha256': profile['certificate_sha256']}
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        raise ValueError('Bundled public profile failed certificate, IP, pin, validity or input verification. No private material was printed.') from None


def artwork(repo):
    """Read pinned startup art from the unchanged OBB and exact optional UI assets."""
    repo = Path(repo)
    result = {}
    with zipfile.ZipFile(repo / 'build/android-game-data/main.1192600.org.wesnoth.offline.obb') as archive:
        for name, (source, size, sha) in STARTUP.items():
            entries = [entry for entry in archive.infolist() if entry.filename == source]
            if len(entries) != 1 or entries[0].file_size != size:
                raise ValueError('Pinned startup artwork is missing or oversized.')
            data = archive.read(entries[0])
            if record(data) != {'bytes': size, 'sha256': sha}:
                raise ValueError('Pinned startup artwork differs from the existing data manifest.')
            result[name] = data
    def unique_keys(pairs):
        value = {}
        for key, entry in pairs:
            if key in value: raise ValueError('Duplicate owned UI manifest field.')
            value[key] = entry
        return value
    manifest = json.loads(regular_bytes(repo / 'tools/native-account-game/ui-assets.json', 1024 * 1024), object_pairs_hook=unique_keys)
    if (not isinstance(manifest, dict) or set(manifest) != {'format', 'files'} or type(manifest['format']) is not int
            or manifest['format'] != 1 or not isinstance(manifest['files'], list) or len(manifest['files']) > 512):
        raise ValueError('Invalid owned UI asset manifest.')
    entries = []
    for entry in manifest['files']:
        if not isinstance(entry, dict) or set(entry) != {'path', 'bytes', 'sha256'}:
            raise ValueError('Invalid owned UI asset entry.')
        name = entry['path']
        if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9_-]+(?:/[a-z0-9_.-]+)+', name) or any(p in ('.', '..') for p in name.split('/')):
            raise ValueError('Unsafe owned UI asset name.')
        asset = 'alliance-ui/' + name
        if asset in result or type(entry['bytes']) is not int or not 0 < entry['bytes'] <= 16 * 1024 * 1024:
            raise ValueError('Duplicate or oversized owned UI asset.')
        data = regular_bytes(repo / 'tools/native-account-game/ui-assets' / name, 16 * 1024 * 1024)
        if record(data) != {key: entry[key] for key in ('bytes', 'sha256')}:
            raise ValueError('Owned UI asset differs from its pinned manifest.')
        result[asset] = data
        entries.append(entry)
    result[UI_MANIFEST] = json_bytes({'format': 1, 'files': entries})
    return result


def bundle(repo):
    profile, identity = public_profile(repo)
    files = artwork(repo)
    files[PROFILE_ASSET] = profile
    return files, {'public_server': identity,
                   'bundled_assets': {name: record(data) for name, data in sorted(files.items())}}
