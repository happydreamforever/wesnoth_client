"""GLib dependency preservation, prerequisite binding and offline verification."""

import importlib.util
import io
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from test_android_bootstrap import ar_library, elf_program

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("android_glib_deps", REPO / "tools/build_android_glib_deps.py")
G = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(G)


def create_fixture(repo):
    """Write synthetic Android artifacts, bound by the real receipt validators."""
    def write(root, name, content=b"fixture"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    for name in G.PROJECT_INPUTS:
        write(repo, name, (REPO / name).read_bytes())
    text_root = repo / "build/android-text-base"
    for name in G.T.REQUIRED_HEADERS:
        write(text_root, "prefix/include/" + name)
    for name in G.T.REQUIRED_ARCHIVES:
        write(text_root, "prefix/lib/" + name, ar_library(objects=2))
    for name in G.T.REQUIRED_PC:
        write(text_root, "prefix/lib/pkgconfig/" + name, b"prefix=${pcfiledir}/../..\n")
    write(text_root, G.T.ARTIFACTS[0], elf_program(needed=("libc.so",)))
    profile, inputs, _, pins = G.T.selected_inputs(repo)
    validation, files = G.T.validate_prefix(text_root)
    text_receipt = {"schema_version": 1, "kind": G.T.KIND, "profile": profile,
                    "inputs": {"project_files": inputs, "archives": pins}, "prefix_files": files,
                    "artifacts": {name: files[name] for name in G.T.ARTIFACTS}, "validation": validation}
    write(text_root, "receipt.json", G.B.json_bytes(text_receipt))

    root = repo / "build/android-glib-deps"
    for name in G.REQUIRED_HEADERS:
        write(root, "prefix/include/" + name)
    for name in G.REQUIRED_ARCHIVES:
        write(root, "prefix/lib/" + name, ar_library(objects=2))
    for name in G.REQUIRED_PC:
        write(root, "prefix/lib/pkgconfig/" + name, b"prefix=${pcfiledir}/../..\n")
    write(root, G.ARTIFACTS[0], elf_program(needed=("libc.so", "libdl.so")))
    write(root, G.ARTIFACTS[1], (repo / G.FIXTURE).read_bytes())
    profile, inputs, _, pins = G.selected_inputs(repo)
    validation, files = G.validate_prefix(root)
    receipt = {"schema_version": 1, "kind": G.KIND, "profile": profile,
               "inputs": {"project_files": inputs, "archives": pins, "text_base": G.text_base_inputs(repo)},
               "prefix_files": files, "artifacts": {name: files[name] for name in G.ARTIFACTS}, "validation": validation}
    write(root, "receipt.json", G.B.json_bytes(receipt))
    return root, receipt


class GLibDepsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wesnoth-glib-deps-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.root = self.repo / "build/android-glib-deps"
        self.profile = dict(G.B.SELECTED_PROFILE)
        self.sources = {"sources/libffi-3.4.4/configure": b"#!/bin/sh\nexit 0\n"}
        self.generated = {"toolchain/cc": b"#!/bin/bash\nexit 0\n"}
        self.upstream = {"receipt": {"bytes": 1, "sha256": "0" * 64}, "prefix_files": {}}

    def prepare(self, upstream=None):
        return G.prepare_sources(self.root, self.sources, list(self.sources), self.generated,
                                 self.profile, {}, upstream or self.upstream)

    def write(self, name, content=b"fixture"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def archive(self, members):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            for name, kind in members:
                entry = tarfile.TarInfo(name)
                entry.type = kind
                entry.mode = 0o4755
                entry.size = 7 if kind == tarfile.REGTYPE else 0
                if kind == tarfile.SYMTYPE:
                    entry.linkname = "../../outside"
                archive.addfile(entry, io.BytesIO(b"fixture") if entry.size else None)
        return output.getvalue()

    def test_real_plan_keeps_real_intl_and_unicode_pcre_interpreter(self):
        profile, inputs, packages, pins = G.selected_inputs(REPO)
        self.assertEqual(profile, self.profile)
        self.assertEqual(len(inputs), 12)
        self.assertEqual(set(pins), {p[1] for p in G.PACKAGES})
        self.assertNotIn("--disable-nls", packages[0]["configure_options"])
        self.assertIn("--disable-jit", packages[1]["configure_options"])
        self.assertIn("--enable-builddir=.", packages[2]["configure_options"])
        self.assertNotIn("--disable-builddir", packages[2]["configure_options"])

    def test_verify_only_needs_neither_ndk_nor_build_tools_and_preserves_upstream(self):
        root, expected = create_fixture(self.repo)
        before = G.M.inventory(self.repo / "build/android-text-base")
        with patch.object(G.T, "run", side_effect=AssertionError("tool executed")), \
             patch.object(G.T, "tools_and_versions", side_effect=AssertionError("NDK inspected")), \
             patch.object(G.M, "archive_data", side_effect=AssertionError("source cache inspected")):
            self.assertEqual(G.verify_receipt(self.repo), expected)
        self.assertEqual(before, G.M.inventory(self.repo / "build/android-text-base"))
        self.assertFalse((root / "sources").exists())

    def test_modified_prerequisite_archive_is_rejected_and_preserved(self):
        create_fixture(self.repo)
        path = self.repo / "build/android-text-base/prefix/lib/libiconv.a"
        changed = ar_library(objects=3)
        path.write_bytes(changed)
        with self.assertRaisesRegex(G.BuildError, "Text receipt"):
            G.verify_receipt(self.repo)
        self.assertEqual(path.read_bytes(), changed)

    def test_changed_prerequisite_receipt_is_bound_even_if_artifacts_are_same(self):
        create_fixture(self.repo)
        path = self.repo / "build/android-text-base/receipt.json"
        receipt = G.B.read_json(path)
        receipt["tools"] = {"changed": "new build environment"}
        path.write_bytes(G.B.json_bytes(receipt))
        # It is a valid prior receipt, but a different prerequisite build.
        G.T.verify_receipt(self.repo)
        with self.assertRaisesRegex(G.BuildError, "receipt/input/artifact hashes differ"):
            G.verify_receipt(self.repo)

    def test_changed_catalog_source_or_installed_catalog_is_rejected(self):
        create_fixture(self.repo)
        for path in (self.repo / G.FIXTURE, self.root / G.ARTIFACTS[1]):
            with self.subTest(path=path):
                original = path.read_bytes()
                path.write_bytes(original + b"changed")
                with self.assertRaisesRegex(G.BuildError, "receipt/input/artifact hashes differ"):
                    G.verify_receipt(self.repo)
                path.write_bytes(original)

    def test_changed_tool_or_plan_is_rejected(self):
        create_fixture(self.repo)
        script = self.repo / "tools/build_android_glib_deps.py"
        script.write_bytes(script.read_bytes() + b"\n# local edit\n")
        with self.assertRaisesRegex(G.BuildError, "receipt/input/artifact hashes differ"):
            G.verify_receipt(self.repo)
        self.assertTrue(script.read_bytes().endswith(b"# local edit\n"))

    def test_host_binary_or_unexpected_shared_dependency_is_rejected(self):
        create_fixture(self.repo)
        for data in (b"MZ invalid Windows binary", elf_program(needed=("libc.so", "libintl.so"))):
            self.write(G.ARTIFACTS[0], data)
            with self.assertRaises(G.BuildError):
                G.verify_receipt(self.repo)

    def test_shared_library_extra_archive_or_absolute_metadata_is_rejected(self):
        create_fixture(self.repo)
        for name, content in (("prefix/lib/libunexpected.a", ar_library()),
                              ("prefix/lib/libffi.so", b"unexpected"),
                              ("prefix/lib/pkgconfig/host.pc", b"prefix=/workspace/host\n")):
            with self.subTest(name=name):
                path = self.write(name, content)
                with self.assertRaises(G.BuildError):
                    G.validate_prefix(self.root)
                path.unlink()

    def test_unmanaged_or_edited_source_is_preserved(self):
        sentinel = self.write("user.txt", b"keep")
        with self.assertRaisesRegex(G.BuildError, "unmanaged"):
            self.prepare()
        self.assertEqual(sentinel.read_bytes(), b"keep")
        sentinel.unlink()
        self.root.rmdir()
        self.prepare()
        source = self.write("sources/libffi-3.4.4/configure", b"user changes")
        with self.assertRaisesRegex(G.BuildError, "changed, added or removed"):
            self.prepare()
        self.assertEqual(source.read_bytes(), b"user changes")

    def test_prior_stage_binding_prevents_reuse_with_other_prerequisite(self):
        self.prepare()
        with self.assertRaisesRegex(G.BuildError, "different source/toolchain inputs"):
            self.prepare({"receipt": {"bytes": 2, "sha256": "1" * 64}, "prefix_files": {}})

    def test_release_source_modes_and_timestamps_are_normalized_and_reusable(self):
        data = self.archive([("libffi-3.4.4/configure", tarfile.REGTYPE), ("libffi-3.4.4/configure.ac", tarfile.REGTYPE)])
        files, modes = G.archive_files(data, "libffi-3.4.4")
        marker = G.prepare_sources(self.root, files, modes, self.generated, self.profile, {}, self.upstream)
        paths = [self.root / name for name in files]
        self.assertEqual({int(path.stat().st_mtime) for path in paths}, {1577836800})
        self.assertEqual({path.stat().st_mode & 0o7777 for path in paths}, {0o755})
        self.assertEqual(marker, G.prepare_sources(self.root, files, modes, self.generated, self.profile, {}, self.upstream))

    def test_source_archive_links_traversal_case_collisions_and_wrong_root_fail(self):
        for members in ([('libffi-3.4.4/configure', tarfile.SYMTYPE)],
                        [('libffi-3.4.4/../configure', tarfile.REGTYPE)],
                        [('other/configure', tarfile.REGTYPE)],
                        [('libffi-3.4.4/configure', tarfile.REGTYPE), ('libffi-3.4.4/CONFIGURE', tarfile.REGTYPE)],
                        [('libffi-3.4.4/configure', tarfile.REGTYPE), ('libffi-3.4.4/A/first', tarfile.REGTYPE), ('libffi-3.4.4/a/second', tarfile.REGTYPE)]):
            with self.subTest(members=members), self.assertRaises(G.BuildError):
                G.archive_files(self.archive(members), "libffi-3.4.4")

    def test_private_alias_reuse_is_scoped_and_prefix_edits_are_preserved(self):
        self.prepare()
        target = self.write("build/intl/intl/libintl.la")
        alias = target.parent / ".libs/libintl.la"
        alias.parent.mkdir()
        alias.symlink_to("../libintl.la")
        self.prepare()
        alias.unlink()
        alias.symlink_to("../../outside")
        with self.assertRaises(G.BuildError):
            self.prepare()
        alias.unlink()
        path = self.write("prefix/include/libintl.h", b"original")
        with self.assertRaisesRegex(G.BuildError, "no owned file record"):
            G.verify_prefix(self.root)
        G.save_prefix_state(self.root)
        path.write_bytes(b"user changes")
        with self.assertRaisesRegex(G.BuildError, "changed or an install"):
            G.verify_prefix(self.root)
        self.assertEqual(path.read_bytes(), b"user changes")

    def test_pcre_bundled_chartable_alias_is_reusable_but_cannot_escape(self):
        relative = "sources/pcre2-10.42/src/pcre2_chartables.c.dist"
        self.sources[relative] = b"bundled tables"
        self.prepare()
        target = self.root / relative
        alias = self.root / "build/pcre2/src/pcre2_chartables.c"
        alias.parent.mkdir(parents=True)
        alias.symlink_to(target)
        self.prepare()
        self.assertEqual(target.read_bytes(), b"bundled tables")
        alias.unlink()
        alias.symlink_to(self.repo / "outside")
        with self.assertRaises(G.BuildError):
            self.prepare()

    def test_msys_wrapper_only_exempts_target_locale_literals(self):
        ndk = {name: Path("/c/NDK with spaces") / name for name in ("clang", "clang++", "llvm-ar", "llvm-ranlib", "llvm-strip", "llvm-nm", "llvm-objdump")}
        with patch.object(G.T, "is_msys", return_value=True):
            generated = G.generated_files(self.root, ndk, {"pkg-config": Path("/usr/bin/pkg-config")})
        wrapper = generated["toolchain/cc"].decode()
        self.assertIn("-DLOCALEDIR=;-DLOCALE_ALIAS_PATH=", wrapper)
        self.assertNotIn("MSYS_NO_PATHCONV", wrapper)
        self.assertNotIn("MSYS2_ARG_CONV_EXCL='*'", wrapper)
        self.assertIn("'/c/NDK with spaces/clang'", wrapper)
        self.assertIn("'/c/NDK with spaces/clang++' --target=x86_64-linux-android23", generated["toolchain/cxx"].decode())

    def test_configure_environment_discards_host_cache_and_preprocessor(self):
        contaminated = {"CPP": "host-cpp", "CXXCPP": "host-cpp", "CCAS": "host-as", "LIBS": "-lhost",
                        "LIBICONV": "-L/host -liconv", "PKG_CONFIG": "/host/pkg-config", "ac_cv_func_iconv": "yes"}
        with patch.dict(os.environ, contaminated):
            env = G.isolated_environment()
        self.assertFalse(set(contaminated) & set(env))
        self.assertEqual(env["CONFIG_SITE"], "/dev/null")

    def test_recorded_libffi_fix_requires_exact_source_and_preserves_trampolines(self):
        package = G.selected_inputs(REPO)[2][2]
        files = {"sources/" + package["source_dir"] + "/" + e["path"]: e["before"].encode() for e in package["source_edits"]}
        G.apply_source_edits(package, files)
        self.assertIn(b"int open_temp_exec_file(void) FFI_HIDDEN;", files["sources/libffi-3.4.4/include/ffi_common.h"])
        self.assertIn(b"#include <ffi_common.h>", files["sources/libffi-3.4.4/src/tramp.c"])
        self.assertNotIn("--disable-exec-static-tramp", package["configure_options"])
        with self.assertRaisesRegex(G.BuildError, "does not match"):
            G.apply_source_edits(package, files)

    def test_runtime_uses_config_guess_from_shared_release_build_aux(self):
        package = self.root / "sources/gettext-0.21.1"
        source = package / "gettext-runtime"
        source.mkdir(parents=True)
        guess = self.write("sources/gettext-0.21.1/build-aux/config.guess", b"#!/bin/sh\n")
        self.assertEqual(G.config_guess(source, package), guess)
        guess.unlink()
        with self.assertRaisesRegex(G.BuildError, "no config.guess"):
            G.config_guess(source, package)


if __name__ == "__main__":
    unittest.main()
