"""Verify preservation, explicit build blockers and staging integrity boundaries."""

import ast
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prepare_android_source", REPO / "tools" / "prepare_android_source.py")
PREPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREPARE)


class AndroidSourcePreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wesnoth android staging ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.engine = self.base / "engine"
        self.output = self.base / "build" / "android-source"
        self.profile = PREPARE.load_profile(REPO / "tools" / "android-build-profile.json")
        # Use real upstream overlay inputs. Other source bytes are deliberately
        # small, including a binary file to catch text-only copying mistakes.
        paths = (
            "SConstruct", "packaging/android/build.gradle", "packaging/android/app/build.gradle",
            "packaging/android/gradle/wrapper/gradle-wrapper.properties", "src/modules/lua/lua.h",
            "src/modules/lua/lapi.c",
        )
        self.original = {name: (REPO / "engine" / name).read_bytes() for name in paths}
        self.original["src/binary-fixture.dat"] = b"\x00\xff\x01\r\n"
        manifest = {"upstream": {"commit": PREPARE.UPSTREAM_COMMIT}, "files": []}
        for name, data in self.original.items():
            target = self.engine / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            manifest["files"].append({"path": name, "bytes": len(data), "sha256": PREPARE.digest(data), "git_mode": "100644"})
        manifest_data = PREPARE.json_bytes(manifest)
        (self.engine / "source-manifest.json").write_bytes(manifest_data)
        override = patch.object(PREPARE, "SOURCE_MANIFEST_SHA256", PREPARE.digest(manifest_data))
        override.start()
        self.addCleanup(override.stop)

    def stage(self):
        return PREPARE.prepare(self.engine, self.output, self.profile)

    def test_copies_only_verified_inputs_and_keeps_original_bytes(self):
        (self.engine / ".env").write_text("PRIVATE_SENTINEL=do_not_copy\n")
        (self.engine / "untracked-user-work.txt").write_text("user work")
        self.assertEqual(self.stage(), (True, len(self.original)))
        for name, data in self.original.items():
            self.assertEqual((self.engine / name).read_bytes(), data)
        self.assertFalse((self.output / ".env").exists())
        self.assertFalse((self.output / "untracked-user-work.txt").exists())
        self.assertFalse((self.output / "source-manifest.json").exists())
        self.assertEqual((self.output / PREPARE.ORIGINAL_MANIFEST).read_bytes(), (self.engine / "source-manifest.json").read_bytes())
        self.assertEqual((self.output / "src/binary-fixture.dat").read_bytes(), self.original["src/binary-fixture.dat"])
        marker = json.loads((self.output / PREPARE.STAGE_MARKER).read_text())
        self.assertFalse(marker["windows_android_build_ready"])
        self.assertEqual(marker["upstream_commit"], PREPARE.UPSTREAM_COMMIT)
        self.assertEqual(len(marker["overlay_files"]), 4)
        for name, expected in marker["files"].items():
            self.assertEqual(PREPARE.digest((self.output / name).read_bytes()), expected)

    def test_source_mode_changes_are_ignored_but_content_changes_stop_before_writes(self):
        if os.name != "nt":
            (self.engine / "SConstruct").chmod(0o755)
        self.assertEqual(self.stage()[0], True)
        second_output = self.base / "new-output"
        (self.engine / "SConstruct").write_bytes(b"user edit\n")
        with self.assertRaisesRegex(PREPARE.PreparationError, "SConstruct"):
            PREPARE.prepare(self.engine, second_output, self.profile)
        self.assertFalse(second_output.exists())
        self.assertEqual((self.engine / "SConstruct").read_bytes(), b"user edit\n")

    def test_missing_source_stops_before_output_creation(self):
        (self.engine / "src/modules/lua/lapi.c").unlink()
        with self.assertRaisesRegex(PREPARE.PreparationError, "lapi.c"):
            self.stage()
        self.assertFalse(self.output.exists())

    def test_changed_manifest_cannot_authorize_additional_files(self):
        manifest_path = self.engine / "source-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"].append({"path": ".env", "bytes": 0, "sha256": PREPARE.digest(b""), "git_mode": "100644"})
        manifest_path.write_bytes(PREPARE.json_bytes(manifest))
        with self.assertRaisesRegex(PREPARE.PreparationError, "manifest differs"):
            self.stage()
        self.assertFalse(self.output.exists())

    def test_idempotent_rerun_does_not_touch_existing_files(self):
        self.stage()
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.output.rglob("*") if path.is_file()}
        self.assertEqual(self.stage(), (False, len(self.original)))
        for path, expected in before.items():
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), expected)

    def test_unmanaged_destination_is_preserved(self):
        self.output.mkdir(parents=True)
        user_file = self.output / "user.txt"
        user_file.write_text("preserve")
        with self.assertRaisesRegex(PREPARE.PreparationError, "incomplete or unmanaged"):
            self.stage()
        self.assertEqual(user_file.read_text(), "preserve")
        self.assertEqual(list(self.output.iterdir()), [user_file])

    def test_edited_existing_stage_is_preserved(self):
        self.stage()
        (self.output / "SConstruct").write_text("local staged edit")
        with self.assertRaisesRegex(PREPARE.PreparationError, "staged file was edited: SConstruct"):
            self.stage()
        self.assertEqual((self.output / "SConstruct").read_text(), "local staged edit")

    def test_extra_existing_stage_file_is_preserved(self):
        self.stage()
        user_file = self.output / "user.txt"
        user_file.write_text("preserve")
        with self.assertRaisesRegex(PREPARE.PreparationError, "additional or missing files"):
            self.stage()
        self.assertEqual(user_file.read_text(), "preserve")

    def test_changed_profile_requires_new_destination(self):
        self.stage()
        original_marker = (self.output / PREPARE.STAGE_MARKER).read_bytes()
        changed = dict(self.profile, min_sdk=24)
        with self.assertRaisesRegex(PREPARE.PreparationError, "different provenance or settings"):
            PREPARE.prepare(self.engine, self.output, changed)
        self.assertEqual((self.output / PREPARE.STAGE_MARKER).read_bytes(), original_marker)

    @unittest.skipIf(os.name == "nt", "Symbolic links require separate Windows privileges")
    def test_linked_source_and_overlapping_destination_are_refused(self):
        target = self.engine / "src/modules/lua/lapi.c"
        outside = self.base / "real-lapi.c"
        target.rename(outside)
        target.symlink_to(outside)
        with self.assertRaisesRegex(PREPARE.PreparationError, "Symbolic link"):
            self.stage()
        self.assertFalse(self.output.exists())
        alias = self.base / "engine-alias"
        alias.symlink_to(self.engine, target_is_directory=True)
        with self.assertRaisesRegex(PREPARE.PreparationError, "separate from the preserved engine"):
            PREPARE.prepare(self.engine, alias / "child", self.profile)

    def test_interrupted_copy_has_no_complete_marker_and_is_not_reused(self):
        real_open = Path.open
        count = 0

        def broken_open(path, mode="r", *args, **kwargs):
            nonlocal count
            if mode == "xb":
                count += 1
                if count == 2:
                    raise OSError("simulated disk failure")
            return real_open(path, mode, *args, **kwargs)

        with patch.object(Path, "open", broken_open):
            with self.assertRaisesRegex(PREPARE.PreparationError, "Incomplete stage retained"):
                self.stage()
        self.assertTrue(self.output.exists())
        self.assertFalse((self.output / PREPARE.STAGE_MARKER).exists())
        with self.assertRaisesRegex(PREPARE.PreparationError, "incomplete or unmanaged"):
            self.stage()

    def test_preparation_rejects_overlap_before_any_writes(self):
        for output in (self.engine, self.engine / "child", self.base):
            with self.subTest(output=output):
                with self.assertRaisesRegex(PREPARE.PreparationError, "separate from the preserved engine"):
                    PREPARE.prepare(self.engine, output, self.profile)
        self.assertFalse((self.engine / "child").exists())

    def test_staged_configuration_preserves_android_version_and_blocks_native_build(self):
        self.stage()
        android = self.output / "packaging/android"
        wrapper = (android / "gradle/wrapper/gradle-wrapper.properties").read_text()
        self.assertIn("gradle-8.9-bin.zip", wrapper)
        self.assertIn("\ndistributionSha256Sum=" + self.profile["gradle_sha256"] + "\n", wrapper)
        self.assertEqual(wrapper.count("distributionSha256Sum="), 1)
        app = (android / "app/build.gradle").read_text()
        for required in ("minSdk 23", "compileSdk 35", "targetSdk 35", "buildToolsVersion '34.0.0'", "ndkVersion '26.3.11579264'", 'versionName "1.19.26"', 'include "x86_64"'):
            self.assertIn(required, app)
        self.assertNotIn('include "x86_64",', app)
        root = (android / "build.gradle").read_text()
        self.assertIn("version '8.7.3'", root)
        self.assertIn("it.name == 'preBuild'", root)
        self.assertIn("doFirst { throw new GradleException", root)
        self.assertIn("'buildCppDepends', 'buildCppSource'", root)
        self.assertNotIn("commandLine", root)
        lua_check = (self.output / "SConstruct").read_text()
        self.assertNotIn('File("#/src/modules/lua/.git")', lua_check)
        self.assertIn('("lua.h", "lapi.c")', lua_check)
        ast.parse(lua_check)

    def test_bad_profile_values_are_rejected_without_executing_input(self):
        profile_path = self.base / "profile.json"
        for key, invalid in (("abi", "arm64-v8a"), ("gradle_sha256", ""), ("min_sdk", 29), ("target_sdk", 1), ("compile_sdk", 36), ("schema_version", True), ("agp_version", "8.7.3'; throw new Error('injected')")):
            with self.subTest(key=key):
                profile_path.write_bytes(PREPARE.json_bytes(dict(self.profile, **{key: invalid})))
                with self.assertRaises(PREPARE.PreparationError):
                    PREPARE.load_profile(profile_path)


if __name__ == "__main__":
    unittest.main()
