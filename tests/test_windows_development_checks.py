"""Signer and source-cache guards at the portable build boundary."""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('development_checks',ROOT/'tools/windows_development.py')
D=importlib.util.module_from_spec(spec);spec.loader.exec_module(D)

class AndroidDevelopmentChecks(unittest.TestCase):
    def test_apk_certificate_requires_success_and_exactly_one_signer(self):
        digest='a'*64
        good=f'Signer #1 certificate SHA-256 digest: {digest}\n'
        for code,text in ((1,good),(0,''),(0,good+good),(0,good+'Signer #2 certificate SHA-256 digest: '+'b'*64+'\n')):
            with patch.object(D.subprocess,'run',return_value=SimpleNamespace(returncode=code,stdout=text)):
                with self.assertRaises(ValueError): D.certificate(Path('apk'),Path('sdk'),Path('java'))
        with patch.object(D.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=good)) as run:
            self.assertEqual(D.certificate(Path('original apk'),Path('sdk'),Path('java')),digest)
            self.assertIn('original apk',run.call_args.args[0])

    def test_cache_import_verifies_before_copy_and_never_overwrites_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'external archive cache';source.mkdir()
            (root/'tools').mkdir()
            records=[]
            for name in ('boost.tar','bzip.tar','network.tar'):
                data=('fixture '+name).encode();(source/name).write_bytes(data)
                records.append(dict(name=name,bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
            (root/'tools/android-native-archives.json').write_text(json.dumps({'archives':records}))
            modules={'build_android_boost':SimpleNamespace(BOOST_ARCHIVE='boost.tar',B=SimpleNamespace(ARCHIVE_NAME='bzip.tar')),
                     'build_android_network':SimpleNamespace(ARCHIVES={'network.tar':'source'})}
            with patch.object(D,'module',side_effect=modules.__getitem__):
                D.import_archives(root,source)
                target=root/'packaging/android/download'
                self.assertEqual((target/'boost.tar').read_bytes(),(source/'boost.tar').read_bytes())
                D.import_archives(root,source)
                (target/'network.tar').write_bytes(b'preserve conflicting cache')
                (target/'boost.tar').unlink()
                with self.assertRaises(ValueError): D.import_archives(root,source)
                self.assertFalse((target/'boost.tar').exists())
                self.assertEqual((target/'network.tar').read_bytes(),b'preserve conflicting cache')

    def test_stage_verification_uses_existing_complete_validators(self):
        calls=[]
        def fake(name):
            return SimpleNamespace(verify_media=lambda root:calls.append((name,'media')),
                prior_inputs=lambda root:calls.append((name,'fonts')),
                verify_receipt=lambda root:calls.append((name,'receipt')))
        with patch.object(D,'module',side_effect=fake):
            for stage in D.STAGES: D.verify_stage(stage)
        self.assertEqual(calls[:2],[('android_engine_artifacts','media'),('build_android_network','fonts')])
        self.assertEqual(calls[-1],('build_android_game','receipt'))
