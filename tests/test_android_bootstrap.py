"""Check bootstrap input preservation and Android binary validation boundaries."""

import importlib.util
import io
import json
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("android_bootstrap", REPO / "tools/build_android_bootstrap.py")
BOOTSTRAP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOOTSTRAP)


def elf_object(machine=62, kind=1):
    return b"\x7fELF\x02\x01\x01" + b"\0" * 9 + struct.pack("<HHIQQQIHHHHHH", kind, machine, 1, 0, 0, 0, 0, 64, 0, 0, 0, 0, 0)


def elf_program(needed=("libc++_shared.so", "libz.so", "libc.so"), api=23,
                interpreter="/system/bin/linker64", rpath=False):
    """A small deterministic ELF fixture with load/dynamic/interp/Android notes."""
    base = 0x1000
    data = bytearray(1024)
    data[:64] = elf_object(kind=3)
    struct.pack_into("<Q", data, 32, 64)
    struct.pack_into("<HH", data, 54, 56, 4)
    strings = b"\0"
    offsets = []
    for name in needed:
        offsets.append(len(strings))
        strings += name.encode("ascii") + b"\0"
    data[512:512 + len(strings)] = strings
    dynamic = [(5, base + 512), (10, len(strings))] + [(1, offset) for offset in offsets]
    if rpath:
        dynamic.append((29, 0))
    dynamic.append((0, 0))
    for i, pair in enumerate(dynamic):
        struct.pack_into("<qQ", data, 320 + 16 * i, *pair)
    interpreter_bytes = interpreter.encode("ascii") + b"\0"
    data[700:700 + len(interpreter_bytes)] = interpreter_bytes
    note = struct.pack("<III", 8, 4, 1) + b"Android\0" + struct.pack("<I", api)
    data[800:800 + len(note)] = note
    for i, (kind, offset, virtual, size) in enumerate((
        (1, 0, base, len(data)), (2, 320, base + 320, len(dynamic) * 16),
        (3, 700, base + 700, len(interpreter_bytes)), (4, 800, base + 800, len(note)),
    )):
        struct.pack_into("<IIQQQQQQ", data, 64 + i * 56, kind, 4, offset, virtual, virtual, size, size, 8)
    return bytes(data)


def ar_library(objects=7, machine=62):
    data = b"!<arch>\n"
    for i in range(objects):
        obj = elf_object(machine)
        header = (f"unit{i}.o/".ljust(16) + "0".ljust(12) + "0".ljust(6) + "0".ljust(6)
                  + "644".ljust(8) + str(len(obj)).ljust(10) + "`\n").encode("ascii")
        data += header + obj
    return data


def tar_bytes(extra=None):
    names = ("blocksort.c", "huffman.c", "crctable.c", "randtable.c", "compress.c",
             "decompress.c", "bzlib.c", "bzlib.h", "bzlib_private.h", "LICENSE")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name in names:
            entry = tarfile.TarInfo("bzip2-1.0.8/" + name)
            content = (name + " fixture").encode("ascii")
            entry.size = len(content)
            archive.addfile(entry, io.BytesIO(content))
        if extra:
            entry = tarfile.TarInfo(extra[0])
            entry.type = extra[1]
            entry.linkname = "../../outside"
            entry.size = 0
            archive.addfile(entry, io.BytesIO())
    return buffer.getvalue()


class AndroidBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wesnoth native build ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.root = self.repo / "build/android-native-bootstrap"
        self.data = tar_bytes()
        self.pin = {"name": BOOTSTRAP.ARCHIVE_NAME, "bytes": len(self.data), "sha256": BOOTSTRAP.sha256(self.data)}
        self.sources = BOOTSTRAP.archive_files(self.data)
        self.profile = dict(BOOTSTRAP.SELECTED_PROFILE)
        self.ndk = self.base / "Android SDK/ndk/26.3.11579264"
        self.cmake = self.base / "CMake/bin/cmake"
        self.ninja = self.base / "ninja"

    def prepare(self):
        return BOOTSTRAP.prepare_work(self.root, self.pin, self.profile, self.sources, self.ndk, self.cmake, self.ninja)

    def test_valid_archive_extracts_only_expected_library_sources(self):
        self.prepare()
        self.assertTrue((self.root / BOOTSTRAP.MARKER).is_file())
        for name, data in self.sources.items():
            self.assertEqual((self.root / name).read_bytes(), data)
        self.assertEqual((self.root / "sources/bzip2-1.0.8/LICENSE").read_bytes(), b"LICENSE fixture")

    def test_archive_identity_checked_before_extraction(self):
        folder = self.repo / "packaging/android/download"
        folder.mkdir(parents=True)
        path = folder / self.pin["name"]
        path.write_bytes(self.data)
        selected, content = BOOTSTRAP.find_archive(self.repo, self.pin)
        self.assertEqual(selected, path)
        self.assertEqual(content, self.data)
        path.write_bytes(bytes([self.data[0] ^ 1]) + self.data[1:])
        with self.assertRaisesRegex(BOOTSTRAP.BuildError, "SHA256 differs"):
            BOOTSTRAP.find_archive(self.repo, self.pin)
        self.assertFalse(self.root.exists())

    def test_explicit_archive_directory_does_not_silently_fall_back(self):
        folder = self.repo / "packaging/android/download"
        folder.mkdir(parents=True)
        (folder / self.pin["name"]).write_bytes(self.data)
        with self.assertRaisesRegex(BOOTSTRAP.BuildError, "missing"):
            BOOTSTRAP.find_archive(self.repo, self.pin, self.base / "unavailable")

    def test_windows_msys_archive_cache_is_supported_without_changing_override(self):
        cache = self.base / "msys64/tmp/android-dl"
        cache.mkdir(parents=True)
        (cache / self.pin["name"]).write_bytes(self.data)
        with (patch.object(BOOTSTRAP.sys, "platform", "win32"),
              patch.object(BOOTSTRAP, "Path", return_value=cache) as cache_path):
            selected, content = BOOTSTRAP.find_archive(self.repo, self.pin)
            self.assertEqual(selected, cache / self.pin["name"])
            self.assertEqual(content, self.data)
            cache_path.assert_called_once_with("D:/Test/msys64/tmp/android-dl")
            with self.assertRaisesRegex(BOOTSTRAP.BuildError, "missing"):
                BOOTSTRAP.find_archive(self.repo, self.pin, self.base / "unavailable")
            self.assertEqual(cache_path.call_count, 1)

    def test_unsafe_archive_members_are_rejected(self):
        for name, kind in (
            ("bzip2-1.0.8/../../outside", tarfile.REGTYPE),
            ("/absolute", tarfile.REGTYPE), ("bzip2-1.0.8/c:\\outside", tarfile.REGTYPE),
            ("bzip2-1.0.8/NUL.txt", tarfile.REGTYPE), ("bzip2-1.0.8/file. ", tarfile.REGTYPE),
            ("bzip2-1.0.8/link", tarfile.SYMTYPE), ("bzip2-1.0.8/hardlink", tarfile.LNKTYPE),
            ("bzip2-1.0.8/BZLIB.C", tarfile.REGTYPE),
        ):
            with self.subTest(name=name):
                with self.assertRaises(BOOTSTRAP.BuildError):
                    BOOTSTRAP.archive_files(tar_bytes((name, kind)))
        self.assertFalse((self.base / "outside").exists())

    def test_unmanaged_existing_work_is_preserved(self):
        self.root.mkdir(parents=True)
        user_file = self.root / "user.txt"
        user_file.write_text("preserve")
        with self.assertRaisesRegex(BOOTSTRAP.BuildError, "unmanaged"):
            self.prepare()
        self.assertEqual(user_file.read_text(), "preserve")
        self.assertEqual(list(self.root.iterdir()), [user_file])

    def test_unchanged_sources_can_be_reused_without_rewriting(self):
        self.prepare()
        before = {p: p.stat().st_mtime_ns for p in self.root.rglob("*") if p.is_file()}
        self.prepare()
        for path, stamp in before.items():
            self.assertEqual(path.stat().st_mtime_ns, stamp)

    def test_changed_source_is_preserved(self):
        self.prepare()
        edited = self.root / "sources/bzip2-1.0.8/bzlib.c"
        edited.write_text("user edit")
        with self.assertRaisesRegex(BOOTSTRAP.BuildError, "source was edited"):
            self.prepare()
        self.assertEqual(edited.read_text(), "user edit")

    def test_extra_source_is_preserved(self):
        self.prepare()
        extra = self.root / "sources/bzip2-1.0.8/user.txt"
        extra.write_text("preserve")
        with self.assertRaisesRegex(BOOTSTRAP.BuildError, "added or missing"):
            self.prepare()
        self.assertEqual(extra.read_text(), "preserve")

    def test_different_toolchain_does_not_reuse_existing_build(self):
        self.prepare()
        self.ndk = self.base / "different-ndk"
        with self.assertRaisesRegex(BOOTSTRAP.BuildError, "different inputs"):
            self.prepare()

    def test_modified_prior_binary_blocks_rebuild(self):
        self.prepare()
        files = dict(zip(BOOTSTRAP.ARTIFACTS, (elf_program(), elf_program(), ar_library())))
        for name, data in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        receipt = {"schema_version": 1, "kind": BOOTSTRAP.KIND, "profile": self.profile,
                   "artifacts": {name: BOOTSTRAP.fingerprint(self.root / name) for name in files}}
        (self.root / "receipt.json").write_bytes(BOOTSTRAP.json_bytes(receipt))
        target = self.root / BOOTSTRAP.ARTIFACTS[0]
        target.write_bytes(b"user bytes")
        with self.assertRaisesRegex(BOOTSTRAP.BuildError, "artifact was edited"):
            self.prepare()
        self.assertEqual(target.read_bytes(), b"user bytes")

    def test_android_elf_identity_and_needed_libraries(self):
        result = BOOTSTRAP.inspect_shared_elf(elf_program(), require_executable=True)
        self.assertEqual(result["interpreter"], "/system/bin/linker64")
        self.assertEqual(result["android_api_notes"], [23])
        self.assertEqual(result["machine"], "x86_64")
        self.assertIn("libc++_shared.so", result["needed"])

    def test_host_elf_wrong_api_rpath_and_wrong_libraries_are_rejected(self):
        for data in (
            elf_program(api=28), elf_program(interpreter="/lib64/ld-linux-x86-64.so.2"),
            elf_program(rpath=True), elf_program(needed=("libstdc++.so.6",)),
            elf_program(needed=("libc++_shared.so", "libc.so")), b"MZ" + b"\0" * 1024,
        ):
            with self.subTest(header=data[:20]):
                with self.assertRaises(BOOTSTRAP.BuildError):
                    BOOTSTRAP.inspect_shared_elf(data, require_executable=True)

    def test_truncated_elf_and_wrong_architecture_are_rejected(self):
        for data in (elf_program()[:250], elf_object(machine=183), b"\x7fELF\x01\x01" + b"\0" * 100):
            with self.assertRaises(BOOTSTRAP.BuildError):
                BOOTSTRAP.inspect_shared_elf(data, require_executable=True)

    def test_static_archive_requires_seven_android_compatible_objects(self):
        result = BOOTSTRAP.inspect_static_archive(ar_library())
        self.assertEqual(result["elf_object_count"], 7)
        for data in (ar_library(objects=6), ar_library(machine=183), b"!<thin>\n", ar_library()[:-1]):
            with self.assertRaises(BOOTSTRAP.BuildError):
                BOOTSTRAP.inspect_static_archive(data)

    def test_subprocess_uses_argument_array_not_shell(self):
        cmake = self.base / "Program Files/CMake/bin/cmake.exe"
        with patch.object(BOOTSTRAP.subprocess, "run") as execute:
            execute.return_value.returncode = 0
            BOOTSTRAP.run([cmake, "-DBZIP2_SOURCE_DIR=C:/path with spaces/source"])
        arguments, options = execute.call_args
        self.assertEqual(arguments[0], [str(cmake), "-DBZIP2_SOURCE_DIR=C:/path with spaces/source"])
        self.assertNotIn("shell", options)

    def test_tool_versions_accept_known_ninja_vendor_suffix_and_record_it(self):
        for path in (self.cmake, self.ninja):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
        for version in ("1.13.2", "1.13.2.git.kitware.jobserver-pipe-1", "1.13.2-vendor.1"):
            with self.subTest(version=version), patch.object(BOOTSTRAP, "run", side_effect=[
                "cmake version 4.3.0\n", version + "\n", "Android clang version 17.0.2\n",
            ]):
                result = BOOTSTRAP.check_tools(self.cmake, self.ninja, {"clang++": self.base / "clang++"})
                self.assertEqual(result["ninja"], version)

    def test_silent_or_incompatible_tools_fail_as_normal_build_errors(self):
        for path in (self.cmake, self.ninja):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
        for versions in (
            ("", "1.13.2", "clang version 17.0.2"),
            ("cmake version 4.3.0", "", "clang version 17.0.2"),
            ("cmake version 4.3.0", "1.13.2", ""),
            ("cmake version 4.3.1", "1.13.2", "clang version 17.0.2"),
            ("cmake version 4.3.0", "1.13.20", "clang version 17.0.2"),
            ("cmake version 4.3.0", "1.13.2", "clang version 18.0.2"),
        ):
            with self.subTest(versions=versions), patch.object(BOOTSTRAP, "run", side_effect=versions):
                with self.assertRaises(BOOTSTRAP.BuildError):
                    BOOTSTRAP.check_tools(self.cmake, self.ninja, {"clang++": self.base / "clang++"})

    def test_failed_build_removes_stale_receipt_before_commands(self):
        (self.repo / "tools").mkdir()
        self.prepare()
        files = dict(zip(BOOTSTRAP.ARTIFACTS, (elf_program(), elf_program(), ar_library())))
        for name, data in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        receipt = {"schema_version": 1, "kind": BOOTSTRAP.KIND, "profile": self.profile,
                   "artifacts": {name: BOOTSTRAP.fingerprint(self.root / name) for name in files}}
        receipt_path = self.root / "receipt.json"
        receipt_path.write_bytes(BOOTSTRAP.json_bytes(receipt))
        with (patch.object(BOOTSTRAP, "profile_and_inputs", return_value=(self.profile, {})),
              patch.object(BOOTSTRAP, "archive_pin", return_value=self.pin),
              patch.object(BOOTSTRAP, "find_archive", return_value=(self.base / self.pin["name"], self.data)),
              patch.object(BOOTSTRAP, "host_tag", return_value="linux-x86_64"),
              patch.object(BOOTSTRAP, "ndk_paths", return_value={"cmake_toolchain": self.ndk / "toolchain.cmake"}),
              patch.object(BOOTSTRAP, "check_tools", return_value={}),
              patch.object(BOOTSTRAP, "run", side_effect=BOOTSTRAP.BuildError("simulated compiler error"))):
            with self.assertRaisesRegex(BOOTSTRAP.BuildError, "simulated compiler"):
                BOOTSTRAP.build(self.repo, self.ndk, self.cmake, self.ninja, 2)
        self.assertFalse(receipt_path.exists())
        self.assertEqual((self.root / BOOTSTRAP.ARTIFACTS[0]).read_bytes(), files[BOOTSTRAP.ARTIFACTS[0]])


if __name__ == "__main__":
    unittest.main()
