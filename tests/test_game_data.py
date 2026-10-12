"""Exercise exact, read-only game-data parity with small isolated fixtures."""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("verify_game_data", REPO / "tools/verify_game_data.py")
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)
SUCCESS = "GAME DATA VERIFICATION PASSED"


class GameDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wesnoth game data parity ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "original"
        self.root.mkdir()
        self.fixture = {
            "manifest.txt": b"core.version=1.19.26\nmusic.version=1.19.18\n",
            "status.properties": b"manual_install=true\ncore.version=1.19.26\r\n",
            "data/core/main.cfg": b"[game]\nvalue=1\n[/game]\n",
            "data/test/empty.cfg": b"",
            "images/pixel.bin": b"\x00\xff\x10\r\n",
            "translations/.gitignore": b"hidden fixture\n",
        }
        for name, content in self.fixture.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.expected = VERIFY.scan(self.root)
        self.reference = self.base / "reference.json"
        self.reference.write_text(json.dumps({
            "schema": 1, "algorithm": VERIFY.ALGORITHM, **self.expected,
        }), encoding="utf-8")

    def copy(self, name="copy"):
        return Path(shutil.copytree(self.root, self.base / name))

    def invoke(self, root):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(VERIFY, "REFERENCE", self.reference), redirect_stdout(output), redirect_stderr(errors):
            result = VERIFY.main(["--data-root", str(root)])
        return result, output.getvalue(), errors.getvalue()

    def assert_failure(self, root):
        result, output, errors = self.invoke(root)
        self.assertEqual(result, 2, output + errors)
        self.assertNotIn(SUCCESS, output + errors)
        self.assertIn("GAME DATA VERIFICATION FAILED", errors)
        return output, errors

    def test_unchanged_copy_passes_without_modification(self):
        root = self.copy()
        before = {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in root.rglob("*") if p.is_file()}
        self.assertEqual(VERIFY.differences(VERIFY.scan(root), self.expected), [])
        result, output, errors = self.invoke(root)
        self.assertEqual(result, 0, errors)
        self.assertIn(SUCCESS, output)
        self.assertEqual(errors, "")
        after = {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_canonical_hash_has_independent_exact_record_definition(self):
        root = self.base / "canonical"
        root.mkdir()
        (root / "a.cfg").write_bytes(b"abc")
        (root / "empty.cfg").write_bytes(b"")
        # Known standard SHA256 values, with literal ASCII framing and LF.
        first = (b'["a.cfg",3,"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"]\n')
        second = (b'["empty.cfg",0,"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"]\n')
        digest = hashlib.sha256(first + second).hexdigest()
        result = VERIFY.scan(root)
        self.assertEqual(result["tree_sha256"], digest)
        self.assertEqual(result["directories"], {".": {"files": 2, "bytes": 3, "sha256": digest}})
        self.assertEqual(result["empty_files"], ["empty.cfg"])

    def test_same_size_byte_tamper_fails(self):
        root = self.copy()
        path = root / "images/pixel.bin"
        path.write_bytes(b"\x00\xfe\x10\r\n")
        actual = VERIFY.scan(root)
        self.assertEqual(actual["files"], self.expected["files"])
        self.assertEqual(actual["bytes"], self.expected["bytes"])
        self.assertTrue(VERIFY.differences(actual, self.expected))
        output, _ = self.assert_failure(root)
        self.assertIn("Directory differs (file names, sizes or contents): images", output)

    def test_name_only_and_case_only_changes_fail(self):
        for index, name in enumerate(("renamed.bin", "Pixel.bin")):
            with self.subTest(name=name):
                root = self.copy(f"copy-{index}")
                (root / "images/pixel.bin").rename(root / "images" / name)
                actual = VERIFY.scan(root)
                self.assertEqual(actual["bytes"], self.expected["bytes"])
                self.assertEqual(actual["files"], self.expected["files"])
                self.assert_failure(root)

    def test_missing_and_extra_files_fail(self):
        missing = self.copy("missing")
        (missing / "data/core/main.cfg").unlink()
        self.assert_failure(missing)
        extra = self.copy("extra")
        (extra / "extra.cfg").write_bytes(b"extra")
        self.assert_failure(extra)

    def test_empty_file_removed_added_or_populated_fails(self):
        for action in ("removed", "added", "populated"):
            with self.subTest(action=action):
                root = self.copy(action)
                if action == "removed":
                    (root / "data/test/empty.cfg").unlink()
                elif action == "added":
                    (root / "unexpected-empty.cfg").touch()
                else:
                    (root / "data/test/empty.cfg").write_bytes(b"not empty")
                actual = VERIFY.scan(root)
                self.assertNotEqual(actual["empty_files"], self.expected["empty_files"])
                self.assert_failure(root)

    def test_hidden_files_are_included(self):
        self.assertEqual(self.expected["files"], len(self.fixture))
        self.assertIn("translations", self.expected["directories"])
        root = self.copy()
        (root / "translations/.gitignore").unlink()
        self.assert_failure(root)

    def test_line_endings_are_exact_bytes(self):
        root = self.copy()
        path = root / "status.properties"
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
        actual = VERIFY.scan(root)
        self.assertNotEqual(actual["metadata"]["status.properties"], self.expected["metadata"]["status.properties"])
        self.assert_failure(root)

    def test_timestamps_and_empty_directory_structure_are_ignored(self):
        root = self.copy()
        os.utime(root / "images/pixel.bin", ns=(1_000_000_000, 1_000_000_000))
        (root / "unused/empty/directory").mkdir(parents=True)
        self.assertEqual(VERIFY.differences(VERIFY.scan(root), self.expected), [])

    @unittest.skipIf(os.name == "nt", "Creating symlinks may require Windows privilege")
    def test_file_directory_and_root_symlinks_are_rejected(self):
        target = self.base / "target"
        target.mkdir()
        (target / "outside.cfg").write_bytes(b"outside")
        for kind in ("file", "directory", "root"):
            with self.subTest(kind=kind):
                root = self.copy(kind)
                if kind == "file":
                    (root / "link.cfg").symlink_to(target / "outside.cfg")
                elif kind == "directory":
                    (root / "linked-directory").symlink_to(target, target_is_directory=True)
                else:
                    alias = self.base / "root-alias"
                    alias.symlink_to(root, target_is_directory=True)
                    root = alias
                with self.assertRaisesRegex(VERIFY.VerificationError, "Link or reparse"):
                    VERIFY.scan(root)
                self.assert_failure(root)

    def test_windows_reparse_attribute_is_rejected(self):
        info = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_file_attributes=0x400)
        with patch.object(Path, "lstat", return_value=info):
            with self.assertRaisesRegex(VERIFY.VerificationError, "reparse"):
                VERIFY.checked_stat(self.root)

    @unittest.skipIf(os.name == "nt", "Windows normally cannot create colliding names")
    def test_casefold_file_and_directory_collisions_are_rejected(self):
        for kind in ("file", "directory"):
            with self.subTest(kind=kind):
                root = self.copy(kind)
                if kind == "file":
                    (root / "Case.cfg").write_bytes(b"one")
                    (root / "case.cfg").write_bytes(b"two")
                else:
                    (root / "Folder").mkdir()
                    (root / "folder").mkdir()
                with self.assertRaisesRegex(VERIFY.VerificationError, "collision"):
                    VERIFY.scan(root)

    def test_windows_invalid_names_are_rejected(self):
        names = ("CON", "con.cfg", "PRN.txt", "AUX", "NUL", "COM1.cfg", "LPT9", "COM\u00b9",
                 "bad:name", "bad?name", "bad*name", "bad|name", "bad<name", 'bad"name',
                 "bad/name", "bad\\name", "trailing.", "trailing ", "control\x01", ".", "..")
        for name in names:
            with self.subTest(name=name):
                with self.assertRaisesRegex(VERIFY.VerificationError, "Windows-incompatible"):
                    VERIFY.check_name(name)

    @unittest.skipIf(os.name == "nt", "Windows cannot create this invalid fixture name")
    def test_invalid_name_is_checked_during_inventory(self):
        root = self.copy()
        (root / "bad:name.cfg").write_bytes(b"invalid")
        self.assert_failure(root)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "Host cannot create a FIFO fixture")
    def test_special_file_is_rejected_without_reading_it(self):
        root = self.copy()
        os.mkfifo(root / "pipe")
        with self.assertRaisesRegex(VERIFY.VerificationError, "Special file"):
            VERIFY.scan(root)

    def test_changed_file_after_initial_inventory_is_rejected(self):
        root = self.copy()
        def progress(message):
            if message.startswith("Found "):
                (root / "images/pixel.bin").write_bytes(b"changed data")
        with self.assertRaisesRegex(VERIFY.VerificationError, "changed during verification"):
            VERIFY.scan(root, progress)

    def test_added_file_after_hashing_is_rejected(self):
        root = self.copy()
        def progress(message):
            if message.startswith("Checked "):
                (root / "late-arrival.cfg").write_bytes(b"copied during scan")
        with self.assertRaisesRegex(VERIFY.VerificationError, "Data changed during verification"):
            VERIFY.scan(root, progress)

    def test_interruption_returns_130_and_never_success(self):
        with patch.object(VERIFY, "scan", side_effect=KeyboardInterrupt):
            result, output, errors = self.invoke(self.root)
        self.assertEqual(result, 130)
        self.assertIn("INCOMPLETE", errors)
        self.assertNotIn(SUCCESS, output + errors)

    def test_read_error_returns_nonzero_and_never_success(self):
        with patch.object(VERIFY, "scan", side_effect=PermissionError("fixture read denied")):
            self.assert_failure(self.root)

    def test_missing_or_empty_root_returns_nonzero(self):
        self.assert_failure(self.base / "missing")
        empty = self.base / "empty"
        empty.mkdir()
        self.assert_failure(empty)

    def test_unsupported_reference_fails_before_scanning(self):
        for reference in (
            {"schema": 999, "algorithm": VERIFY.ALGORITHM},
            {"schema": 1, "algorithm": VERIFY.ALGORITHM, **self.expected, "directories": []},
            [],
        ):
            with self.subTest(reference_type=type(reference).__name__):
                self.reference.write_text(json.dumps(reference), encoding="utf-8")
                with patch.object(VERIFY, "scan") as scan:
                    self.assert_failure(self.root)
                scan.assert_not_called()


if __name__ == "__main__":
    unittest.main()
