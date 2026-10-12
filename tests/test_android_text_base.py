"""Preservation, cross-toolchain and receipt guards for the text foundation."""

import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from test_android_bootstrap import ar_library, elf_program


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("android_text_base", REPO / "tools/build_android_text_base.py")
T = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(T)


class TextBaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wesnoth-text-base-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.root = self.repo / "build/android-text-base"
        self.profile = dict(T.B.SELECTED_PROFILE)
        self.sources = {"sources/libiconv-1.17/configure": b"#!/bin/sh\nexit 0\n"}
        self.generated = {"toolchain/cc": b"#!/bin/bash\nexit 0\n"}
        self.pins = {"libiconv-1.17.tar.gz": {"bytes": 1, "sha256": "a" * 64}}

    def write(self, name, content=b"fixture"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def prepare(self):
        return T.prepare_sources(self.root, self.sources, list(self.sources), self.generated, self.profile, self.pins)

    def populate(self):
        self.prepare()
        for name in T.REQUIRED_HEADERS:
            self.write("prefix/include/" + name)
        for name in T.REQUIRED_ARCHIVES:
            self.write("prefix/lib/" + name, ar_library(objects=2))
        for name in T.REQUIRED_PC:
            self.write("prefix/lib/pkgconfig/" + name, b"prefix=${pcfiledir}/../..\n")
        self.write(T.ARTIFACTS[0], elf_program(needed=("libc.so", "libm.so")))

    def make_receipt(self):
        for name in T.PROJECT_INPUTS:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((REPO / name).read_bytes())
        profile, inputs, _, pins = T.selected_inputs(self.repo)
        validation, files = T.validate_prefix(self.root)
        receipt = {"schema_version": 1, "kind": T.KIND, "profile": profile,
                   "inputs": {"project_files": inputs, "archives": pins}, "prefix_files": files,
                   "artifacts": {name: files[name] for name in T.ARTIFACTS}, "validation": validation}
        self.write("receipt.json", T.B.json_bytes(receipt))
        return receipt

    def test_real_plan_is_static_pic_offline_and_runtime_neutral(self):
        profile, inputs, packages, pins = T.selected_inputs(REPO)
        self.assertEqual(profile, self.profile)
        self.assertEqual(set(inputs), set(T.PROJECT_INPUTS))
        self.assertEqual(len(inputs), 7)
        self.assertEqual(len(pins), 2)
        self.assertIn("--with-pic", packages[0]["configure_options"])
        self.assertIn("--disable-shared", packages[0]["configure_options"])
        self.assertIn("--prefix=/usr", packages[0]["configure_options"])
        self.assertIn("--wrap-mode=nodownload", packages[1]["meson_options"])
        self.assertIn("-Dtests=disabled", packages[1]["meson_options"])
        self.assertIn("-Db_staticpic=true", packages[1]["meson_options"])

    def archive(self, names):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            for name, mode, kind in names:
                entry = tarfile.TarInfo(name)
                entry.mode = mode
                entry.type = kind
                data = b"fixture"
                entry.size = len(data) if kind == tarfile.REGTYPE else 0
                if kind == tarfile.SYMTYPE:
                    entry.linkname = "/outside"
                archive.addfile(entry, io.BytesIO(data) if entry.size else None)
        return output.getvalue()

    def test_extraction_retains_only_safe_executable_modes(self):
        data = self.archive([("libiconv-1.17/configure", 0o4755, tarfile.REGTYPE),
                             ("libiconv-1.17/COPYING", 0o644, tarfile.REGTYPE)])
        files, executable = T.archive_files(data, "libiconv-1.17")
        self.assertEqual(executable, ["sources/libiconv-1.17/configure"])
        T.prepare_sources(self.root, files, executable, self.generated, self.profile, self.pins)
        self.assertEqual((self.root / executable[0]).stat().st_mode & 0o7777, 0o755)

    def test_archive_links_traversal_duplicates_and_wrong_root_are_rejected(self):
        for members in ([('libiconv-1.17/configure', 0o755, tarfile.SYMTYPE)],
                        [('libiconv-1.17/../configure', 0o755, tarfile.REGTYPE)],
                        [('other/configure', 0o755, tarfile.REGTYPE)],
                        [('libiconv-1.17/configure', 0o755, tarfile.REGTYPE), ('libiconv-1.17/CONFIGURE', 0o755, tarfile.REGTYPE)]):
            with self.subTest(members=members), self.assertRaises(T.BuildError):
                T.archive_files(self.archive(members), "libiconv-1.17")

    def test_unowned_stage_and_modified_source_are_preserved(self):
        sentinel = self.write("user.txt", b"preserve")
        with self.assertRaisesRegex(T.BuildError, "unmanaged"):
            self.prepare()
        self.assertEqual(sentinel.read_bytes(), b"preserve")
        sentinel.unlink()
        self.root.rmdir()
        self.prepare()
        source = self.write("sources/libiconv-1.17/configure", b"user changes")
        with self.assertRaisesRegex(T.BuildError, "changed, added or removed"):
            self.prepare()
        self.assertEqual(source.read_bytes(), b"user changes")

    def test_unchanged_source_reuse_and_modified_wrapper_guard(self):
        marker = self.prepare()
        stamp = (self.root / "toolchain/cc").stat().st_mtime_ns
        self.assertEqual(self.prepare(), marker)
        self.assertEqual((self.root / "toolchain/cc").stat().st_mtime_ns, stamp)
        wrapper = self.write("toolchain/cc", b"user wrapper")
        with self.assertRaisesRegex(T.BuildError, "changed, added or removed"):
            self.prepare()
        self.assertEqual(wrapper.read_bytes(), b"user wrapper")

    def test_prefix_edits_and_interrupted_install_are_preserved(self):
        self.prepare()
        path = self.write("prefix/include/iconv.h", b"original")
        with self.assertRaisesRegex(T.BuildError, "no owned file record"):
            T.verify_prefix(self.root)
        T.save_prefix_state(self.root)
        T.verify_prefix(self.root)
        path.write_bytes(b"user edit")
        with self.assertRaisesRegex(T.BuildError, "changed or an install"):
            T.verify_prefix(self.root)
        self.assertEqual(path.read_bytes(), b"user edit")

    def test_directory_links_and_case_collisions_are_rejected(self):
        self.prepare()
        outside = self.repo / "outside"
        outside.mkdir()
        (self.root / "prefix").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(T.BuildError):
            self.prepare()
        self.assertEqual(list(outside.iterdir()), [])
        (self.root / "prefix").unlink()
        self.write("prefix/include/A/file")
        self.write("prefix/include/a/other")
        with self.assertRaisesRegex(T.BuildError, "collide on Windows"):
            T.save_prefix_state(self.root)

    def test_environment_discards_host_toolchain_and_package_settings(self):
        contaminated = {"CC": "host-gcc", "CFLAGS": "-I/host", "CONFIG_SITE": "/host/site", "MAKEFLAGS": "--eval=oops",
                        "PYTHONPATH": "/host/python", "PKG_CONFIG_LIBDIR": "/host/pkgconfig", "MESON_CROSS_FILE": "/host/cross"}
        with patch.dict(os.environ, contaminated), patch.object(T, "is_msys", return_value=True):
            env = T.isolated_environment()
        self.assertNotIn("CC", env)
        self.assertNotIn("MAKEFLAGS", env)
        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("MESON_CROSS_FILE", env)
        self.assertEqual(env["CONFIG_SITE"], "/dev/null")
        self.assertEqual(env["PKG_CONFIG_LIBDIR"], "")
        self.assertEqual(env["PATH"], "/usr/bin:/bin")

    def test_nested_build_redirect_blocks_but_known_libtool_alias_is_reusable(self):
        self.prepare()
        outside = self.repo / "outside"
        outside.mkdir()
        build = self.root / "build"
        build.mkdir()
        (build / "iconv").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(T.BuildError):
            self.prepare()
        self.assertEqual(list(outside.iterdir()), [])
        (build / "iconv").unlink()
        target = self.write("build/iconv/lib/libiconv.la")
        alias = target.parent / ".libs/libiconv.la"
        alias.parent.mkdir()
        alias.symlink_to("../libiconv.la")
        self.prepare()
        alias.unlink()
        alias.symlink_to(outside / "unrelated")
        with self.assertRaises(T.BuildError):
            self.prepare()

    def test_cross_file_and_wrappers_quote_ndk_paths_with_spaces(self):
        names = ("clang", "llvm-ar", "llvm-ranlib", "llvm-strip", "llvm-nm", "llvm-objdump")
        ndk = {name: Path("/Android SDK/ndk tools/bin") / name for name in names}
        files = T.generated_files(self.root, ndk, {"pkg-config": Path("/usr/bin/pkg-config")})
        self.assertIn(b"'/Android SDK/ndk tools/bin/clang' --target=x86_64-linux-android23", files["toolchain/cc"])
        cross = files["toolchain/android.ini"].decode()
        self.assertIn("system = 'android'", cross)
        self.assertIn("needs_exe_wrapper = true", cross)
        self.assertIn("pkg-config = ['/usr/bin/pkg-config']", cross)
        with patch.object(T, "is_msys", return_value=True):
            windows_files = T.generated_files(self.root, ndk, {"pkg-config": Path("/usr/bin/pkg-config")})
        self.assertIn(b"MSYS2_ARG_CONV_EXCL='-DLIBDIR=;-DINSTALLDIR='", windows_files["toolchain/cc"])
        self.assertNotIn(b"MSYS2_ARG_CONV_EXCL", windows_files["toolchain/ar"])

    def test_native_msys_path_is_converted_before_pathlib(self):
        with patch.object(T, "is_msys", return_value=True), patch.object(T, "run", return_value="/d/Android SDK/ndk\n") as run:
            self.assertEqual(T.input_path("D:/Android SDK/ndk"), Path("/d/Android SDK/ndk"))
            self.assertEqual(run.call_args.args[0], ["/usr/bin/cygpath", "-u", "--", "D:/Android SDK/ndk"])

    def test_relocated_pc_has_no_absolute_build_prefix(self):
        path = self.write("prefix/lib/pkgconfig/pixman-1.pc", b"prefix=/usr\nlibdir=${prefix}/lib\nLibs: -L${libdir} -lpixman-1\n")
        T.normalize_pc(path)
        self.assertIn("prefix=${pcfiledir}/../..", path.read_text())
        self.assertNotIn("prefix=/usr", path.read_text())

    @unittest.skipUnless(shutil.which("pkg-config"), "Requires real pkg-config for relocated metadata parsing")
    def test_real_pkgconfig_handles_a_relocated_prefix_with_spaces(self):
        prefix = self.root / "prefix with spaces"
        directory = prefix / "lib/pkgconfig"
        directory.mkdir(parents=True)
        T.write_library_pc(directory, "iconv", "iconv", "1.17")
        T.write_library_pc(directory, "libcharset", "charset", "1.5")
        env = dict(T.isolated_environment(), PKG_CONFIG_LIBDIR=str(directory))
        result = subprocess.run([shutil.which("pkg-config"), "--cflags", "--libs", "--static", "iconv", "libcharset"],
                                env=env, capture_output=True, text=True, timeout=10, check=True)
        flags = shlex.split(result.stdout)
        includes = [Path(flag[2:]).resolve() for flag in flags if flag.startswith("-I")]
        libraries = [Path(flag[2:]).resolve() for flag in flags if flag.startswith("-L")]
        self.assertEqual(includes, [(prefix / "include").resolve()])
        self.assertEqual(libraries, [(prefix / "lib").resolve()])
        self.assertIn("-liconv", flags)
        self.assertIn("-lcharset", flags)

    def test_expected_static_archives_headers_metadata_and_android_elf(self):
        self.populate()
        validation, _ = T.validate_prefix(self.root)
        self.assertEqual(set(validation["static_libraries"]), {"lib/" + name for name in T.REQUIRED_ARCHIVES})
        (self.root / "prefix/lib/pkgconfig/iconv.pc").unlink()
        with self.assertRaisesRegex(T.BuildError, "Installed text package metadata"):
            T.validate_prefix(self.root)

    def test_host_metadata_and_unexpected_library_are_rejected(self):
        self.populate()
        pc = self.write("prefix/lib/pkgconfig/pixman-1.pc", b"prefix=${pcfiledir}/../..\nLibs: /workspace/host/lib.a\n")
        with self.assertRaisesRegex(T.BuildError, "build-host path"):
            T.validate_prefix(self.root)
        pc.write_bytes(b"prefix=${pcfiledir}/../..\n")
        self.write("prefix/lib/libhost.a", ar_library(objects=1))
        with self.assertRaisesRegex(T.BuildError, "exactly libiconv"):
            T.validate_prefix(self.root)

    def test_wrong_android_loader_api_and_cpp_dependencies_are_rejected(self):
        self.populate()
        for blob in (elf_program(api=28, needed=("libc.so",)),
                     elf_program(interpreter="/lib64/ld-linux-x86-64.so.2", needed=("libc.so",)),
                     elf_program(needed=("libc.so", "libc++_shared.so")), elf_program(needed=("libc.so",), rpath=True)):
            with self.subTest(blob=blob[700:735]), self.assertRaises(T.BuildError):
                self.write(T.ARTIFACTS[0], blob)
                T.validate_prefix(self.root)

    def test_readonly_verification_needs_no_ndk_and_detects_input_or_prefix_edits(self):
        self.populate()
        receipt = self.make_receipt()
        before = {path: path.stat().st_mtime_ns for path in self.root.rglob("*") if path.is_file()}
        with patch.object(T, "tools_and_versions", side_effect=AssertionError("must not probe tools")):
            self.assertEqual(T.verify_receipt(self.repo), receipt)
        self.assertEqual(before, {path: path.stat().st_mtime_ns for path in before})
        source = self.repo / "tools/native-text-base/probe.c"
        original = source.read_bytes()
        source.write_bytes(original + b"\n/* local edit */\n")
        with self.assertRaisesRegex(T.BuildError, "receipt/input/artifact"):
            T.verify_receipt(self.repo)
        source.write_bytes(original)
        self.write("prefix/include/iconv.h", b"changed")
        with self.assertRaisesRegex(T.BuildError, "receipt/input/artifact"):
            T.verify_receipt(self.repo)


if __name__ == "__main__":
    unittest.main()
