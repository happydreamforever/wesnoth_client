"""Offline rendering receipts, prerequisite binding and source/prefix protection."""
import configparser
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import struct
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from test_android_bootstrap import ar_library, elf_program

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("android_text_render", REPO / "tools/build_android_text_render.py")
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)


def create_fixture(repo, font_assets=None):
    def write(root, name, content=b"fixture"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path
    for name in R.PROJECT_INPUTS:
        write(repo, name, (REPO / name).read_bytes())
    if font_assets is not None:
        write(repo, "tools/native-fonts/assets.json", R.B.json_bytes(font_assets))
    text = repo / "build/android-text-base"
    for name in R.T.REQUIRED_HEADERS:
        write(text, "prefix/include/" + name)
    for name in R.T.REQUIRED_ARCHIVES:
        write(text, "prefix/lib/" + name, ar_library(objects=2))
    for name in R.T.REQUIRED_PC:
        write(text, "prefix/lib/pkgconfig/" + name, b"prefix=${pcfiledir}/../..\n")
    write(text, R.T.ARTIFACTS[0], elf_program(needed=("libc.so",)))
    profile, inputs, _, pins = R.T.selected_inputs(repo)
    validation, files = R.T.validate_prefix(text)
    write(text, "receipt.json", R.B.json_bytes({"schema_version": 1, "kind": R.T.KIND, "profile": profile,
        "inputs": {"project_files": inputs, "archives": pins}, "validation": validation,
        "prefix_files": files, "artifacts": {name: files[name] for name in R.T.ARTIFACTS}}))
    fonts = repo / "build/android-fonts"
    for name in R.F.REQUIRED_HEADERS:
        write(fonts, "prefix/include/" + name)
    for name in R.F.REQUIRED_ARCHIVES:
        write(fonts, "prefix/lib/" + name, ar_library(objects=2))
    write(fonts, "prefix/lib/pkgconfig/freetype2.pc", b"prefix=${pcfiledir}/../..\nURL: https://freetype.org\n")
    for name in R.F.ARTIFACTS:
        write(fonts, name, elf_program(needed=("libc++_shared.so", "libc.so", "libm.so")))
    profile, inputs, _, pins = R.F.selected_inputs(repo)
    # The original validator's NDK argument is only used for text-path checks.
    # This is fixture construction; production verify_font_receipt has no NDK.
    validation, files = R.F.validate_prefix(fonts, {"cmake_toolchain": repo / "unused-ndk/build/cmake/android.toolchain.cmake"})
    write(fonts, "receipt.json", R.B.json_bytes({"schema_version": 1, "kind": R.F.KIND, "profile": profile,
        "inputs": {"project_files": inputs, "archives": pins}, "validation": validation,
        "prefix_files": files, "artifacts": {name: files[name] for name in R.F.ARTIFACTS}}))
    root = repo / "build/android-text-render"
    for name in R.REQUIRED_HEADERS:
        write(root, "prefix/include/" + name)
    for name in R.REQUIRED_ARCHIVES:
        write(root, "prefix/lib/" + name, ar_library(objects=2))
    for name in R.REQUIRED_PC:
        write(root, "prefix/lib/pkgconfig/" + name, b"prefix=${pcfiledir}/../..\n")
    write(root, R.ARTIFACTS[0], elf_program(needed=("libc.so", "libm.so", "libdl.so")))
    profile, inputs, _, pins = R.selected_inputs(repo)
    validation, files = R.validate_prefix(root)
    receipt = {"schema_version": 1, "kind": R.KIND, "profile": profile,
        "inputs": {"project_files": inputs, "archives": pins, "prerequisites": R.prior_inputs(repo)},
        "validation": validation, "prefix_files": files, "artifacts": {name: files[name] for name in R.ARTIFACTS}}
    write(root, "receipt.json", R.B.json_bytes(receipt))
    return root, receipt


class RenderingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wesnoth render ")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.root = self.repo / "build/android-text-render"
        self.sources = {"sources/expat-2.5.0/configure": b"#!/bin/bash\n"}
        self.generated = {"toolchain/cc": b"#!/bin/bash\nexit 0\n"}

    def write(self, name, content=b"fixture"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def prepare(self):
        return R.prepare_sources(self.root, self.sources, list(self.sources), self.generated, dict(R.B.SELECTED_PROFILE), {}, {})

    def archive(self, entries):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as tar:
            for name, kind in entries:
                item = tarfile.TarInfo(name)
                item.type = kind
                item.linkname = "../outside" if kind == tarfile.SYMTYPE else ""
                item.size = 1 if kind == tarfile.REGTYPE else 0
                item.mode = 0o755
                tar.addfile(item, io.BytesIO(b"x") if item.size else None)
        return stream.getvalue()

    def test_plan_binds_only_two_prior_stages_and_three_static_libraries(self):
        _, inputs, packages, pins = R.selected_inputs(REPO)
        self.assertEqual(len(inputs), 15)
        self.assertIn("tools/native-fonts/assets.json", inputs)
        self.assertNotIn("tools/build_android_glib.py", inputs)
        self.assertEqual(set(pins), {package[1] for package in R.PACKAGES})
        self.assertIn("-Ddefault-fonts-dirs=[]", packages[1]["meson_options"])
        self.assertIn("-Dadditional-fonts-dirs=[]", packages[1]["meson_options"])
        self.assertIn("-Dcache-build=disabled", packages[1]["meson_options"])
        self.assertIn("--disable-pdf", packages[2]["configure_options"])
        self.assertIn("--disable-png", packages[2]["configure_options"])

    def test_fontconfig_fix_preserves_external_iconv_header_mapping(self):
        _, _, packages, _ = R.selected_inputs(REPO)
        package = packages[1]
        edit, = package["source_edits"]
        original = ("if iconv.allowed()\n  if cc.has_header_symbol('iconv.h', 'libiconv_open')\n"
                    + edit["before"] + "    found_iconv = 1\n  endif\nendif\nconf.set('USE_ICONV', found_iconv)\n")
        files = {"sources/fontconfig-2.17.1/meson.build": original.encode()}
        R.apply_source_edits(package, files)
        corrected = files["sources/fontconfig-2.17.1/meson.build"].decode()
        self.assertNotIn("conf.set('LIBICONV_PLUG'", corrected)
        self.assertIn("found_iconv = 1", corrected)
        self.assertIn("conf.set('USE_ICONV', found_iconv)", corrected)
        self.assertEqual(corrected, original.replace(edit["before"], edit["after"], 1))

    def test_source_fix_refuses_missing_repeated_and_unrecorded_anchors(self):
        _, _, packages, _ = R.selected_inputs(REPO)
        package = packages[1]
        before = package["source_edits"][0]["before"].encode()
        for data in (b"changed upstream input", before + before):
            files = {"sources/fontconfig-2.17.1/meson.build": data}
            with self.subTest(data=data), self.assertRaisesRegex(R.BuildError, "recorded source fix"):
                R.apply_source_edits(package, files)
            self.assertEqual(files["sources/fontconfig-2.17.1/meson.build"], data)
        package = json.loads(json.dumps(package))
        package["source_edits"][0]["path"] = "../outside"
        with self.assertRaises(R.BuildError):
            R.apply_source_edits(package, {})

    def test_verify_only_requires_no_ndk_cache_or_external_tools(self):
        _, expected = create_fixture(self.repo)
        before = {stage: R.M.inventory(self.repo / "build" / stage) for stage in ("android-text-base", "android-fonts", "android-text-render")}
        with patch.object(R.T, "run", side_effect=AssertionError("external tool")), \
             patch.object(R.M, "archive_data", side_effect=AssertionError("source cache")), \
             patch.object(R.T, "tools_and_versions", side_effect=AssertionError("NDK")), \
             patch.object(R.F, "validate_prefix", side_effect=AssertionError("old NDK verifier")):
            self.assertEqual(R.verify_receipt(self.repo), expected)
        self.assertEqual(before, {stage: R.M.inventory(self.repo / "build" / stage) for stage in before})

    def test_each_prerequisite_header_change_is_rejected_and_preserved(self):
        create_fixture(self.repo)
        for name in ("android-fonts/prefix/include/freetype2/ft2build.h", "android-text-base/prefix/include/iconv.h"):
            path = self.repo / "build" / name
            original = path.read_bytes()
            path.write_bytes(b"user edit")
            with self.subTest(name=name), self.assertRaises(R.BuildError):
                R.verify_receipt(self.repo)
            self.assertEqual(path.read_bytes(), b"user edit")
            path.write_bytes(original)

    def test_new_header_probe_source_and_asset_manifest_changes_are_rejected(self):
        create_fixture(self.repo)
        for path in (self.root / "prefix/include/expat.h", self.repo / "tools/native-text-render/probe.c", self.repo / "tools/native-fonts/assets.json"):
            original = path.read_bytes()
            path.write_bytes(original + b" ")
            with self.subTest(path=path), self.assertRaises(R.BuildError):
                R.verify_receipt(self.repo)
            path.write_bytes(original)

    def test_valid_but_changed_prior_receipt_invalidates_binding(self):
        create_fixture(self.repo)
        path = self.repo / "build/android-fonts/receipt.json"
        receipt = R.B.read_json(path)
        receipt["tools"] = {"new": "tool metadata"}
        path.write_bytes(R.B.json_bytes(receipt))
        R.verify_font_receipt(self.repo)
        with self.assertRaisesRegex(R.BuildError, "receipt/input/artifact hashes"):
            R.verify_receipt(self.repo)

    def test_font_contract_errors_are_reported_as_build_errors(self):
        create_fixture(self.repo)
        path = self.repo / "build/android-fonts" / R.F.ARTIFACTS[0]
        path.write_bytes(elf_program(needed=("libc.so",)))
        with self.assertRaises(R.BuildError):
            R.verify_font_receipt(self.repo)

    def test_probe_rejects_wrong_abi_and_unneeded_shared_dependencies(self):
        create_fixture(self.repo)
        wrong = bytearray(elf_program());struct.pack_into("<H", wrong, 18, 183)
        for content in (bytes(wrong), elf_program(needed=("libc.so", "libc++_shared.so")), elf_program(needed=("libc.so", "libz.so"))):
            self.write(R.ARTIFACTS[0], content)
            with self.assertRaises(R.BuildError):
                R.validate_prefix(self.root)

    def test_archives_reject_links_traversal_and_windows_collisions(self):
        base = [("expat-2.5.0/configure", tarfile.REGTYPE)]
        for extra in (("expat-2.5.0/../outside", tarfile.REGTYPE), ("expat-2.5.0/link", tarfile.SYMTYPE),
                      ("expat-2.5.0/CONFIGURE", tarfile.REGTYPE)):
            with self.subTest(extra=extra), self.assertRaises(R.BuildError):
                R.archive_files(self.archive(base + [extra]), "expat-2.5.0")
        files, executables = R.archive_files(self.archive(base), "expat-2.5.0")
        self.assertEqual(files, {"sources/expat-2.5.0/configure": b"x"})
        self.assertEqual(executables, list(files))

    def test_cairo_sized_archive_fits_full_source_guard(self):
        entries = [("cairo-1.16.0/configure", tarfile.REGTYPE)]
        entries += [("cairo-1.16.0/test/" + str(i), tarfile.REGTYPE) for i in range(4685)]
        files, _ = R.archive_files(self.archive(entries), "cairo-1.16.0")
        self.assertEqual(len(files), 4686)

    def test_unmanaged_stage_and_modified_sources_are_preserved(self):
        sentinel = self.write("user.txt", b"keep")
        with self.assertRaisesRegex(R.BuildError, "unmanaged"):
            self.prepare()
        self.assertEqual(sentinel.read_bytes(), b"keep")
        sentinel.unlink();self.root.rmdir()
        self.prepare()
        path = self.write("sources/expat-2.5.0/configure", b"edit")
        with self.assertRaisesRegex(R.BuildError, "changed, added or removed"):
            self.prepare()
        self.assertEqual(path.read_bytes(), b"edit")

    def test_stage_reuse_accepts_only_three_exact_libtool_aliases(self):
        self.prepare()
        for name, target in (("expat/lib/.libs/libexpat.la", "../libexpat.la"),
                             ("expat/lib/.libs/libexpatinternal.la", "../libexpatinternal.la"),
                             ("cairo/src/.libs/libcairo.la", "../libcairo.la")):
            path = self.root / "build" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            (path.parent / target).write_bytes(b"owned Libtool metadata")
            path.symlink_to(target)
        self.prepare()
        link = self.root / "build/expat/lib/.libs/libexpat.la"
        link.unlink();link.symlink_to("../../../../outside")
        with self.assertRaises(R.BuildError):
            self.prepare()

    def test_changed_prefix_and_build_redirection_are_preserved(self):
        self.prepare()
        path = self.write("prefix/include/expat.h", b"original")
        R.save_prefix_state(self.root)
        path.write_bytes(b"edit")
        with self.assertRaises(R.BuildError):
            R.verify_prefix(self.root)
        self.assertEqual(path.read_bytes(), b"edit")
        outside = self.repo / "outside";outside.mkdir()
        (self.root / "build").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(R.BuildError):
            self.prepare()
        self.assertEqual(list(outside.iterdir()), [])

    def test_target_crossfile_isolates_all_three_prefixes(self):
        ndk = {name: Path("/c/SDK with spaces") / name for name in ("clang", "clang++", "llvm-ar", "llvm-ranlib", "llvm-strip", "llvm-nm", "llvm-objdump")}
        with patch.object(R.T, "is_msys", return_value=True):
            files = R.generated_files(self.root, self.repo, ndk, {"pkg-config": Path("/usr/bin/pkg-config")})
        cross = configparser.ConfigParser();cross.read_string(files["toolchain/android.ini"].decode())
        self.assertEqual(cross["host_machine"]["system"], "'android'")
        self.assertIn("pkg_config_libdir", cross["properties"])
        self.assertNotIn("pkg_config_libdir", cross["built-in options"])
        for prefix in (self.root / "prefix", self.repo / "build/android-fonts/prefix", self.repo / "build/android-text-base/prefix"):
            self.assertIn(str(prefix / "lib/pkgconfig"), cross["properties"]["pkg_config_libdir"])
        self.assertIn("-DLIBDIR=;-DINSTALLDIR=", files["toolchain/cc"].decode())
        self.assertIn("--target=x86_64-linux-android23", files["toolchain/cxx"].decode())
        self.assertIn("'", files["toolchain/cc"].decode())

    def test_config_guess_finds_each_release_auxiliary_directory(self):
        for name, directory in (("expat-2.5.0", "conftools"), ("cairo-1.16.0", "build")):
            source = self.root / "sources" / name
            path = self.write("sources/" + name + "/" + directory + "/config.guess")
            self.assertEqual(R.config_guess(source, source), path)

    def test_isolated_environment_removes_stale_cross_results(self):
        with patch.dict(os.environ, {"cairo_cv_warn_maybe": "yes", "ac_cv_func_bad": "yes", "LIBS": "-lhost", "PKG_CONFIG": "/host/pkg-config"}):
            env = R.isolated_environment()
        for name in ("cairo_cv_warn_maybe", "ac_cv_func_bad", "LIBS", "PKG_CONFIG"):
            self.assertNotIn(name, env)

    def configure_fixture(self, mode):
        self.prepare()
        self.write("prefix/include/local.h", b"preserve installed header")
        private = self.root / "build/cairo"
        private.mkdir(parents=True)
        script = self.repo / "fake-configure.py"
        script.write_text(r'''import json, os, pathlib, sys
private = pathlib.Path.cwd()
counter = private / "launches.json"
launches = json.loads(counter.read_text()) if counter.exists() else []
launches.append({"tmp": os.environ["TMPDIR"], "eof": sys.stdin.buffer.read() == b"",
                 "sentinel": os.environ.get("WORKFLOW_SENTINEL"), "TMP": os.environ.get("TMP"), "TEMP": os.environ.get("TEMP")})
counter.write_text(json.dumps(launches))
number = len(launches)
mode = sys.argv[1]
pathlib.Path(os.environ["TMPDIR"], "retained.tmp").write_text("temporary diagnostic " + str(number))
if mode != "linked-snapshot":
    (private / "config.log").write_text("config log " + str(number))
    (private / "config.status").write_bytes(b"" if number == 1 else b"partial status")
print("stdout step " + str(number), flush=True)
print("stderr step " + str(number), file=sys.stderr, flush=True)
if mode == "success" or mode == "transient" and number == 2:
    raise SystemExit(0)
if mode in ("always", "transient", "busy-only", "linked-snapshot"):
    print("/source/cairo/configure: line 37058: cannot create temp file for here-document: Device or resource busy", file=sys.stderr)
if mode in ("always", "transient", "write-only", "different-busy", "linked-snapshot"):
    print("configure: error: write failure creating ./config.status", file=sys.stderr)
if mode == "different-busy":
    print("/source/cairo/configure: line 37058: cannot create temp file for here-document: Permission denied", file=sys.stderr)
if mode == "compiler":
    print("configure: error: C compiler cannot create executables", file=sys.stderr)
raise SystemExit(1)
''', encoding="utf-8")
        env = dict(os.environ, TMPDIR="unchanged inherited TMPDIR", TMP="unchanged TMP", TEMP="unchanged TEMP", WORKFLOW_SENTINEL="keep")
        return [sys.executable, "-B", script, mode], env, private

    def test_cairo_busy_retry_keeps_logs_uses_distinct_temp_and_preserves_inputs(self):
        command, env, private = self.configure_fixture("transient")
        original_env = dict(env)
        source_before = R.M.inventory(self.root, "sources")
        prefix_before = R.M.inventory(self.root)
        with patch.object(R.time, "sleep") as sleep, contextlib.redirect_stdout(io.StringIO()) as console:
            R.run_cairo_configure(command, env, self.root)
        sleep.assert_called_once_with(1)
        self.assertEqual(env, original_env)
        self.assertEqual(R.M.inventory(self.root, "sources"), source_before)
        self.assertEqual(R.M.inventory(self.root), prefix_before)
        launches = json.loads((private / "launches.json").read_text())
        self.assertEqual(len(launches), 2)
        self.assertEqual(len({item["tmp"] for item in launches}), 2)
        for number, item in enumerate(launches, 1):
            self.assertTrue(item["eof"])
            self.assertEqual(item["sentinel"], "keep")
            self.assertEqual((item["TMP"], item["TEMP"]), ("unchanged TMP", "unchanged TEMP"))
            temp = Path(item["tmp"])
            self.assertTrue(temp.is_relative_to(self.root / "build/cairo-configure-attempts"))
            self.assertEqual((temp / "retained.tmp").read_text(), "temporary diagnostic " + str(number))
            log = (temp.parent / "configure.log").read_text()
            self.assertIn("stdout step " + str(number), log)
            self.assertIn("stderr step " + str(number), log)
            self.assertIn("stdout step " + str(number), console.getvalue())
        failed = Path(launches[0]["tmp"]).parent
        self.assertEqual((failed / "config.log").read_text(), "config log 1")
        self.assertEqual((failed / "config.status").read_bytes(), b"")
        self.prepare()  # New diagnostics do not change the source/toolchain marker.

    def test_cairo_repeated_busy_failure_stops_after_three_attempts(self):
        command, env, private = self.configure_fixture("always")
        with patch.object(R.time, "sleep") as sleep, contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(R.BuildError, "Cairo configure failed"):
            R.run_cairo_configure(command, env, self.root)
        self.assertEqual([call.args for call in sleep.call_args_list], [(1,), (2,)])
        launches = json.loads((private / "launches.json").read_text())
        self.assertEqual(len(launches), 3)
        self.assertEqual(len({item["tmp"] for item in launches}), 3)
        for item in launches:
            self.assertTrue((Path(item["tmp"]).parent / "config.log").is_file())
        self.assertFalse((self.root / "receipt.json").exists())

    def test_cairo_other_failures_or_incomplete_signature_are_not_retried(self):
        command, env, private = self.configure_fixture("compiler")
        for mode in ("compiler", "busy-only", "write-only", "different-busy"):
            counter = private / "launches.json"
            if counter.exists():
                counter.unlink()
            with self.subTest(mode=mode), patch.object(R.time, "sleep") as sleep, contextlib.redirect_stdout(io.StringIO()), self.assertRaises(R.BuildError):
                R.run_cairo_configure([*command[:-1], mode], env, self.root)
            sleep.assert_not_called()
            self.assertEqual(len(json.loads(counter.read_text())), 1)

    def test_cairo_success_does_not_retry_and_existing_diagnostics_are_preserved(self):
        command, env, private = self.configure_fixture("success")
        old = self.write("build/cairo-configure-attempts/old-attempt/configure.log", b"old evidence")
        with patch.object(R.time, "sleep") as sleep, contextlib.redirect_stdout(io.StringIO()):
            R.run_cairo_configure(command, env, self.root)
        sleep.assert_not_called()
        self.assertEqual(len(json.loads((private / "launches.json").read_text())), 1)
        self.assertEqual(old.read_bytes(), b"old evidence")

    def test_cairo_attempt_directory_and_snapshot_links_cannot_escape_stage(self):
        command, env, private = self.configure_fixture("linked-snapshot")
        outside = self.repo / "outside"
        outside.mkdir()
        attempts = self.root / "build/cairo-configure-attempts"
        attempts.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(R.BuildError):
            R.run_cairo_configure(command, env, self.root)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((private / "launches.json").exists())
        attempts.unlink()
        target = outside / "user.txt"
        target.write_bytes(b"keep user data")
        (private / "config.log").symlink_to(target)
        with patch.object(R.time, "sleep") as sleep, contextlib.redirect_stdout(io.StringIO()), self.assertRaises(R.BuildError):
            R.run_cairo_configure(command, env, self.root)
        sleep.assert_not_called()
        self.assertEqual(target.read_bytes(), b"keep user data")
        self.assertEqual(len(json.loads((private / "launches.json").read_text())), 1)

    @unittest.skipUnless(shutil.which("bash"), "Bash is required for large here-document execution")
    def test_real_bash_large_quoted_and_expanded_heredocs_use_private_temp(self):
        self.prepare()
        private = self.root / "build/cairo"
        private.mkdir(parents=True)
        literal = ("quoted " + "x" * 96 + "\n") * 1500
        raw = ("expanded ${WESNOTH_HEREDOC_VALUE}\n") * 3000
        expanded = raw.replace("${WESNOTH_HEREDOC_VALUE}", "native temp input")
        script = self.repo / "large-heredocs.sh"
        script.write_text("set -eu\ncat >quoted.txt <<'END_QUOTED'\n" + literal + "END_QUOTED\n"
                          "cat >expanded.txt <<END_EXPANDED\n" + raw + "END_EXPANDED\n"
                          "printf '%s' \"$TMPDIR\" > selected-temp.txt\n"
                          "printf '%s\\n' 'BASH LARGE HEREDOC PASS'\n", encoding="utf-8")
        env = dict(os.environ, TMPDIR="unusable inherited temp path", WESNOTH_HEREDOC_VALUE="native temp input")
        with patch.object(R.time, "sleep") as sleep, contextlib.redirect_stdout(io.StringIO()) as console:
            R.run_cairo_configure([shutil.which("bash"), "--noprofile", "--norc", script], env, self.root)
        sleep.assert_not_called()
        self.assertEqual((private / "quoted.txt").read_text(), literal)
        self.assertEqual((private / "expanded.txt").read_text(), expanded)
        temp = Path((private / "selected-temp.txt").read_text())
        self.assertTrue(temp.is_relative_to(self.root / "build/cairo-configure-attempts"))
        self.assertIn("BASH LARGE HEREDOC PASS", console.getvalue())
        self.assertIn("BASH LARGE HEREDOC PASS", (temp.parent / "configure.log").read_text())

    @unittest.skipUnless(shutil.which("pkg-config"), "pkg-config is required for a real relocation fixture")
    def test_pkgconfig_resolves_transitive_iconv_from_spaced_prefix(self):
        new = self.repo / "render prefix/lib/pkgconfig";old = self.repo / "old prefix/lib/pkgconfig"
        new.mkdir(parents=True);old.mkdir(parents=True)
        R.write_pc(new, "fontconfig", "fontconfig", "2.17.1", "URL: https://example.invalid\n")
        R.write_pc(old, "iconv", "iconv", "1.17")
        R.normalize_pc(new / "fontconfig.pc")
        result = subprocess.run([shutil.which("pkg-config"), "--cflags", "--libs", "--static", "fontconfig"],
            env=dict(os.environ, PKG_CONFIG_PATH="", PKG_CONFIG_LIBDIR=os.pathsep.join((str(new), str(old)))),
            text=True, capture_output=True, check=True)
        flags = shlex.split(result.stdout)
        self.assertTrue(any(x.startswith("-L") and Path(x[2:]).resolve() == old.parent for x in flags))
        self.assertTrue(any(x.startswith("-I") and Path(x[2:]).resolve() == old.parent.parent / "include" for x in flags))
        self.assertIn("-liconv", flags)


if __name__ == "__main__":
    unittest.main()
