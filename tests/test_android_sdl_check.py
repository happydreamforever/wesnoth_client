"""Check offline SDL preparation, preserved work and APK validation boundaries."""

import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("android_sdl_check", REPO / "tools/build_android_sdl_check.py")
SDL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SDL)


def source_archive(extra=None):
    content = {
        "CMakeLists.txt": b"cmake fixture", "LICENSE.txt": b"license fixture",
        "include/SDL3/SDL_version.h": b"version fixture",
    }
    content.update({"android-project/app/src/main/java/org/libsdl/app/" + name: name.encode("ascii") for name in SDL.JAVA_NAMES})
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name, data in content.items():
            member = tarfile.TarInfo(SDL.SDL_ROOT + "/" + name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        if extra is not None:
            member = tarfile.TarInfo(extra[0])
            member.type = extra[1]
            member.linkname = "../../outside"
            archive.addfile(member, io.BytesIO())
    return stream.getvalue()


class AndroidSdlCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wesnoth sdl check ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "android-sdl-apk"
        self.profile = SDL.B.read_json(REPO / "tools/android-build-profile.json")
        self.sources = SDL.archive_files(source_archive())
        self.files = SDL.generated_files(REPO, self.profile, b"wrapper fixture", self.sources)
        self.pin = {"name": SDL.ARCHIVE_NAME, "bytes": 10, "sha256": "0" * 64}

    def prepare(self):
        return SDL.prepare(self.root, self.files, self.pin)

    def test_java_glue_is_exactly_from_verified_archive(self):
        self.assertEqual(len(SDL.JAVA_NAMES), 11)
        for name in SDL.JAVA_NAMES:
            self.assertEqual(self.files["app/src/main/java/org/libsdl/app/" + name], name.encode("ascii"))
        gradle = self.files["app/build.gradle"].decode()
        self.assertIn("keepDebugSymbols += ['**/*.so']", gradle)
        self.assertIn("applicationId 'org.wesnoth.offlinecheck'", gradle)
        self.assertIn("ndkVersion '26.3.11579264'", gradle)
        self.assertIn("android.builder.sdkDownload=false", self.files["gradle.properties"].decode())

    def test_missing_or_extra_sdl_java_fails(self):
        missing = dict(self.sources)
        missing.pop(next(name for name in missing if name.endswith("SDL.java")))
        with self.assertRaisesRegex(SDL.BuildError, "eleven"):
            SDL.generated_files(REPO, self.profile, b"wrapper", missing)
        extra = dict(self.sources)
        extra["sources/SDL3-3.4.2/android-project/app/src/main/java/org/libsdl/app/Extra.java"] = b"extra"
        with self.assertRaisesRegex(SDL.BuildError, "eleven"):
            SDL.generated_files(REPO, self.profile, b"wrapper", extra)

    def test_unsafe_or_linked_archive_member_is_rejected(self):
        for name, kind in (
            ("SDL3-3.4.2/../../outside", tarfile.REGTYPE),
            ("SDL3-3.4.2/CMakeLists.txt", tarfile.REGTYPE),
            ("SDL3-3.4.2/CON.txt", tarfile.REGTYPE),
            ("SDL3-3.4.2/link", tarfile.SYMTYPE),
            ("SDL3-3.4.2/link", tarfile.LNKTYPE),
        ):
            with self.subTest(name=name):
                with self.assertRaises(SDL.BuildError):
                    SDL.archive_files(source_archive((name, kind)))

    def test_unmanaged_destination_is_preserved(self):
        self.root.mkdir()
        file = self.root / "user.txt"
        file.write_text("keep")
        with self.assertRaisesRegex(SDL.BuildError, "unmanaged"):
            self.prepare()
        self.assertEqual(file.read_text(), "keep")
        self.assertEqual(list(self.root.iterdir()), [file])

    def test_unchanged_source_files_are_not_rewritten(self):
        self.prepare()
        before = {name: (self.root / name).stat().st_mtime_ns for name in self.files}
        self.prepare()
        self.assertEqual(before, {name: (self.root / name).stat().st_mtime_ns for name in self.files})

    def test_modified_managed_source_is_preserved(self):
        self.prepare()
        path = self.root / "app/build.gradle"
        path.write_text("user edit")
        with self.assertRaisesRegex(SDL.BuildError, "changed or removed"):
            self.prepare()
        self.assertEqual(path.read_text(), "user edit")

    def test_extra_managed_source_is_preserved(self):
        self.prepare()
        path = self.root / "sources/SDL3-3.4.2/user.c"
        path.write_text("user edit")
        with self.assertRaisesRegex(SDL.BuildError, "Extra file"):
            self.prepare()
        self.assertEqual(path.read_text(), "user edit")

    def test_verified_generated_files_can_be_updated_without_touching_build_outputs(self):
        self.prepare()
        output = self.root / "native-build/user-note.txt"
        output.parent.mkdir()
        output.write_text("keep")
        self.files["build.gradle"] += b"\n// generation update\n"
        self.prepare()
        self.assertEqual((self.root / "build.gradle").read_bytes(), self.files["build.gradle"])
        self.assertEqual(output.read_text(), "keep")

    def test_missing_gradle_distribution_is_blocked_before_wrapper(self):
        home = self.root / "gradle-home"
        with self.assertRaisesRegex(SDL.BuildError, "already cached"):
            SDL.require_cached_gradle(home, self.profile)
        self.assertFalse(home.exists())

    def test_exact_wrapper_url_cache_requires_completed_marker(self):
        home = self.root / "gradle-home"
        url = b"https://services.gradle.org/distributions/gradle-8.9-bin.zip"
        key = SDL.base36(int(SDL.hashlib.md5(url, usedforsecurity=False).hexdigest(), 16))
        folder = home / "wrapper/dists/gradle-8.9-bin" / key
        launcher = folder / "gradle-8.9/lib/gradle-launcher-8.9.jar"
        launcher.parent.mkdir(parents=True)
        launcher.write_bytes(b"launcher")
        with self.assertRaisesRegex(SDL.BuildError, "already cached"):
            SDL.require_cached_gradle(home, self.profile)
        (folder / "gradle-8.9-bin.zip.ok").touch()
        self.assertEqual(SDL.require_cached_gradle(home, self.profile), launcher)

    def make_apk(self, extra_library=None, changed_library=False):
        self.root.mkdir(exist_ok=True)
        apk = self.root / "check.apk"
        with zipfile.ZipFile(apk, "w") as archive:
            archive.writestr("AndroidManifest.xml", b"manifest fixture")
            archive.writestr("classes.dex", b"dex fixture")
            for name in SDL.LIBRARIES:
                data = ("compiled-" + name).encode("ascii")
                path = self.root / SDL.JNI_ROOT / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                archive.writestr("lib/x86_64/" + name, b"changed" if changed_library else data)
            if extra_library:
                archive.writestr(extra_library, b"other abi")
        return apk

    def test_apk_payload_metadata_and_signature_are_all_checked(self):
        apk = self.make_apk()
        badging = ("package: name='org.wesnoth.offlinecheck' versionCode='1' versionName='1.0' platformBuildVersionName='15'\n"
                   "sdkVersion:'23'\ntargetSdkVersion:'35'\n"
                   "launchable-activity: name='org.wesnoth.offlinecheck.CheckActivity' label='Wesnoth SDL Check'\n"
                   "native-code: 'x86_64'\n")
        with patch.object(SDL, "run", side_effect=[badging, ""]) as run:
            result = SDL.verify_apk(apk, self.root, Path("aapt2"), Path("java"), Path("apksigner.jar"))
        self.assertTrue(result["signature_verified"])
        self.assertEqual(run.call_args_list[-1].args[0], [Path("java"), "-jar", Path("apksigner.jar"), "verify", "--verbose", apk])

    def test_wrong_native_payload_or_abi_rejected_before_external_tools(self):
        for kwargs in ({"changed_library": True}, {"extra_library": "lib/arm64-v8a/libmain.so"}):
            with self.subTest(kwargs=kwargs):
                apk = self.make_apk(**kwargs)
                with patch.object(SDL, "run") as run:
                    with self.assertRaises(SDL.BuildError):
                        SDL.verify_apk(apk, self.root, Path("aapt2"), Path("java"), Path("apksigner.jar"))
                    run.assert_not_called()

    def test_wrong_package_metadata_prevents_signature_success(self):
        apk = self.make_apk()
        with patch.object(SDL, "run", return_value="package: name='wrong.package'\n") as run:
            with self.assertRaisesRegex(SDL.BuildError, "metadata"):
                SDL.verify_apk(apk, self.root, Path("aapt2"), Path("java"), Path("apksigner.jar"))
            self.assertEqual(run.call_count, 1)

    def test_graphics_library_uses_explicit_android_dependencies_and_api(self):
        path = self.root / "libmain.so"
        path.parent.mkdir()
        # The ELF header parser is shared with the independently tested native
        # bootstrap; llvm-readelf supplies the dynamic and Android-note fields.
        path.write_bytes(b"fixture")
        notes = "Android 0x00000084 NT_ANDROID_TYPE_IDENT\n description data: 17 00 00 00 72 32 36 64\n"
        dynamic = "(NEEDED) Shared library: [libSDL3.so]\n(NEEDED) Shared library: [libc++_shared.so]\n"
        with patch.object(SDL.B, "elf_header", return_value=3), patch.object(SDL, "run", return_value=dynamic + notes):
            self.assertEqual(SDL.inspect_library(path, Path("readelf"))["android_api"], 23)
        for output in (dynamic + notes.replace("17 00", "1c 00"),
                       dynamic + notes + "(NEEDED) Shared library: [libstdc++.so.6]\n",
                       dynamic + notes + "(RUNPATH) Library runpath: [/host/lib]\n"):
            with patch.object(SDL.B, "elf_header", return_value=3), patch.object(SDL, "run", return_value=output):
                with self.assertRaises(SDL.BuildError):
                    SDL.inspect_library(path, Path("readelf"))

    def test_changed_recorded_artifact_is_preserved(self):
        apk = self.make_apk()
        (self.root / SDL.APK).parent.mkdir(parents=True)
        (self.root / SDL.APK).write_bytes(apk.read_bytes())
        receipt = {"kind": SDL.KIND, "artifacts": {name: SDL.B.fingerprint(self.root / name) for name in SDL.ARTIFACTS}}
        (self.root / "receipt.json").write_bytes(SDL.B.json_bytes(receipt))
        (self.root / SDL.APK).write_bytes(b"user edit")
        with self.assertRaisesRegex(SDL.BuildError, "artifact was edited"):
            SDL.verify_previous(self.root)
        self.assertEqual((self.root / SDL.APK).read_bytes(), b"user edit")


if __name__ == "__main__":
    unittest.main()
