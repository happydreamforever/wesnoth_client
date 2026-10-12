"""Exercise offline media source ownership, prefix reuse and ABI boundaries."""

import argparse
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("android_media", REPO / "tools/build_android_media.py")
MEDIA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MEDIA)


def elf(machine=62, kind=1):
    return b"\x7fELF\x02\x01\x01" + b"\0" * 9 + struct.pack("<HHIQQQIHHHHHH", kind, machine, 1, 0, 0, 0, 0, 64, 0, 0, 0, 0, 0)


def static_archive(objects=(elf(),)):
    data = b"!<arch>\n"
    for index, content in enumerate(objects):
        header = (f"unit{index}.o/".ljust(16) + "0".ljust(12) + "0".ljust(6) + "0".ljust(6)
                  + "644".ljust(8) + str(len(content)).ljust(10) + "`\n").encode("ascii")
        data += header + content + (b"\n" if len(content) % 2 else b"")
    return data


def source_archive(source="libpng-1.6.39", extra=()):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, kind, link in [(source + "/CMakeLists.txt", tarfile.REGTYPE, ""),
                                 (source + "/LICENSE", tarfile.REGTYPE, "")] + list(extra):
            member = tarfile.TarInfo(name)
            member.type = kind
            member.linkname = link
            data = (name + " fixture").encode("utf-8") if kind == tarfile.REGTYPE else b""
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return buffer.getvalue()


def readelf_output(needed=("libSDL3.so", "libSDL3_image.so", "libSDL3_mixer.so", "libc++_shared.so", "libc.so"),
                   api="17", loader="/system/bin/linker64", rpath=False):
    output = "\n".join(" 0x0000000000000001 (NEEDED) Shared library: [" + name + "]" for name in needed)
    output += "\n Android 0x00000084 NT_ANDROID_TYPE_IDENT\n description data: " + api + " 00 00 00\n"
    output += "[Requesting program interpreter: " + loader + "]\n"
    if rpath:
        output += " 0x000000000000001d (RUNPATH) Library runpath: [/usr/lib]\n"
    return output


class AndroidMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wesnoth media build ")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.root = self.repo / "build/android-media"
        self.data = source_archive()
        self.sources = MEDIA.archive_files(self.data, "libpng-1.6.39")
        self.pin = {"bytes": len(self.data), "sha256": MEDIA.B.sha256(self.data)}
        self.profile = dict(MEDIA.B.SELECTED_PROFILE)
        self.ndk = Path(self.temp.name) / "SDK/ndk/26.3.11579264"
        self.ndk_tools = {"cmake_toolchain": self.ndk / "build/cmake/android.toolchain.cmake",
                          "llvm-readelf": self.ndk / "llvm-readelf"}

    def prepare(self):
        return MEDIA.prepare_sources(self.root, self.sources, self.profile, {"png": self.pin})

    def write(self, relative, data=b"fixture"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def populated_prefix(self):
        self.prepare()
        for name in MEDIA.REQUIRED_HEADERS:
            self.write("prefix/include/" + name)
        for name in MEDIA.REQUIRED_ARCHIVES:
            self.write("prefix/lib/" + name, static_archive())
        for name in MEDIA.ARTIFACTS:
            self.write(name, elf(kind=3))

    def test_real_profile_plan_and_exact_project_contract(self):
        profile, inputs, packages, pins = MEDIA.selected_inputs(REPO)
        self.assertEqual(profile, self.profile)
        self.assertEqual(set(inputs), set(MEDIA.PROJECT_INPUTS))
        self.assertEqual(len(packages), 7)
        self.assertEqual(len(pins), 7)
        self.assertIn("ZLIB_LIBRARY:STRING=z", packages[0]["cmake_options"])
        self.assertIn("M_LIBRARY:STRING=m", packages[0]["cmake_options"])

    def test_archive_hash_verified_and_explicit_cache_is_authoritative(self):
        folder = self.repo / "packaging/android/download"
        folder.mkdir(parents=True)
        path = folder / "source.tar.gz"
        path.write_bytes(self.data)
        self.assertEqual(MEDIA.archive_data(self.repo, path.name, self.pin), self.data)
        with self.assertRaisesRegex(MEDIA.BuildError, "Missing"):
            MEDIA.archive_data(self.repo, path.name, self.pin, self.repo / "absent")
        path.write_bytes(self.data[:-1] + bytes([self.data[-1] ^ 1]))
        with self.assertRaisesRegex(MEDIA.BuildError, "size/hash"):
            MEDIA.archive_data(self.repo, path.name, self.pin)
        self.assertFalse(self.root.exists())

    def test_archive_path_and_link_boundaries_precede_writes(self):
        for name, kind in (("libpng-1.6.39/../../outside", tarfile.REGTYPE),
                           ("/absolute", tarfile.REGTYPE), ("libpng-1.6.39/NUL.txt", tarfile.REGTYPE),
                           ("libpng-1.6.39/file. ", tarfile.REGTYPE), ("libpng-1.6.39/CMAKELISTS.TXT", tarfile.REGTYPE),
                           ("libpng-1.6.39/link", tarfile.SYMTYPE), ("libpng-1.6.39/hard", tarfile.LNKTYPE)):
            with self.subTest(name=name, kind=kind), self.assertRaises(MEDIA.BuildError):
                MEDIA.archive_files(source_archive(extra=[(name, kind, "../../outside")]), "libpng-1.6.39")
        self.assertFalse(self.root.exists())

    def test_only_inspected_xcode_links_can_be_omitted(self):
        source = "SDL3_image-3.4.0"
        links = [(source + "/" + name, tarfile.SYMTYPE, target) for name, target in MEDIA.KNOWN_XCODE_LINKS.items()]
        extras = links + [(source + "/Xcode/unused.txt", tarfile.REGTYPE, "")]
        files = MEDIA.archive_files(source_archive(source, extras), source)
        self.assertEqual(len(files), 2)
        self.assertFalse(any("Xcode" in name for name in files))
        for bad in (links[:-1], links + [(source + "/Xcode/new-link", tarfile.SYMTYPE, "outside")],
                    [(links[0][0], tarfile.SYMTYPE, "../../../outside")] + links[1:]):
            with self.subTest(members=bad), self.assertRaises(MEDIA.BuildError):
                MEDIA.archive_files(source_archive(source, bad), source)

    def test_unmanaged_root_and_changed_sources_are_preserved(self):
        self.root.mkdir(parents=True)
        user = self.write("user.txt", b"preserve")
        with self.assertRaisesRegex(MEDIA.BuildError, "unmanaged"):
            self.prepare()
        self.assertEqual(user.read_bytes(), b"preserve")
        user.unlink()
        self.root.rmdir()
        self.prepare()
        target = self.write(next(iter(self.sources)), b"user edit")
        with self.assertRaisesRegex(MEDIA.BuildError, "changed, added or removed"):
            self.prepare()
        self.assertEqual(target.read_bytes(), b"user edit")

    def test_unchanged_source_reuse_does_not_rewrite(self):
        marker = self.prepare()
        before = {name: (self.root / name).stat().st_mtime_ns for name in self.sources}
        self.assertEqual(self.prepare(), marker)
        self.assertEqual(before, {name: (self.root / name).stat().st_mtime_ns for name in self.sources})
        extra = self.write("sources/libpng-1.6.39/extra", b"preserve")
        with self.assertRaisesRegex(MEDIA.BuildError, "changed, added or removed"):
            self.prepare()
        self.assertEqual(extra.read_bytes(), b"preserve")

    def test_known_private_png_aliases_allow_incremental_build_only(self):
        self.prepare()
        target = self.write("build/png/libpng16.a", b"archive")
        alias = target.with_name("libpng.a")
        alias.symlink_to(target.name)
        self.prepare()
        alias.unlink()
        alias.symlink_to("../../outside")
        with self.assertRaises(MEDIA.BuildError):
            self.prepare()
        self.assertTrue(alias.is_symlink())

    def test_prefix_edits_and_interrupted_install_are_preserved(self):
        self.prepare()
        target = self.write("prefix/include/header.h", b"original")
        with self.assertRaisesRegex(MEDIA.BuildError, "no owned file record"):
            MEDIA.verify_prefix(self.root)
        MEDIA.save_prefix_state(self.root)
        MEDIA.verify_prefix(self.root)
        target.write_bytes(b"user edit")
        with self.assertRaisesRegex(MEDIA.BuildError, "changed or a previous install"):
            MEDIA.verify_prefix(self.root)
        self.assertEqual(target.read_bytes(), b"user edit")

    def test_prefix_symlink_rejected_before_hashing_outside_tree(self):
        self.prepare()
        external = self.repo / "outside"
        external.write_text("preserve")
        (self.root / "prefix").mkdir()
        (self.root / "prefix/alias").symlink_to(external)
        with self.assertRaises(MEDIA.BuildError):
            MEDIA.inventory(self.root)
        self.assertEqual(external.read_text(), "preserve")

    def test_case_colliding_prefix_directories_are_rejected_for_windows(self):
        self.prepare()
        self.write("prefix/share/licenses/SDL3/LICENSE.txt")
        self.write("prefix/share/licenses/sdl3/COPYING")
        with self.assertRaisesRegex(MEDIA.BuildError, "collide on Windows"):
            MEDIA.inventory(self.root)

    def test_png_aliases_materialized_and_pkgconfig_relocated(self):
        self.prepare()
        prefix = self.root / "prefix"
        archive = self.write("prefix/lib/libpng16.a", b"static archive")
        archive.with_name("libpng.a").symlink_to(archive.name)
        metadata = self.write("prefix/lib/pkgconfig/libpng16.pc", ("prefix=" + prefix.as_posix() + "\nLibs: -L" + prefix.as_posix() + "/lib -lpng16\n").encode())
        metadata.with_name("libpng.pc").symlink_to(metadata.name)
        helper = self.write("prefix/bin/libpng16-config", b"host helper")
        helper.with_name("libpng-config").symlink_to(helper.name)
        MEDIA.normalize_prefix(prefix)
        self.assertEqual((prefix / "lib/libpng.a").read_bytes(), archive.read_bytes())
        self.assertFalse((prefix / "lib/libpng.a").is_symlink())
        for name in ("libpng.pc", "libpng16.pc"):
            content = (metadata.parent / name).read_text()
            self.assertIn("prefix=${pcfiledir}/../..", content)
            self.assertIn("-L${prefix}/lib", content)
            self.assertNotIn(prefix.as_posix(), content)
        self.assertFalse(helper.exists())

    def test_unexpected_png_alias_is_not_followed_or_replaced(self):
        self.prepare()
        path = self.write("prefix/lib/user.a", b"preserve")
        alias = path.with_name("libpng.a")
        alias.symlink_to(path.name)
        with self.assertRaisesRegex(MEDIA.BuildError, "Unexpected installed libpng alias"):
            MEDIA.normalize_prefix(self.root / "prefix")
        self.assertEqual(path.read_bytes(), b"preserve")
        self.assertTrue(alias.is_symlink())

    def test_cross_configuration_clears_host_search_and_disables_fetch(self):
        prefix = self.root / "prefix"
        with patch.dict(os.environ, {key: "/host" for key in ("CPATH", "LIB", "CMAKE_PREFIX_PATH", "PKG_CONFIG_PATH", "DESTDIR")}, clear=True):
            env = MEDIA.isolated_environment(prefix)
        self.assertEqual(env, {"PKG_CONFIG_PATH": "", "PKG_CONFIG_LIBDIR": str(prefix / "lib/pkgconfig")})
        args = argparse.Namespace(cmake=Path("cmake"), ninja=Path("ninja"))
        command = MEDIA.cmake_arguments(args, self.ndk_tools, prefix, Path("src"), Path("out"))
        for expected in ("-DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=ONLY", "-DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=ONLY",
                         "-DCMAKE_FIND_ROOT_PATH_MODE_PACKAGE=ONLY", "-DCMAKE_DISABLE_FIND_PACKAGE_PkgConfig=ON",
                         "-DFETCHCONTENT_FULLY_DISCONNECTED=ON", "-DFETCHCONTENT_UPDATES_DISCONNECTED=ON"):
            self.assertIn(expected, command)

    def test_static_archive_checks_every_member_and_padding(self):
        path = self.repo / "static.a"
        path.write_bytes(static_archive((elf(), elf())))
        self.assertEqual(MEDIA.inspect_static_archive(path)["elf_object_count"], 2)
        for data in (static_archive((elf(), elf(machine=183))), static_archive((elf(), b"MZ host object")),
                     static_archive(()) , b"!<thin>\n", static_archive()[:-1], static_archive() + b"x"):
            with self.subTest(data=data[:12]), self.assertRaises(MEDIA.BuildError):
                path.write_bytes(data)
                MEDIA.inspect_static_archive(path)

    def test_dynamic_artifacts_require_android_api_and_allowed_dependencies(self):
        path = self.repo / "probe"
        path.write_bytes(elf(kind=3))
        with patch.object(MEDIA, "run", return_value=readelf_output()):
            result = MEDIA.inspect_dynamic(path, Path("readelf"), {}, executable=True)
        self.assertEqual(result["android_api"], 23)
        for output in (readelf_output(api="1c"), readelf_output(loader="/lib64/ld-linux-x86-64.so.2"),
                       readelf_output(rpath=True), readelf_output(needed=("libc.so.6",)),
                       readelf_output(needed=("libc.so",))):
            with self.subTest(output=output), patch.object(MEDIA, "run", return_value=output), self.assertRaises(MEDIA.BuildError):
                MEDIA.inspect_dynamic(path, Path("readelf"), {}, executable=True)

    def test_prefix_validates_extra_static_libraries_not_only_required_names(self):
        self.populated_prefix()
        self.write("prefix/lib/host-extra.a", static_archive((elf(machine=183),)))
        with patch.object(MEDIA, "inspect_dynamic", return_value={}), self.assertRaises(MEDIA.BuildError):
            MEDIA.validate_prefix(self.root, self.ndk_tools, {})

    def test_metadata_cannot_retain_ndk_path_outside_build_tree(self):
        self.populated_prefix()
        metadata = self.write("prefix/lib/libpng/libpng16.cmake")
        for leaked_path in (self.ndk / "sysroot/libz.so", self.repo / "m"):
            metadata.write_text('INTERFACE_LINK_LIBRARIES "' + leaked_path.as_posix() + '"')
            with (self.subTest(path=leaked_path), patch.object(MEDIA, "inspect_dynamic", return_value={}),
                  self.assertRaisesRegex(MEDIA.BuildError, "build-host path")):
                MEDIA.validate_prefix(self.root, self.ndk_tools, {})
        metadata.write_text('INTERFACE_LINK_LIBRARIES "z;m"')
        with patch.object(MEDIA, "inspect_dynamic", return_value={}):
            validation, records = MEDIA.validate_prefix(self.root, self.ndk_tools, {})
        self.assertEqual(len(validation["static_libraries"]), 9)
        self.assertIn("prefix/lib/libpng/libpng16.cmake", records)

    def test_failed_rebuild_invalidates_success_receipt_and_preserves_prefix(self):
        self.prepare()
        for name in MEDIA.ARTIFACTS:
            self.write(name)
        records = MEDIA.save_prefix_state(self.root)
        receipt = {"kind": MEDIA.KIND, "prefix_files": records,
                   "artifacts": {name: records[name] for name in MEDIA.ARTIFACTS}}
        (self.root / "receipt.json").write_text(json.dumps(receipt))
        package = {"name": "png", "archive": "png", "source_dir": "libpng-1.6.39",
                   "license_files": ["LICENSE"], "cmake_options": []}
        args = argparse.Namespace(ndk=self.ndk, cmake=Path("cmake"), ninja=Path("ninja"), jobs=2, archive_directory=None)
        with (patch.object(MEDIA, "selected_inputs", return_value=(self.profile, {}, [package], {"png": self.pin})),
              patch.object(MEDIA, "archive_data", return_value=self.data),
              patch.object(MEDIA.B, "ndk_paths", return_value=self.ndk_tools),
              patch.object(MEDIA.B, "check_tools", return_value={}),
              patch.object(MEDIA, "run", side_effect=MEDIA.BuildError("configure failed"))):
            with self.assertRaisesRegex(MEDIA.BuildError, "configure failed"):
                MEDIA.build(self.repo, args)
        self.assertFalse((self.root / "receipt.json").exists())
        self.assertEqual(MEDIA.inventory(self.root), records)
        MEDIA.verify_prefix(self.root)


if __name__ == "__main__":
    unittest.main()
