"""Layout source omissions, immutable prerequisites, host generation and receipts."""
import configparser
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from test_android_bootstrap import ar_library, elf_program
from test_android_text_render import create_fixture as create_render
from test_android_glib import create_fixture as create_glib

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("android_layout", REPO / "tools/build_android_text_layout.py")
L = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(L)


def create_fixture(repo):
    create_glib(repo)
    create_render(repo)
    def write(root, name, data=b"fixture"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path
    for name in L.PROJECT_INPUTS:
        write(repo, name, (REPO / name).read_bytes())
    root = repo / "build/android-text-layout"
    for name in L.REQUIRED_HEADERS:
        write(root, "prefix/include/" + name)
    write(root, "prefix/include/fribidi/fribidi-unicode-version.h", b'#define FRIBIDI_UNICODE_VERSION "18.0.0"\n')
    for name in L.REQUIRED_ARCHIVES:
        write(root, "prefix/lib/" + name, ar_library(objects=2))
    for name in L.REQUIRED_PC:
        write(root, "prefix/lib/pkgconfig/" + name, b"prefix=${pcfiledir}/../..\n")
    write(root, L.ARTIFACTS[0], elf_program(needed=("libc.so", "libc++_shared.so", "libm.so")))
    write(root, L.ARTIFACTS[1], (repo / "build/android-fonts" / L.ARTIFACTS[1]).read_bytes())
    profile, inputs, _, pins = L.selected_inputs(repo)
    validation, files = L.validate_prefix(root)
    receipt = {"schema_version": 1, "kind": L.KIND, "profile": profile,
               "inputs": {"project_files": inputs, "archives": pins, "prerequisites": L.prior_inputs(repo)},
               "validation": validation, "prefix_files": files, "artifacts": {name: files[name] for name in L.ARTIFACTS}}
    write(root, "receipt.json", L.B.json_bytes(receipt))
    return root, receipt


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wesnoth layout ")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.root = self.repo / "build/android-text-layout"

    def write(self, name, data=b"fixture"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def source_fixture(self, package_name="fribidi", extra=None, missing=None, changed=None):
        package = json.loads(json.dumps(next(x for x in L.selected_inputs(REPO)[2]["packages"] if x["name"] == package_name)))
        data = {"meson.build": b"project('fixture')", "COPYING": b"license", "lib/arabic-misc.tab.i": b"hand authored"}
        for name in package["omitted_generated_files"]:
            data[name] = name.encode()
            package["omitted_generated_files"][name] = {"bytes": len(data[name]), "sha256": L.B.sha256(data[name])}
        if missing:
            del data[missing]
        if changed:
            data[changed] = b"unexpected source changes"
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:xz") as archive:
            for name, content in data.items():
                entry = tarfile.TarInfo(package["source_dir"] + "/" + name)
                entry.size, entry.mode = len(content), 0o644
                archive.addfile(entry, io.BytesIO(content))
            if extra:
                name, kind = extra
                entry = tarfile.TarInfo(package["source_dir"] + "/" + name)
                entry.type = kind
                entry.linkname = "../outside"
                entry.size = 1 if kind == tarfile.REGTYPE else 0
                archive.addfile(entry, io.BytesIO(b"x") if entry.size else None)
        return output.getvalue(), package

    def prepare(self):
        return L.prepare_sources(self.root, {"sources/owned/source.c": b"source"}, [],
                                 {"toolchain/cc": b"compiler"}, dict(L.B.SELECTED_PROFILE), {}, {})

    def test_plan_pins_three_archives_four_static_libraries_and_previous_helpers(self):
        _, inputs, plan, pins = L.selected_inputs(REPO)
        self.assertEqual(set(pins), {"fribidi-1.0.17.tar.xz", "pango-1.50.14.tar.xz", "glib-2.82.1.tar.xz"})
        self.assertEqual(sum(len(x["targets"]) for x in plan["packages"]), 4)
        self.assertIn("tools/build_android_glib.py", inputs)
        self.assertIn("tools/build_android_text_render.py", inputs)
        for package in plan["packages"]:
            self.assertIn("--wrap-mode=nodownload", package["meson_options"])

    def test_exact_seven_stale_fribidi_files_omitted_hand_authored_table_retained(self):
        data, package = self.source_fixture()
        files, _ = L.archive_files(data, package)
        self.assertEqual(len(files), 3)
        self.assertIn("sources/fribidi-1.0.17/lib/arabic-misc.tab.i", files)
        self.assertFalse(any(name.endswith("fribidi-unicode-version.h") for name in files))

    def test_omissions_require_exact_hash_and_presence(self):
        for name in L.OMITTED["fribidi"]:
            for variation in ("missing", "changed"):
                data, package = self.source_fixture(**{variation: name})
                with self.subTest(name=name, variation=variation), self.assertRaises(L.BuildError):
                    L.archive_files(data, package)

    def test_only_two_exact_unused_docs_may_collide(self):
        data, package = self.source_fixture("pango")
        files, _ = L.archive_files(data, package)
        self.assertFalse(any("func." in name for name in files))
        data, package = self.source_fixture("pango", extra=("MESON.build", tarfile.REGTYPE))
        with self.assertRaisesRegex(L.BuildError, "collide"):
            L.archive_files(data, package)

    def test_traversal_links_duplicate_and_special_archive_members_rejected(self):
        for name, kind in (("../escape", tarfile.REGTYPE), ("link", tarfile.SYMTYPE), ("hard", tarfile.LNKTYPE),
                           ("fifo", tarfile.FIFOTYPE), ("meson.build", tarfile.REGTYPE), ("subprojects/.wraplock", tarfile.REGTYPE)):
            data, package = self.source_fixture(extra=(name, kind))
            with self.subTest(name=name), self.assertRaises(L.BuildError):
                L.archive_files(data, package)

    def test_sources_reusable_but_edits_and_new_inputs_preserved_rejected(self):
        marker = self.prepare()
        self.assertEqual(self.prepare(), marker)
        path = self.root / "sources/owned/source.c"
        path.write_bytes(b"user edit")
        with self.assertRaises(L.BuildError):
            self.prepare()
        self.assertEqual(path.read_bytes(), b"user edit")

    def test_unmanaged_directory_not_adopted(self):
        self.write("keep.txt", b"keep")
        with self.assertRaisesRegex(L.BuildError, "unmanaged"):
            self.prepare()
        self.assertEqual((self.root / "keep.txt").read_bytes(), b"keep")

    def test_symlinked_stage_or_prefix_rejected(self):
        outside = self.repo / "outside"
        outside.mkdir()
        self.root.parent.mkdir()
        self.root.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(L.BuildError):
            self.prepare()
        self.root.unlink()
        self.prepare()
        (self.root / "prefix").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(L.BuildError):
            self.prepare()

    def test_prefix_ownership_preserves_changed_file(self):
        path = self.write("prefix/include/owned.h", b"original")
        with self.assertRaises(L.BuildError):
            L.verify_prefix(self.root)
        L.save_prefix_state(self.root)
        path.write_bytes(b"edited")
        with self.assertRaises(L.BuildError):
            L.verify_prefix(self.root)
        self.assertEqual(path.read_bytes(), b"edited")

    def test_host_generator_has_native_python_and_cc_not_target_tools(self):
        tools = {name: Path("/host") / name for name in ("pkg-config", "host-cc", "host-cxx")}
        ndk = {name: Path("/ndk path") / name for name in ("clang", "clang++", "llvm-ar", "llvm-ranlib", "llvm-strip", "llvm-nm", "llvm-objdump")}
        template = b"#!@PYTHON@\nVERSION='@VERSION@'\n"
        with patch.object(L.T, "is_msys", return_value=True):
            files = L.generated_files(self.root, self.repo, ndk, tools, template)
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(files["toolchain/native.ini"].decode())
        self.assertIn("/host/host-cc", config["binaries"]["c"])
        self.assertIn("glib-mkenums.py", config["binaries"]["glib-mkenums"])
        self.assertNotIn("/ndk", config["binaries"]["glib-mkenums"])
        self.assertIn(b"2.82.1", files["toolchain/glib-mkenums.py"])
        self.assertIn(b"-DSYSCONFDIR=", files["toolchain/cc"])
        self.assertIn(b"-DSYSCONFDIR=", files["toolchain/cxx"])
        self.assertIn(b"needs_exe_wrapper = true", files["toolchain/android.ini"])
        # Meson 1.12 checks the HOST binary map even for its native enum tool.
        self.assertIn(b"glib-mkenums =", files["toolchain/android.ini"])
        self.assertIn(b"toolchain/glib-mkenums.py", files["toolchain/android.ini"])

    def test_verify_receipt_no_ndk_cache_or_processes(self):
        _, receipt = create_fixture(self.repo)
        before = {stage: L.M.inventory(self.repo / "build" / stage) for stage in L.STAGES}
        with patch.object(L.T, "run", side_effect=AssertionError("process")), \
             patch.object(L.M, "archive_data", side_effect=AssertionError("cache")), \
             patch.object(L.T, "tools_and_versions", side_effect=AssertionError("NDK")):
            self.assertEqual(L.verify_receipt(self.repo), receipt)
        self.assertEqual(before, {stage: L.M.inventory(self.repo / "build" / stage) for stage in L.STAGES})

    def test_prerequisite_changes_in_each_stage_rejected_preserved(self):
        create_fixture(self.repo)
        for stage in L.STAGES:
            path = next((self.repo / "build" / stage / "prefix/include").rglob("*.h"))
            original = path.read_bytes()
            path.write_bytes(b"user changed")
            with self.subTest(stage=stage), self.assertRaises(L.BuildError):
                L.verify_receipt(self.repo)
            self.assertEqual(path.read_bytes(), b"user changed")
            path.write_bytes(original)

    def test_wrong_unicode_empty_file_and_unexpected_shared_file_rejected(self):
        root, _ = create_fixture(self.repo)
        unicode = root / "prefix/include/fribidi/fribidi-unicode-version.h"
        original = unicode.read_bytes()
        unicode.write_bytes(original.replace(b"18.0.0", b"14.0.0"))
        with self.assertRaisesRegex(L.BuildError, "Unicode"):
            L.validate_prefix(root)
        unicode.write_bytes(original)
        for name, data in (("prefix/lib/extra.so", b"unexpected"), ("prefix/include/empty", b"")):
            path = self.write(name, data)
            with self.assertRaises(L.BuildError):
                L.validate_prefix(root)
            path.unlink()

    def test_runtime_copy_must_match_verified_font_runtime(self):
        root, _ = create_fixture(self.repo)
        path = root / L.ARTIFACTS[1]
        path.write_bytes(path.read_bytes() + b"tamper")
        with self.assertRaisesRegex(L.BuildError, "runtime differs"):
            L.verify_receipt(self.repo)

    def test_layout_probe_wrong_loader_api_or_dynamic_dependency_rejected(self):
        root, _ = create_fixture(self.repo)
        for kwargs in ({"needed": ("libc.so",)}, {"needed": ("libc.so", "libc++_shared.so", "libwrong.so")},
                       {"needed": ("libc.so", "libc++_shared.so"), "api": 28}):
            with self.subTest(kwargs=kwargs):
                (root / L.ARTIFACTS[0]).write_bytes(elf_program(**kwargs))
                with self.assertRaises(L.BuildError):
                    L.validate_prefix(root)

    def test_receipt_input_binding_rejects_new_builder_bytes(self):
        create_fixture(self.repo)
        path = self.repo / "tools/build_android_text_layout.py"
        path.write_bytes(path.read_bytes() + b"\n# modified\n")
        with self.assertRaisesRegex(L.BuildError, "receipt/input/artifact"):
            L.verify_receipt(self.repo)

    def test_plan_cannot_override_offline_options_or_remove_unicode_notice(self):
        create_fixture(self.repo)
        path = self.repo / "tools/native-text-layout/build-plan.json"
        plan = json.loads(path.read_text())
        for change in ("options", "notice"):
            modified = json.loads(json.dumps(plan))
            if change == "options":
                modified["packages"][0]["meson_options"].append("--wrap-mode=forcefallback")
            else:
                modified["packages"][0]["license_files"] = ["COPYING"]
            path.write_text(json.dumps(modified))
            with self.subTest(change=change), self.assertRaises(L.BuildError):
                L.selected_inputs(self.repo)

    def test_install_manifest_escaping_owned_tree_rejected_without_copy(self):
        package = L.selected_inputs(REPO)[2]["packages"][0]
        source = self.repo / "outside.h"
        source.write_bytes(b"private outside bytes")
        self.write("build/fribidi/meson-info/intro-installed.json", L.B.json_bytes({str(source): "/usr/include/fribidi/fribidi.h"}))
        with self.assertRaisesRegex(L.BuildError, "escapes"):
            L.install_development_files(self.root, package)
        self.assertFalse((self.root / "prefix").exists())
        self.assertEqual(source.read_bytes(), b"private outside bytes")

    def test_install_manifest_symlink_source_rejected(self):
        package = L.selected_inputs(REPO)[2]["packages"][0]
        target = self.repo / "outside.h"
        target.write_bytes(b"keep")
        source = self.root / "sources/fribidi-1.0.17/lib/fribidi.h"
        source.parent.mkdir(parents=True)
        source.symlink_to(target)
        self.write("build/fribidi/meson-info/intro-installed.json", L.B.json_bytes({str(source): "/usr/include/fribidi/fribidi.h"}))
        with self.assertRaises(L.BuildError):
            L.install_development_files(self.root, package)
        self.assertFalse((self.root / "prefix").exists())


if __name__ == "__main__":
    unittest.main()
