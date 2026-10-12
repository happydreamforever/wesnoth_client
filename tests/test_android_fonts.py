"""Check the independent font prefix and its portable Android artifact receipt."""

import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_android_bootstrap import ar_library, elf_program


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("android_fonts", REPO / "tools/build_android_fonts.py")
F = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(F)


class AndroidFontTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wesnoth fonts build ")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.root = self.repo / "build/android-fonts"
        self.profile = dict(F.B.SELECTED_PROFILE)
        self.sources = {"sources/freetype-2.13.0/CMakeLists.txt": b"fixture",
                        "sources/freetype-2.13.0/LICENSE.TXT": b"license"}
        self.pins = {"freetype-2.13.0.tar.xz": {"bytes": 1, "sha256": "a" * 64}}
        self.ndk = Path(self.temp.name) / "Android SDK/ndk/26.3.11579264"
        self.ndk_tools = {"cmake_toolchain": self.ndk / "build/cmake/android.toolchain.cmake"}

    def write(self, relative, data=b"fixture"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def prepare(self):
        return F.prepare_sources(self.root, self.sources, self.profile, self.pins)

    def populate(self):
        self.prepare()
        for name in F.REQUIRED_HEADERS:
            self.write("prefix/include/" + name)
        for name in F.REQUIRED_ARCHIVES:
            self.write("prefix/lib/" + name, ar_library(objects=2))
        for name in F.ARTIFACTS:
            self.write(name, elf_program(needed=("libc++_shared.so", "libc.so", "libm.so")))

    def test_real_plan_has_exact_independent_contract(self):
        profile, inputs, packages, pins = F.selected_inputs(REPO)
        self.assertEqual(profile, self.profile)
        self.assertEqual(len(inputs), 9)
        self.assertEqual(set(inputs), set(F.PROJECT_INPUTS))
        self.assertEqual(len(pins), 2)
        self.assertEqual([p["name"] for p in packages], ["freetype", "harfbuzz"])
        self.assertIn("FT_DISABLE_HARFBUZZ=ON", packages[0]["cmake_options"])
        self.assertIn("HB_HAVE_FREETYPE=ON", packages[1]["cmake_options"])
        # HarfBuzz 7.1 reads this before including GNUInstallDirs. Without it,
        # a successful library build exports the unusable include path /harfbuzz.
        self.assertIn("CMAKE_INSTALL_INCLUDEDIR:STRING=include", packages[1]["cmake_options"])

    def test_profile_mismatch_blocks_before_creating_stage(self):
        path = self.repo / "tools/android-build-profile.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"schema_version": 1, **self.profile, "abi": "arm64-v8a"}))
        with self.assertRaisesRegex(F.BuildError, "selected Wesnoth"):
            F.selected_inputs(self.repo)
        self.assertFalse(self.root.exists())

    def test_unmanaged_and_edited_sources_are_preserved(self):
        user = self.write("user.txt", b"preserve")
        with self.assertRaisesRegex(F.BuildError, "unmanaged"):
            self.prepare()
        self.assertEqual(user.read_bytes(), b"preserve")
        user.unlink()
        self.root.rmdir()
        self.prepare()
        target = self.write("sources/freetype-2.13.0/CMakeLists.txt", b"user edit")
        with self.assertRaisesRegex(F.BuildError, "changed, added or removed"):
            self.prepare()
        self.assertEqual(target.read_bytes(), b"user edit")

    def test_unchanged_sources_reuse_without_rewriting_and_new_files_block(self):
        marker = self.prepare()
        timestamps = {name: (self.root / name).stat().st_mtime_ns for name in self.sources}
        self.assertEqual(self.prepare(), marker)
        self.assertEqual(timestamps, {name: (self.root / name).stat().st_mtime_ns for name in self.sources})
        extra = self.write("sources/freetype-2.13.0/user.txt", b"preserve")
        with self.assertRaisesRegex(F.BuildError, "changed, added or removed"):
            self.prepare()
        self.assertEqual(extra.read_bytes(), b"preserve")

    def test_source_and_output_redirects_block_reuse(self):
        self.prepare()
        target = self.repo / "outside"
        target.mkdir()
        (self.root / "build").symlink_to(target, target_is_directory=True)
        with self.assertRaises(F.BuildError):
            self.prepare()
        self.assertTrue((self.root / "build").is_symlink())
        self.assertEqual(list(target.iterdir()), [])

    def test_prefix_state_preserves_edits_and_incomplete_installs(self):
        self.prepare()
        target = self.write("prefix/include/ft2build.h", b"original")
        with self.assertRaisesRegex(F.BuildError, "no owned file record"):
            F.verify_prefix(self.root)
        F.save_prefix_state(self.root)
        F.verify_prefix(self.root)
        target.write_bytes(b"user edit")
        with self.assertRaisesRegex(F.BuildError, "changed or an earlier install"):
            F.verify_prefix(self.root)
        self.assertEqual(target.read_bytes(), b"user edit")

    def test_prefix_case_collision_is_rejected_before_receipt(self):
        self.prepare()
        self.write("prefix/share/licenses/FreeType/first.txt")
        self.write("prefix/share/licenses/freetype/second.txt")
        with self.assertRaisesRegex(F.BuildError, "collide on Windows"):
            F.save_prefix_state(self.root)

    def test_pkgconfig_and_known_harfbuzz_dependency_become_relocatable(self):
        self.prepare()
        prefix = self.root / "prefix"
        pc = self.write("prefix/lib/pkgconfig/freetype2.pc", ("prefix=" + prefix.as_posix() + "\nLibs: -L" + prefix.as_posix() + "/lib -lfreetype\n").encode())
        export = self.write("prefix/lib/cmake/harfbuzz/harfbuzzConfig.cmake", (
            'get_filename_component(_IMPORT_PREFIX "${CMAKE_CURRENT_LIST_FILE}" PATH)\n'
            'set_target_properties(harfbuzz::harfbuzz PROPERTIES\n'
            ' INTERFACE_LINK_LIBRARIES "' + prefix.as_posix() + '/lib/libfreetype.a")\n').encode())
        F.normalize_prefix(prefix)
        self.assertIn("prefix=${pcfiledir}/../..", pc.read_text())
        self.assertIn("-L${prefix}/lib", pc.read_text())
        self.assertIn('"${_IMPORT_PREFIX}/lib/libfreetype.a"', export.read_text())
        self.assertNotIn(prefix.as_posix(), export.read_text())

    def test_unknown_export_structure_is_not_silently_rewritten(self):
        self.prepare()
        prefix = self.root / "prefix"
        original = ("set(SOMETHING " + prefix.as_posix() + "/lib/libfreetype.a)").encode()
        export = self.write("prefix/lib/cmake/harfbuzz/harfbuzzConfig.cmake", original)
        with self.assertRaisesRegex(F.BuildError, "cannot be safely relocated"):
            F.normalize_prefix(prefix)
        self.assertEqual(export.read_bytes(), original)

    def test_font_probe_valid_without_unneeded_system_zlib(self):
        path = self.write("probe", elf_program(needed=("libc++_shared.so", "libc.so", "libm.so")))
        result = F.inspect_probe(path)
        self.assertEqual(result["android_api_notes"], [23])
        self.assertEqual(result["interpreter"], "/system/bin/linker64")
        self.assertNotIn("libz.so", result["needed"])

    def test_font_probe_rejects_wrong_loader_api_runtime_and_host_dependencies(self):
        for data in (elf_program(api=28), elf_program(interpreter="/lib64/ld-linux-x86-64.so.2"),
                     elf_program(needed=("libc.so",)), elf_program(needed=("libc.so.6",)), elf_program(rpath=True)):
            with self.subTest(data=data[700:735]), self.assertRaises(F.BuildError):
                F.inspect_probe(self.write("probe", data))

    def test_full_prefix_requires_exact_static_libraries_and_headers(self):
        self.populate()
        validation, records = F.validate_prefix(self.root, self.ndk_tools)
        self.assertEqual(set(validation["static_libraries"]), {"lib/libfreetype.a", "lib/libharfbuzz.a"})
        self.assertEqual(len(records), len(F.REQUIRED_HEADERS) + 4)
        extra = self.write("prefix/lib/unexpected.a", ar_library(objects=1))
        with self.assertRaisesRegex(F.BuildError, "unexpected static"):
            F.validate_prefix(self.root, self.ndk_tools)
        extra.unlink()
        (self.root / "prefix/include/harfbuzz/hb-ft.h").unlink()
        with self.assertRaisesRegex(F.BuildError, "Installed font header"):
            F.validate_prefix(self.root, self.ndk_tools)

    def test_remaining_host_metadata_is_rejected(self):
        self.populate()
        for path in (self.repo / "cached-library.a", self.ndk / "sysroot/hostlib.a"):
            metadata = self.write("prefix/lib/cmake/dependency.cmake", str(path).encode())
            with self.subTest(path=path), self.assertRaisesRegex(F.BuildError, "build-host path"):
                F.validate_prefix(self.root, self.ndk_tools)
            metadata.unlink()

    def test_failed_rebuild_removes_stale_receipt_and_preserves_outputs(self):
        self.populate()
        records = F.save_prefix_state(self.root)
        receipt = {"schema_version": 1, "kind": F.KIND, "profile": self.profile, "prefix_files": records,
                   "artifacts": {name: records[name] for name in F.ARTIFACTS}}
        (self.root / "receipt.json").write_text(json.dumps(receipt))
        package = {"name": "freetype", "archive": "freetype-2.13.0.tar.xz", "source_dir": "freetype-2.13.0",
                   "license_files": ["LICENSE.TXT"], "cmake_options": []}
        args = argparse.Namespace(ndk=self.ndk, cmake=Path("cmake"), ninja=Path("ninja"), jobs=2, archive_directory=None)
        with (patch.object(F, "selected_inputs", return_value=(self.profile, {}, [package], self.pins)),
              patch.object(F.M, "archive_data", return_value=b"fixture"),
              patch.object(F.M, "archive_files", return_value=self.sources),
              patch.object(F.B, "ndk_paths", return_value=self.ndk_tools),
              patch.object(F.B, "check_tools", return_value={}),
              patch.object(F.M, "run", side_effect=F.BuildError("configure failed"))):
            with self.assertRaisesRegex(F.BuildError, "configure failed"):
                F.build(self.repo, args)
        self.assertFalse((self.root / "receipt.json").exists())
        self.assertEqual(F.M.inventory(self.root), records)
        F.verify_prefix(self.root)


if __name__ == "__main__":
    unittest.main()
