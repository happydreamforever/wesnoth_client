#!/usr/bin/env python3
"""Validate only a bounded PUBLIC profile before ADB transfer; print no credential material."""
import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import ssl
import stat
import sys


def validated_bytes(path):
    for part in [path, *path.parents]:
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Profile links/reparse points are not accepted.')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 32768:
        raise ValueError('Public profile must be a regular file no larger than 32 KiB.')
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result: raise ValueError('Duplicate public profile field.')
            result[key] = value
        return result
    with path.open('rb') as source:
        data = source.read(32769)
    if len(data) > 32768: raise ValueError('Public profile exceeds its bound.')
    obj = json.loads(data.decode('utf-8'), object_pairs_hook=pairs)
    if not isinstance(obj, dict) or set(obj) != {'format', 'protocol', 'server_address', 'port', 'minimum_tls', 'certificate_pem', 'certificate_sha256'}:
        raise ValueError('Expected exact PUBLIC profile fields. Private identities/runtime files are not accepted.')
    if type(obj['format']) is not int or obj['format'] != 1 or obj['protocol'] != 'wesnoth-alliance-tls' or obj['minimum_tls'] != '1.2':
        raise ValueError('Unsupported public profile format.')
    host = ipaddress.IPv4Address(obj['server_address'])
    if str(host) != obj['server_address'] or host.is_multicast or host.is_unspecified or int(host) >= 0xe0000000:
        raise ValueError('Expected a numeric IPv4 server address.')
    if type(obj['port']) is not int or not 1 <= obj['port'] <= 65535:
        raise ValueError('Invalid profile port.')
    pem = obj['certificate_pem']
    if not isinstance(pem, str) or len(pem) > 16384 or not re.fullmatch(r'-----BEGIN CERTIFICATE-----\n[A-Za-z0-9+/=\r\n]+-----END CERTIFICATE-----\n', pem):
        raise ValueError('Expected one PUBLIC certificate.')
    digest = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
    if obj['certificate_sha256'] != digest: raise ValueError('Public certificate pin mismatch.')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cadata=pem)
    return obj, data


def validate(path):
    return validated_bytes(path)[0]


def main():
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('profile', type=Path)
        parser.add_argument('--copy-to', type=Path)
        args = parser.parse_args()
        obj, data = validated_bytes(args.profile.absolute())
        if args.copy_to:
            destination = args.copy_to.absolute()
            for parent in destination.parents:
                info = parent.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                    raise ValueError('Copy parent is not a regular directory.')
            with destination.open('xb') as output:
                output.write(data)
        print('Server: ' + obj['server_address'] + ':' + str(obj['port']))
        print('Certificate SHA-256: ' + obj['certificate_sha256'])
        print('PUBLIC ALLIANCE PROFILE VERIFIED')
        return 0
    except (OSError, ValueError, TypeError, KeyError, ssl.SSLError):
        print('ERROR: Public profile validation failed. Select only the original public-profile.json; its contents were not printed.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
