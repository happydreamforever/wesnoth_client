"""Offline package preservation tests; no downloads or global Python writes."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile


TOOL_PATH = Path(__file__).resolve().parents[1] / "tools" / "prepare_scons.py"
SPEC = importlib.util.spec_from_file_location("private_scons_tool", TOOL_PATH)
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)


def fixture_wheel(extra=None):
    files = {
        "SCons/__init__.py": b'__version__ = "4.11.0"\n',
        "SCons/__main__.py": b'print("fixture")\n',
        "scons-4.11.0.dist-info/WHEEL": b"Root-Is-Purelib: true\nTag: py3-none-any\n",
    }
    files.update(extra or {})
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


class PrivateSConsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="wesnoth-scons-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)
        self.data = fixture_wheel()
        self.package = {
            "schema_version": 1, "version": "4.11.0",
            "filename": "scons-4.11.0-py3-none-any.whl",
            "url": "https://files.pythonhosted.org/packages/fixture/scons-4.11.0-py3-none-any.whl",
            "bytes": len(self.data), "sha256": hashlib.sha256(self.data).hexdigest(),
        }
        self.cache = self.repo / "build/downloads" / self.package["filename"]

    def cache_wheel(self):
        self.cache.parent.mkdir(parents=True)
        self.cache.write_bytes(self.data)

    def test_missing_cache_does_not_request_network(self):
        with patch.object(TOOL.urllib.request, "urlopen", side_effect=AssertionError("network")):
            with self.assertRaisesRegex(TOOL.PackageError, "wheel is missing"):
                TOOL.prepare(self.repo, self.package)
        self.assertFalse((self.repo / "build").exists())

    def test_prepare_and_reuse_are_offline(self):
        self.cache_wheel()
        with patch.object(TOOL.urllib.request, "urlopen", side_effect=AssertionError("network")):
            self.assertEqual(TOOL.prepare(self.repo, self.package), (True, 3))
            destination = TOOL.installed_path(self.repo, self.package)
            before = {p.relative_to(destination): (p.read_bytes(), p.stat().st_mtime_ns)
                      for p in destination.rglob("*") if p.is_file()}
            self.assertEqual(TOOL.prepare(self.repo, self.package), (False, 3))
            after = {p.relative_to(destination): (p.read_bytes(), p.stat().st_mtime_ns)
                     for p in destination.rglob("*") if p.is_file()}
            self.assertEqual(before, after)

    def test_corrupt_cached_wheel_preserved_even_with_download(self):
        self.cache_wheel()
        self.cache.write_bytes(b"existing user contents")
        with patch.object(TOOL.urllib.request, "urlopen", side_effect=AssertionError("network")):
            with self.assertRaisesRegex(TOOL.PackageError, "SHA256 differs"):
                TOOL.prepare(self.repo, self.package, download=True)
        self.assertEqual(self.cache.read_bytes(), b"existing user contents")
        self.assertFalse((self.repo / "build/tools").exists())

    def test_modified_private_file_preserved(self):
        self.cache_wheel()
        TOOL.prepare(self.repo, self.package)
        edited = TOOL.installed_path(self.repo, self.package) / "SCons/__init__.py"
        edited.write_bytes(b"local edit")
        with self.assertRaisesRegex(TOOL.PackageError, "was changed"):
            TOOL.prepare(self.repo, self.package)
        self.assertEqual(edited.read_bytes(), b"local edit")

    def test_interrupted_or_unmanaged_destination_preserved(self):
        self.cache_wheel()
        destination = TOOL.installed_path(self.repo, self.package)
        destination.mkdir(parents=True)
        (destination / "user.txt").write_bytes(b"keep")
        with self.assertRaisesRegex(TOOL.PackageError, "additional or missing files"):
            TOOL.prepare(self.repo, self.package)
        self.assertEqual((destination / "user.txt").read_bytes(), b"keep")
        self.assertFalse((destination / TOOL.MARKER).exists())

    def test_incomplete_write_is_not_marked_complete(self):
        self.cache_wheel()
        original_open = Path.open

        def fail_write(path, *args, **kwargs):
            if path.name == "__main__.py" and args and args[0] == "xb":
                raise OSError("simulated sharing conflict")
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", fail_write):
            with self.assertRaisesRegex(TOOL.PackageError, "Incomplete private SCons retained"):
                TOOL.prepare(self.repo, self.package)
        destination = TOOL.installed_path(self.repo, self.package)
        self.assertTrue((destination / "SCons/__init__.py").exists())
        self.assertFalse((destination / TOOL.MARKER).exists())
        with self.assertRaisesRegex(TOOL.PackageError, "additional or missing files"):
            TOOL.prepare(self.repo, self.package)

    def test_path_traversal_and_case_duplicates_rejected(self):
        for name in ("SCons/../../outside.py", "/SCons/absolute.py", "SCons\\bad.py", "scons/__init__.py", "SCons/__INIT__.py"):
            with self.subTest(name=name):
                data = fixture_wheel({name: b"bad"})
                package = {**self.package, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                with self.assertRaises(TOOL.PackageError):
                    TOOL.wheel_files(data, package)
        self.assertFalse((self.repo / "outside.py").exists())

    def test_symlinked_cache_or_destination_refused(self):
        self.cache_wheel()
        external = self.repo / "external"
        external.mkdir()
        (self.repo / "build/tools").symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(TOOL.PackageError, "Symbolic link or junction"):
            TOOL.prepare(self.repo, self.package)
        self.assertEqual(list(external.iterdir()), [])

    def test_only_explicit_download_fetches_and_validates(self):
        class Response(io.BytesIO):
            def geturl(inner_self):
                return self.package["url"]

        with patch.object(TOOL.urllib.request, "urlopen", return_value=Response(self.data)) as fetch:
            self.assertEqual(TOOL.prepare(self.repo, self.package, download=True), (True, 3))
            fetch.assert_called_once()
        self.assertEqual(self.cache.read_bytes(), self.data)

    def test_bad_download_leaves_no_cache_or_install(self):
        class Response(io.BytesIO):
            def geturl(inner_self):
                return self.package["url"]

        with patch.object(TOOL.urllib.request, "urlopen", return_value=Response(b"truncated")):
            with self.assertRaisesRegex(TOOL.PackageError, "SHA256 differs"):
                TOOL.prepare(self.repo, self.package, download=True)
        self.assertFalse((self.repo / "build").exists())

    def test_declaration_rejects_alternate_or_credential_url(self):
        path = self.repo / "package.json"
        for url in ("http://files.pythonhosted.org/scons-4.11.0-py3-none-any.whl",
                    "https://example.com/scons-4.11.0-py3-none-any.whl",
                    "https://token@files.pythonhosted.org/scons-4.11.0-py3-none-any.whl"):
            with self.subTest(url=url):
                path.write_text(json.dumps({**self.package, "url": url}))
                with self.assertRaisesRegex(TOOL.PackageError, "download URL"):
                    TOOL.load_package(path)


if __name__ == "__main__":
    unittest.main()
