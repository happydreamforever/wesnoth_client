"""Offline GLib prerequisite binding, source extraction and prefix guards."""

import importlib.util
import configparser
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from test_android_bootstrap import ar_library, elf_program
from test_android_glib_deps import create_fixture as create_prerequisites

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("android_glib", REPO / "tools/build_android_glib.py")
L = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(L)


def create_fixture(repo):
    create_prerequisites(repo)
    def write(root, name, content=b"fixture"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path
    for name in L.PROJECT_INPUTS:
        write(repo, name, (REPO / name).read_bytes())
    root = repo / "build/android-glib"
    for name in L.REQUIRED_HEADERS:
        write(root, "prefix/include/glib-2.0/" + name)
    write(root, "prefix/lib/glib-2.0/include/glibconfig.h")
    for name in L.REQUIRED_ARCHIVES:
        write(root, "prefix/lib/" + name, ar_library(objects=2))
    for name in L.REQUIRED_PC:
        write(root, "prefix/lib/pkgconfig/" + name, b"prefix=${pcfiledir}/../..\n")
    write(root, L.ARTIFACTS[0], elf_program(needed=("libc.so", "libz.so", "libm.so")))
    profile, inputs, _, pins = L.selected_inputs(repo)
    validation, files = L.validate_prefix(root)
    receipt = {"schema_version": 1, "kind": L.KIND, "profile": profile,
               "inputs": {"project_files": inputs, "archives": pins, "prerequisites": L.prior_inputs(repo)},
               "prefix_files": files, "artifacts": {name: files[name] for name in L.ARTIFACTS}, "validation": validation}
    write(root, "receipt.json", L.B.json_bytes(receipt))
    return root, receipt


class GLibTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wesnoth-glib-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.root = self.repo / "build/android-glib"
        self.sources = {"sources/glib-2.82.1/meson.build": b"project('fixture')\n"}
        self.generated = {"toolchain/cc": b"#!/bin/bash\nexit 0\n"}

    def write(self, name, content=b"fixture"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def prepare(self):
        return L.prepare_sources(self.root, self.sources, [], self.generated, dict(L.B.SELECTED_PROFILE), {}, {})

    def archive(self, replace=None, extra=None):
        entries = [(L.SOURCE + "/meson.build", tarfile.REGTYPE, b"meson", ""),
                   (L.SOURCE + "/LICENSES/LGPL-2.1-or-later.txt", tarfile.REGTYPE, b"license bytes", "")]
        entries += [(name, tarfile.SYMTYPE, b"", target) for name, target in L.LICENSE_ALIASES.items()]
        if replace:
            entries = [replace if item[0] == replace[0] else item for item in entries]
        if extra:
            entries.append(extra)
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:xz") as archive:
            for name, kind, data, target in entries:
                entry = tarfile.TarInfo(name)
                entry.type, entry.linkname, entry.size = kind, target, len(data)
                archive.addfile(entry, io.BytesIO(data) if data else None)
        return output.getvalue()

    def test_plan_builds_only_five_static_offline_libraries(self):
        _, inputs, package, pins = L.selected_inputs(REPO)
        self.assertEqual(len(inputs), 15)
        self.assertEqual(set(pins), {L.ARCHIVE})
        self.assertEqual(package["targets"], list(L.TARGETS))
        self.assertIn("--wrap-mode=nodownload", package["meson_options"])
        self.assertIn("-Dintrospection=disabled", package["meson_options"])
        self.assertNotIn("-Dprefer_static=true", package["meson_options"])

    def test_only_two_exact_license_aliases_are_materialized(self):
        files, executables = L.archive_files(self.archive())
        self.assertEqual(files["sources/" + L.SOURCE + "/COPYING"], b"license bytes")
        self.assertEqual(files["sources/" + L.SOURCE + "/gmodule/COPYING"], b"license bytes")
        self.assertEqual(executables, [])
        L.prepare_sources(self.root, files, executables, self.generated, dict(L.B.SELECTED_PROFILE), {}, {})
        for name in L.LICENSE_ALIASES:
            self.assertFalse((self.root / "sources" / name).is_symlink())

    def test_changed_missing_unknown_and_hardlinked_license_aliases_are_rejected(self):
        cases = [((L.SOURCE + "/COPYING", tarfile.SYMTYPE, b"", "/outside"), None),
                 ((L.SOURCE + "/COPYING", tarfile.REGTYPE, b"copied", ""), None),
                 ((L.SOURCE + "/COPYING", tarfile.LNKTYPE, b"", "LICENSES/LGPL-2.1-or-later.txt"), None),
                 (None, (L.SOURCE + "/other-link", tarfile.SYMTYPE, b"", "COPYING")),
                 ((L.SOURCE + "/LICENSES/LGPL-2.1-or-later.txt", tarfile.SYMTYPE, b"", "other"), None)]
        for replacement, extra in cases:
            with self.subTest(replacement=replacement, extra=extra), self.assertRaises(L.BuildError):
                L.archive_files(self.archive(replacement, extra))

    def test_archive_traversal_and_directory_case_collisions_are_rejected(self):
        for name in (L.SOURCE + "/../outside", L.SOURCE + "/licenses/other.txt", L.SOURCE + "/COPYING"):
            with self.subTest(name=name), self.assertRaises(L.BuildError):
                L.archive_files(self.archive(extra=(name, tarfile.REGTYPE, b"bad", "")))

    def test_meson_wrap_lock_is_managed_empty_and_not_ignored(self):
        files, executables = L.archive_files(self.archive())
        self.assertEqual(files[L.WRAP_LOCK], b"")
        marker = L.prepare_sources(self.root, files, executables, self.generated, dict(L.B.SELECTED_PROFILE), {}, {})
        lock = self.root / L.WRAP_LOCK
        self.assertEqual(marker["source_files"][L.WRAP_LOCK], {"bytes": 0, "sha256": L.B.sha256(b"")})
        # Meson's POSIX lock opens w+ without writing and leaves the file.
        with lock.open("w+", encoding="utf-8"):
            pass
        L.verify_sources(self.root, marker)
        L.prepare_sources(self.root, files, executables, self.generated, dict(L.B.SELECTED_PROFILE), {}, {})
        lock.write_bytes(b"local edit")
        with self.assertRaisesRegex(L.BuildError, "changed, added or removed"):
            L.verify_sources(self.root, marker)
        self.assertEqual(lock.read_bytes(), b"local edit")
        lock.unlink()
        with self.assertRaisesRegex(L.BuildError, "changed, added or removed"):
            L.verify_sources(self.root, marker)
        outside = self.repo / "outside-lock"
        outside.write_bytes(b"")
        lock.symlink_to(outside)
        with self.assertRaises(L.BuildError):
            L.verify_sources(self.root, marker)

    def test_archive_cannot_override_reserved_meson_lock(self):
        for name, kind in ((L.SOURCE + "/subprojects/.wraplock", tarfile.REGTYPE),
                           (L.SOURCE + "/subprojects/.wraplock", tarfile.DIRTYPE),
                           (L.SOURCE + "/subprojects/.WRAPLOCK", tarfile.REGTYPE)):
            with self.subTest(name=name, kind=kind), self.assertRaisesRegex(L.BuildError, "managed Meson lock"):
                L.archive_files(self.archive(extra=(name, kind, b"", "")))

    def test_verify_only_checks_all_three_stages_without_tools_or_cache(self):
        _, receipt = create_fixture(self.repo)
        before = {stage: L.M.inventory(self.repo / "build" / stage) for stage in ("android-text-base", "android-glib-deps", "android-glib")}
        with patch.object(L.T, "run", side_effect=AssertionError("external command")), \
             patch.object(L.M, "archive_data", side_effect=AssertionError("archive access")), \
             patch.object(L.T, "tools_and_versions", side_effect=AssertionError("tool/NDK access")):
            self.assertEqual(L.verify_receipt(self.repo), receipt)
        self.assertEqual(before, {stage: L.M.inventory(self.repo / "build" / stage) for stage in before})

    def test_modified_header_in_either_prerequisite_is_preserved_and_rejected(self):
        create_fixture(self.repo)
        for relative in ("build/android-text-base/prefix/include/iconv.h", "build/android-glib-deps/prefix/include/ffi.h"):
            with self.subTest(relative=relative):
                path = self.repo / relative
                original = path.read_bytes()
                path.write_bytes(b"local edit")
                with self.assertRaises(L.BuildError):
                    L.verify_receipt(self.repo)
                self.assertEqual(path.read_bytes(), b"local edit")
                path.write_bytes(original)

    def test_changed_prerequisite_receipt_is_rejected_even_if_valid(self):
        create_fixture(self.repo)
        path = self.repo / "build/android-glib-deps/receipt.json"
        receipt = L.B.read_json(path)
        receipt["tools"] = {"new": "environment"}
        path.write_bytes(L.B.json_bytes(receipt))
        L.G.verify_receipt(self.repo)
        with self.assertRaisesRegex(L.BuildError, "receipt/input/artifact hashes differ"):
            L.verify_receipt(self.repo)

    def test_changed_probe_input_or_installed_header_is_rejected(self):
        create_fixture(self.repo)
        for path in (self.repo / "tools/native-glib/probe.c", self.root / "prefix/include/glib-2.0/glib.h"):
            with self.subTest(path=path):
                original = path.read_bytes()
                path.write_bytes(original + b" changed")
                with self.assertRaisesRegex(L.BuildError, "receipt/input/artifact hashes differ"):
                    L.verify_receipt(self.repo)
                path.write_bytes(original)

    def test_probe_requires_android_system_zlib_and_rejects_extra_shared_dependency(self):
        create_fixture(self.repo)
        for dependencies in (("libc.so",), ("libc.so", "libz.so", "libglib-2.0.so")):
            self.write(L.ARTIFACTS[0], elf_program(needed=dependencies))
            with self.assertRaises(L.BuildError):
                L.validate_prefix(self.root)

    def test_unmanaged_edited_sources_and_prefixes_are_preserved(self):
        sentinel = self.write("user.txt", b"keep")
        with self.assertRaisesRegex(L.BuildError, "unmanaged"):
            self.prepare()
        self.assertEqual(sentinel.read_bytes(), b"keep")
        sentinel.unlink()
        self.root.rmdir()
        self.prepare()
        source = self.write("sources/glib-2.82.1/meson.build", b"changed source")
        with self.assertRaisesRegex(L.BuildError, "changed, added or removed"):
            self.prepare()
        self.assertEqual(source.read_bytes(), b"changed source")
        header = self.write("prefix/include/glib-2.0/glib.h", b"original")
        L.save_prefix_state(self.root)
        header.write_bytes(b"local edit")
        with self.assertRaisesRegex(L.BuildError, "changed or an install"):
            L.verify_prefix(self.root)
        self.assertEqual(header.read_bytes(), b"local edit")

    def test_private_build_or_prefix_links_cannot_redirect_writes(self):
        self.prepare()
        outside = self.repo / "outside"
        outside.mkdir()
        link = self.root / "build"
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(L.BuildError):
            self.prepare()
        self.assertEqual(list(outside.iterdir()), [])

    def test_cross_file_selects_target_compilers_and_only_verified_dependencies(self):
        ndk = {name: Path("/c/NDK with spaces") / name for name in ("clang", "clang++", "llvm-ar", "llvm-ranlib", "llvm-strip", "llvm-nm", "llvm-objdump")}
        with patch.object(L.T, "is_msys", return_value=True):
            files = L.generated_files(self.root, self.repo, ndk, {"pkg-config": Path("/usr/bin/pkg-config")})
        cross = files["toolchain/android.ini"].decode()
        self.assertIn("system = 'android'", cross)
        self.assertIn("needs_exe_wrapper = true", cross)
        self.assertIn("pkg_config_libdir =", cross)
        self.assertIn(str(self.repo / "build/android-text-base/prefix/lib/pkgconfig"), cross)
        self.assertIn(str(self.repo / "build/android-glib-deps/prefix/lib/pkgconfig"), cross)
        self.assertNotIn("/usr/lib/pkgconfig", cross)
        self.assertIn("-DGIO_MODULE_DIR=", files["toolchain/cc"].decode())
        parsed = configparser.ConfigParser()
        parsed.read_string(cross)
        self.assertIn("pkg_config_libdir", parsed["properties"])
        self.assertNotIn("pkg_config_libdir", parsed["built-in options"])
        for compiler in ("cc", "cxx"):
            self.assertIn("-DGLIB_CHARSETALIAS_DIR=;-DLOCALSTATEDIR=", files["toolchain/" + compiler].decode())

    def test_public_install_selection_includes_unix_headers_and_notifyqueue(self):
        mapping = {}
        for target in L.TARGETS:
            self.write("build/glib/" + target, ar_library())
        for source, destination in (("sources/glib-2.82.1/glib/glib.h", "include/glib-2.0/glib.h"),
                                    ("sources/glib-2.82.1/gobject/gobjectnotifyqueue.c", "include/glib-2.0/gobject/gobjectnotifyqueue.c"),
                                    ("sources/glib-2.82.1/gio/gunixinputstream.h", "include/gio-unix-2.0/gio/gunixinputstream.h"),
                                    ("build/glib/glib/glibconfig.h", "lib/glib-2.0/include/glibconfig.h")):
            path = self.write(source)
            mapping[str(path)] = "/usr/" + destination
        mapping[str(self.repo / "not-built-helper")] = "/usr/bin/glib-compile-resources"
        mapping[str(self.root / "build/glib/girepository/gi-visibility.h")] = "/usr/include/glib-2.0/girepository/gi-visibility.h"
        self.write("build/glib/meson-info/intro-installed.json", L.B.json_bytes(mapping))
        L.install_development_files(self.root)
        self.assertTrue((self.root / "prefix/include/glib-2.0/gobject/gobjectnotifyqueue.c").is_file())
        self.assertTrue((self.root / "prefix/include/gio-unix-2.0/gio/gunixinputstream.h").is_file())
        self.assertFalse((self.root / "prefix/bin").exists())
        self.assertFalse((self.root / "prefix/include/glib-2.0/girepository").exists())

    def test_install_manifest_cannot_read_or_write_outside_owned_paths(self):
        for source, destination in ((str(self.repo / "outside.h"), "/usr/include/glib-2.0/outside.h"),
                                    (str(self.root / "sources/glib-2.82.1/../../../../outside.h"), "/usr/include/glib-2.0/outside.h"),
                                    (str(self.root / "sources/glib-2.82.1/file.h"), "/usr/include/glib-2.0/glib/../../../../outside.h")):
            with self.subTest(source=source, destination=destination):
                self.write("build/glib/meson-info/intro-installed.json", L.B.json_bytes({source: destination}))
                with self.assertRaises(L.BuildError):
                    L.install_development_files(self.root)

    def test_static_package_metadata_records_transitive_iconv_and_intl_discovery(self):
        for name in L.REQUIRED_PC:
            self.write("prefix/lib/pkgconfig/" + name, b"prefix=/usr\nlibdir=/usr/lib\nglib_mkenums=${bindir}/glib-mkenums\nschemasdir=${datadir}/glib-2.0/schemas\nName: fixture\nLibs: -lglib-2.0 -lintl -liconv\n")
        L.normalize_metadata(self.root, self.repo)
        text = (self.root / "prefix/lib/pkgconfig/glib-2.0.pc").read_text()
        self.assertIn("prefix=${pcfiledir}/../..", text)
        self.assertIn("Requires.private: intl iconv", text)
        self.assertNotIn(str(self.repo), text)
        self.assertNotIn("glib_mkenums=", text)
        self.assertIn("schemasdir=", text)


if __name__ == "__main__":
    unittest.main()
