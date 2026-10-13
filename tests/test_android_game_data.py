"""Offline OBB preparation tests with actual small ZIPs and filesystem fixtures."""
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
import prepare_android_game_data as D


class GameDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / 'source with spaces'
        (self.source / 'data').mkdir(parents=True)
        (self.source / 'data/empty.cfg').write_bytes(b'')
        (self.source / 'data/test.cfg').write_bytes(b'[test]\nvalue=one\n[/test]\n')
        (self.source / 'manifest.txt').write_bytes(b'core=1.19.26\n')
        self.expected = D.V.scan(self.source)
        self.output = self.base / 'output'
        self.output.mkdir()

    def build(self):
        return D.build_bundle(self.source, self.output, self.expected)

    def test_exact_bundle_and_reproducibility(self):
        before = D.V.inventory(self.source)
        manifest, obb = self.build()
        self.assertEqual(manifest['sha256'], self.expected['tree_sha256'])
        self.assertEqual(D.V.inventory(self.source), before)
        second = self.base / 'second'
        second.mkdir()
        self.assertEqual((manifest, obb), D.build_bundle(self.source, second, self.expected))
        with zipfile.ZipFile(self.output / D.OBB) as archive:
            self.assertEqual(archive.namelist(), sorted(before))
            self.assertEqual(archive.read('data/test.cfg'), (self.source / 'data/test.cfg').read_bytes())
        self.assertEqual((manifest, obb), D.verify_bundle(self.output, self.expected))

    def test_same_size_source_corruption_does_not_create_success(self):
        file = self.source / 'data/test.cfg'
        file.write_bytes(file.read_bytes().replace(b'one', b'two'))
        with self.assertRaisesRegex(D.BuildError, 'trusted game-data tree'):
            self.build()
        self.assertFalse((self.output / 'receipt.json').exists())

    def test_source_links_rejected(self):
        (self.source / 'data/link.cfg').symlink_to(self.source / 'manifest.txt')
        with self.assertRaises(D.V.VerificationError):
            self.build()

    def test_manifest_pin_is_enforced(self):
        self.build()
        path = self.output / D.MANIFEST
        path.write_bytes(path.read_bytes().replace(b'test.cfg', b'evil.cfg'))
        with self.assertRaisesRegex(D.BuildError, 'trusted game-data tree'):
            D.verify_bundle(self.output, self.expected)

    def rewrite_zip(self, transform):
        self.build()
        path = self.output / D.OBB
        with zipfile.ZipFile(path) as source:
            values = [(entry, source.read(entry)) for entry in source.infolist()]
        path.unlink()
        with zipfile.ZipFile(path, 'x') as dest:
            for index, (entry, value) in enumerate(values):
                transform(index, entry, value, dest)

    def test_same_size_zip_content_corruption_rejected(self):
        def change(index, entry, value, dest):
            dest.writestr(entry, value.replace(b'one', b'two'))
        self.rewrite_zip(change)
        with self.assertRaisesRegex(D.BuildError, 'differs from trusted'):
            D.verify_bundle(self.output, self.expected)

    def test_zip_symlink_rejected(self):
        def change(index, entry, value, dest):
            if index == 0:
                entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            dest.writestr(entry, value)
        self.rewrite_zip(change)
        with self.assertRaisesRegex(D.BuildError, 'metadata'):
            D.verify_bundle(self.output, self.expected)

    def test_unknown_duplicate_and_traversal_entries_rejected(self):
        self.build()
        path = self.output / D.OBB
        with zipfile.ZipFile(path, 'a') as archive:
            archive.writestr('../outside', b'untrusted')
        with self.assertRaisesRegex(D.BuildError, 'Unexpected OBB entries'):
            D.verify_bundle(self.output, self.expected)
        self.assertFalse((self.base / 'outside').exists())

    def test_signed_manifest_still_requires_safe_paths(self):
        path = self.output / D.MANIFEST
        data = D.V.canonical_record('../outside', 0, hashlib.sha256(b'').hexdigest())
        path.write_bytes(data)
        expected = {'files': 1, 'bytes': 0, 'tree_sha256': hashlib.sha256(data).hexdigest()}
        with self.assertRaises(D.V.VerificationError):
            D.parse_manifest(path, expected)

    def test_source_mutation_during_build_rejected(self):
        def mutate(_):
            (self.source / 'data/test.cfg').write_bytes(b'new source')
        with self.assertRaisesRegex(D.BuildError, 'Source changed during'):
            D.build_bundle(self.source, self.output, self.expected, mutate)

    def make_repo(self):
        repo = self.base / 'repo'
        (repo / 'tools').mkdir(parents=True)
        (repo / 'build').mkdir()
        for name in D.PROJECT_INPUTS:
            (repo / name).write_bytes((TOOLS.parent / name).read_bytes())
        return repo

    def test_completed_receipt_reuse_and_changed_input_fail_closed(self):
        repo = self.make_repo()
        with patch.object(D, 'reference', return_value=self.expected):
            first = D.prepare(repo, self.source, lambda _: None)
            self.assertEqual(first, D.verify_receipt(repo))
            self.assertEqual(first, D.prepare(repo, self.base / 'absent-source', lambda _: None))
            input_path = repo / D.PROJECT_INPUTS[0]
            input_path.write_bytes(input_path.read_bytes() + b'\n')
            with self.assertRaisesRegex(D.BuildError, 'receipt does not match'):
                D.verify_receipt(repo)

    def test_partial_stage_and_unrecognized_stage_are_preserved(self):
        repo = self.make_repo()
        stage = repo / D.STAGE
        stage.mkdir()
        saved = stage / 'keep.txt'
        saved.write_bytes(b'untouched')
        with patch.object(D, 'reference', return_value=self.expected):
            with self.assertRaises(OSError):
                D.prepare(repo, self.source)
            self.assertEqual(saved.read_bytes(), b'untouched')
            saved.unlink()
            D.write_json(stage / D.OWNER, D.OWNER_VALUE)
            with self.assertRaisesRegex(D.BuildError, 'Incomplete'):
                D.prepare(repo, self.source)
            self.assertEqual(D.read_json(stage / D.OWNER), D.OWNER_VALUE)

    def test_replaced_operation_lock_is_preserved(self):
        lock = self.output / '.operation.lock'
        with self.assertRaisesRegex(D.BuildError, 'replacement preserved'):
            with D.operation(self.output):
                lock.unlink()
                lock.write_text('another operation\n')
        self.assertEqual(lock.read_text(), 'another operation\n')

    def test_completed_receipt_rejects_active_operation(self):
        repo = self.make_repo()
        with patch.object(D, 'reference', return_value=self.expected):
            D.prepare(repo, self.source, lambda _: None)
            lock = repo / D.STAGE / '.operation.lock'
            lock.write_text('active\n')
            with self.assertRaisesRegex(D.BuildError, 'active or interrupted'):
                D.verify_receipt(repo)
            self.assertEqual(lock.read_text(), 'active\n')

    def test_operation_lock_preserves_existing_owner(self):
        lock = self.output / '.operation.lock'
        lock.write_text('other process\n')
        with self.assertRaisesRegex(D.BuildError, 'already active'):
            with D.operation(self.output):
                self.fail('Must not enter')
        self.assertEqual(lock.read_text(), 'other process\n')


if __name__ == '__main__':
    unittest.main()
