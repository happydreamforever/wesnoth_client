#!/usr/bin/env python3
"""Build an isolated SDL Android check APK from verified, already cached inputs.

This builds a graphics/input checkpoint, not the Wesnoth game. No engine source,
existing Gradle source stage, native bootstrap, or global dependency is modified.
"""

import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import zipfile


REPO = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("wesnoth_native_bootstrap", REPO / "tools/build_android_bootstrap.py")
B = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(B)
BuildError = B.BuildError
KIND = "wesnoth-android-sdl-check"
MARKER = ".wesnoth-sdl-apk.json"
SDL_VERSION = "3.4.2"
SDL_ROOT = "SDL3-" + SDL_VERSION
ARCHIVE_NAME = SDL_ROOT + ".tar.gz"
PACKAGE = "org.wesnoth.offlinecheck"
ACTIVITY = PACKAGE + ".CheckActivity"
APK = "app/build/outputs/apk/debug/app-debug.apk"
LIBRARIES = ("libSDL3.so", "libmain.so", "libc++_shared.so")
JNI_ROOT = "app/src/main/jniLibs/x86_64/"
ARTIFACTS = (APK,) + tuple(JNI_ROOT + name for name in LIBRARIES)
TEMPLATES = {
    "settings.gradle": "settings.gradle",
    "build.gradle": "build.gradle",
    "app.build.gradle": "app/build.gradle",
    "gradle.properties": "gradle.properties",
    "AndroidManifest.xml": "app/src/main/AndroidManifest.xml",
    "CheckActivity.java": "app/src/main/java/org/wesnoth/offlinecheck/CheckActivity.java",
}
PROJECT_INPUTS = (
    "tools/build_android_sdl_check.py", "tools/build_android_bootstrap.py",
    "tools/android-build-profile.json", "tools/android-native-archives.json",
) + tuple("tools/sdl-check/" + name for name in TEMPLATES) + (
    "tools/sdl-check/native/CMakeLists.txt", "tools/sdl-check/native/main.cpp",
)
JAVA_NAMES = {
    "HIDDevice.java", "HIDDeviceBLESteamController.java", "HIDDeviceManager.java",
    "HIDDeviceUSB.java", "SDL.java", "SDLActivity.java", "SDLAudioManager.java",
    "SDLControllerManager.java", "SDLDummyEdit.java", "SDLInputConnection.java", "SDLSurface.java",
}
WRAPPER_PATH = "engine/packaging/android/gradle/wrapper/gradle-wrapper.jar"
WRAPPER_HASH = "2db75c40782f5e8ba1fc278a5574bab070adccb2d21ca5a6e5ed840888448046"


def run(arguments, env=None, capture=False):
    print("RUN: " + subprocess.list2cmdline([str(arg) for arg in arguments]), flush=True)
    result = subprocess.run([str(arg) for arg in arguments], env=env, check=False,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.STDOUT if capture else None,
                            text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        if capture and result.stdout:
            print(result.stdout, end="", flush=True)
        raise BuildError(f"Tool failed with exit {result.returncode}: {arguments[0]}")
    return result.stdout if capture else ""


def selected_inputs(repo):
    profile = B.read_json(repo / "tools/android-build-profile.json")
    selected = {**B.SELECTED_PROFILE, "schema_version": 1, "compile_sdk": 35, "target_sdk": 35,
                "build_tools": "34.0.0", "gradle_version": "8.9", "agp_version": "8.7.3", "java_major": 21,
                "gradle_sha256": "d725d707bfabd4dfdc958c624003b3c80accc03f7037b5122c4b1d0ef15cecab"}
    if any(profile.get(key) != value for key, value in selected.items()):
        raise BuildError("The SDL check requires the existing pinned Android build profile.")
    inputs = {name: B.fingerprint(B.require_file(repo / name, "SDL check source input")) for name in PROJECT_INPUTS}
    wrapper = B.require_file(repo / WRAPPER_PATH, "Preserved Gradle wrapper JAR").read_bytes()
    if B.sha256(wrapper) != WRAPPER_HASH:
        raise BuildError("The preserved Gradle wrapper JAR differs from its restored upstream hash.")
    return profile, inputs, wrapper


def verified_archive(repo, directory=None):
    manifest = B.read_json(repo / "tools/android-native-archives.json")
    pins = [entry for entry in manifest.get("archives", []) if entry.get("name") == ARCHIVE_NAME]
    if len(pins) != 1:
        raise BuildError("The native archive manifest must identify SDL3 3.4.2 exactly once.")
    pin = {key: pins[0][key] for key in ("name", "bytes", "sha256")}
    roots = [directory] if directory is not None else [repo / name for name in (
        "packaging/android/download", "packaging/android/downloads",
        "engine/packaging/android/download", "engine/packaging/android/downloads",
    )]
    if directory is None and sys.platform == "win32":
        roots.append(Path("D:/Test/msys64/tmp/android-dl"))
    for folder in roots:
        path = folder / ARCHIVE_NAME
        if path.exists():
            data = B.require_file(path, "SDL3 archive").read_bytes()
            if len(data) != pin["bytes"] or B.sha256(data) != pin["sha256"]:
                raise BuildError(f"SDL3 archive size/hash differs from its recorded pin: {path}")
            print(f"Verified offline archive: {path}", flush=True)
            return pin, data
    raise BuildError("SDL3 3.4.2 archive is missing. Supply --archive-directory; this build never downloads.")


def archive_files(data):
    files, seen = {}, set()
    total = 0
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"{kind}{i}" for kind in ("COM", "LPT") for i in range(1, 10)}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive:
            name = member.name.rstrip("/") if member.isdir() else member.name
            parts = name.split("/")
            if (parts[0] != SDL_ROOT or any(part in ("", ".", "..") or part.endswith((".", " ")) for part in parts)
                    or "\\" in name or ":" in name
                    or any(part.split(".")[0].upper() in reserved for part in parts)):
                raise BuildError(f"Unsafe SDL archive path: {name}")
            if member.isdir():
                continue
            if len(parts) < 2 or not member.isfile() or name.casefold() in seen:
                raise BuildError(f"Linked, special or duplicate SDL archive member: {name}")
            seen.add(name.casefold())
            total += member.size
            if total > 100_000_000 or len(seen) > 5000:
                raise BuildError("SDL archive exceeds the expected source size/member limits.")
            stream = archive.extractfile(member)
            if stream is None:
                raise BuildError(f"Cannot read SDL archive member: {name}")
            with stream:
                content = stream.read()
            if len(content) != member.size:
                raise BuildError(f"Incomplete SDL archive member: {name}")
            files["sources/" + name] = content
    for name in ("CMakeLists.txt", "LICENSE.txt", "include/SDL3/SDL_version.h"):
        if "sources/" + SDL_ROOT + "/" + name not in files:
            raise BuildError(f"SDL archive lacks required source/license: {name}")
    return files


def generated_files(repo, profile, wrapper, sources):
    files = dict(sources)
    for template, output in TEMPLATES.items():
        content = (repo / "tools/sdl-check" / template).read_text(encoding="utf-8")
        for key, value in profile.items():
            content = content.replace("@" + key.upper() + "@", str(value))
        if re.search(r"@[A-Z_]+@", content):
            raise BuildError(f"Unresolved template setting in {template}")
        files[output] = content.encode("utf-8")
    prefix = "sources/" + SDL_ROOT + "/android-project/app/src/main/java/org/libsdl/app/"
    java = {name.removeprefix(prefix): data for name, data in sources.items() if name.startswith(prefix) and name.endswith(".java")}
    if set(java) != JAVA_NAMES:
        raise BuildError("SDL archive does not contain the expected eleven Android Java glue files.")
    for name, data in java.items():
        files["app/src/main/java/org/libsdl/app/" + name] = data
    files["gradle/wrapper/gradle-wrapper.jar"] = wrapper
    files["gradle/wrapper/gradle-wrapper.properties"] = (
        "distributionBase=GRADLE_USER_HOME\ndistributionPath=wrapper/dists\n"
        f"distributionUrl=https\\://services.gradle.org/distributions/gradle-{profile['gradle_version']}-bin.zip\n"
        f"distributionSha256Sum={profile['gradle_sha256']}\n"
        "networkTimeout=10000\nvalidateDistributionUrl=true\n"
        "zipStoreBase=GRADLE_USER_HOME\nzipStorePath=wrapper/dists\n"
    ).encode("ascii")
    return files


def verify_owned(root, marker):
    if marker.get("kind") != KIND or marker.get("schema_version") != 1:
        raise BuildError("Existing SDL build directory is not recognized. Nothing overwritten.")
    for name, record in marker.get("files", {}).items():
        parts = name.split("/")
        if not parts or any(part in ("", ".", "..") for part in parts) or "\\" in name or ":" in name:
            raise BuildError("Invalid SDL source marker path.")
        path = B.managed_path(root, name)
        if not path.is_file() or B.fingerprint(path) != record:
            raise BuildError(f"Managed SDL source/project file was changed or removed: {name}. Nothing overwritten.")
    for relative in ("sources", "app/src/main/java", "gradle"):
        folder = B.managed_path(root, relative)
        if folder.exists():
            for path in folder.rglob("*"):
                B.reject_link(path)
                if path.is_file() and path.relative_to(root).as_posix() not in marker["files"]:
                    raise BuildError(f"Extra file in managed SDL source: {path}. Nothing overwritten.")


def prepare(root, files, pin):
    B.reject_link(root.parent)
    B.reject_link(root)
    marker_path = B.managed_path(root, MARKER)
    old = None
    if root.exists():
        if not marker_path.is_file():
            raise BuildError("Existing SDL build directory is unmanaged. Nothing overwritten.")
        old = B.read_json(marker_path)
        verify_owned(root, old)
        for folder in ("native-build", "app/build", "app/src/main/jniLibs"):
            directory = B.managed_path(root, folder)
            if directory.exists():
                for path in directory.rglob("*"):
                    B.reject_link(path)
    else:
        root.mkdir(parents=True)
    for name, data in files.items():
        path = B.managed_path(root, name)
        if path.exists() and (old is None or name not in old["files"]):
            raise BuildError(f"Unmanaged file occupies a generated SDL path: {name}. Nothing overwritten.")
        if not path.exists() or path.read_bytes() != data:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    if old is not None:
        for name in set(old["files"]) - set(files):
            B.managed_path(root, name).unlink()
    marker = {"schema_version": 1, "kind": KIND, "archive": pin,
              "files": {name: {"bytes": len(data), "sha256": B.sha256(data)} for name, data in sorted(files.items())}}
    marker_path.write_bytes(B.json_bytes(marker))
    return marker


def base36(number):
    result = ""
    while number:
        number, digit = divmod(number, 36)
        result = "0123456789abcdefghijklmnopqrstuvwxyz"[digit] + result
    return result or "0"


def require_cached_gradle(home, profile):
    url = f"https://services.gradle.org/distributions/gradle-{profile['gradle_version']}-bin.zip"
    key = base36(int(hashlib.md5(url.encode("utf-8"), usedforsecurity=False).hexdigest(), 16))
    folder = home / "wrapper/dists" / f"gradle-{profile['gradle_version']}-bin" / key
    marker = folder / f"gradle-{profile['gradle_version']}-bin.zip.ok"
    launcher = folder / f"gradle-{profile['gradle_version']}/lib/gradle-launcher-{profile['gradle_version']}.jar"
    # Gradle --offline does not govern wrapper distribution downloads. Refuse
    # before the wrapper unless its exact URL cache is already complete.
    if not marker.is_file() or not launcher.is_file() or launcher.stat().st_size == 0:
        raise BuildError("The exact Gradle 8.9 wrapper distribution is not already cached. Run the existing connected tools preparation first.")
    return launcher


def verify_previous(root):
    for receipt_name in ("receipt.json", "native-receipt.json"):
        path = B.managed_path(root, receipt_name)
        if not path.exists():
            continue
        receipt = B.read_json(path)
        expected = set(ARTIFACTS) if receipt_name == "receipt.json" else set(ARTIFACTS[1:])
        if receipt.get("kind") != KIND or set(receipt.get("artifacts", {})) != expected:
            raise BuildError("Existing SDL artifact receipt is not recognized. Nothing overwritten.")
        for name, record in receipt["artifacts"].items():
            if B.fingerprint(B.managed_path(root, name)) != record:
                raise BuildError(f"A recorded SDL artifact was edited: {name}. Nothing overwritten.")


def inspect_library(path, readelf, is_runtime=False):
    if is_runtime:
        return B.inspect_shared_elf(path.read_bytes())
    if B.elf_header(path.read_bytes()) != 3:
        raise BuildError(f"SDL native library must be Android x86_64 ELF64 ET_DYN: {path}")
    output = run([readelf, "--dynamic", "--notes", path], capture=True)
    needed = re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", output)
    allowed = {"libSDL3.so", "libc++_shared.so", "libc.so", "libm.so", "libdl.so", "liblog.so",
               "libandroid.so", "libGLESv1_CM.so", "libGLESv2.so", "libEGL.so", "libOpenSLES.so"}
    if not needed or set(needed) - allowed or re.search(r"\((?:RPATH|RUNPATH)\)", output):
        raise BuildError(f"Unexpected dynamic library or host RPATH in {path}: {needed}")
    if not re.search(r"Android\s+0x[0-9a-f]+\s+NT_ANDROID_TYPE_IDENT\s+description data: 17 00 00 00", output):
        raise BuildError(f"Android API23 ELF note is missing: {path}")
    if path.name == "libmain.so" and not {"libSDL3.so", "libc++_shared.so"}.issubset(needed):
        raise BuildError("The SDL check library is missing its SDL/shared-C++ dependencies.")
    return {"elf_class": 64, "machine": "x86_64", "android_api": 23, "needed": needed, "host_rpath": False}


def verify_apk(apk, root, aapt2, java, apksigner):
    with zipfile.ZipFile(apk) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise BuildError("APK contains duplicate ZIP entries.")
        # This app uses framework styles and literal labels, so AAPT2 may omit
        # a resource table when there are no app-owned resources to package.
        for name in ("AndroidManifest.xml", "classes.dex"):
            if name not in names or not archive.getinfo(name).file_size:
                raise BuildError(f"APK lacks required Android content: {name}")
        libraries = {name for name in names if name.startswith("lib/") and not name.endswith("/")}
        expected = {"lib/x86_64/" + name for name in LIBRARIES}
        if libraries != expected:
            raise BuildError(f"APK must contain exactly the three verified x86_64 native libraries: {libraries}")
        for name in LIBRARIES:
            if archive.read("lib/x86_64/" + name) != (root / JNI_ROOT / name).read_bytes():
                raise BuildError(f"Packaged native library differs from the verified CMake output: {name}")
    badging = run([aapt2, "dump", "badging", apk], capture=True)
    patterns = (rf"(?m)^package: name='{re.escape(PACKAGE)}' versionCode='1' versionName='1\.0'",
                r"(?m)^sdkVersion:'23'$", r"(?m)^targetSdkVersion:'35'$",
                rf"(?m)^launchable-activity: name='{re.escape(ACTIVITY)}'",
                r"(?m)^native-code: 'x86_64'\s*$")
    if not all(re.search(pattern, badging) for pattern in patterns):
        raise BuildError("APK package, activity, SDK or ABI metadata does not match the isolated SDL check.")
    run([java, "-jar", apksigner, "verify", "--verbose", apk])
    return {"package_name": PACKAGE, "activity": ACTIVITY, "abi": "x86_64", "min_sdk": 23,
            "target_sdk": 35, "signature_verified": True, "native_payload_matches": True}


def build(repo, args):
    profile, project_inputs, wrapper = selected_inputs(repo)
    pin, data = verified_archive(repo, args.archive_directory)
    sources = archive_files(data)
    generated = generated_files(repo, profile, wrapper, sources)
    ndk_tools = B.ndk_paths(args.ndk, B.host_tag())
    versions = B.check_tools(args.cmake, args.ninja, ndk_tools)
    env = os.environ.copy()
    if not args.native_only:
        for path, description in ((args.java, "Java"), (args.sdk / "platforms/android-35/android.jar", "Android SDK35")):
            B.require_file(path, description)
        java_version = run([args.java, "-version"], capture=True)
        if not re.search(r'version "21(?:[.\"]|$)', java_version):
            raise BuildError("Use the selected Java 21 runtime for Gradle.")
        versions["java"] = java_version.splitlines()[0]
        require_cached_gradle(args.gradle_user_home, profile)
        suffix = ".exe" if sys.platform == "win32" else ""
        aapt2 = B.require_file(args.sdk / "build-tools/34.0.0" / ("aapt2" + suffix), "SDK AAPT2")
        apksigner = B.require_file(args.sdk / "build-tools/34.0.0/lib/apksigner.jar", "SDK APK signer JAR")
        env.update(JAVA_HOME=str(args.java.parent.parent), ANDROID_HOME=str(args.sdk),
                   ANDROID_SDK_ROOT=str(args.sdk), GRADLE_USER_HOME=str(args.gradle_user_home))
    root = repo / "build/android-sdl-apk"
    if root.exists():
        verify_previous(root)
    marker = prepare(root, generated, pin)
    for name in ("receipt.json", "native-receipt.json"):
        path = B.managed_path(root, name)
        if path.exists():
            path.unlink()
    native_build = root / "native-build"
    run([args.cmake, "-S", repo / "tools/sdl-check/native", "-B", native_build, "-G", "Ninja", "-Wno-deprecated",
         "-DCMAKE_MAKE_PROGRAM=" + args.ninja.as_posix(),
         "-DCMAKE_TOOLCHAIN_FILE=" + ndk_tools["cmake_toolchain"].as_posix(),
         "-DANDROID_ABI=x86_64", "-DANDROID_PLATFORM=android-23", "-DANDROID_STL=c++_shared",
         "-DCMAKE_BUILD_TYPE=Release", "-DSDL3_SOURCE_DIR=" + (root / "sources" / SDL_ROOT).as_posix()])
    run([args.cmake, "--build", native_build, "--parallel", str(args.jobs)])
    validation = {}
    for name in LIBRARIES:
        source = ndk_tools["runtime"] if name == "libc++_shared.so" else native_build / "lib" / name
        B.require_file(source, "Compiled Android library")
        validation[name] = inspect_library(source, ndk_tools["llvm-readelf"], name == "libc++_shared.so")
        destination = B.managed_path(root, JNI_ROOT + name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
    native_receipt = {"schema_version": 1, "kind": KIND,
                      "artifacts": {name: B.fingerprint(root / name) for name in ARTIFACTS[1:]}}
    (root / "native-receipt.json").write_bytes(B.json_bytes(native_receipt))
    if args.native_only:
        print("SDL NATIVE BUILD PASS: no APK packaging or Android execution requested.", flush=True)
        return native_receipt
    run([args.java, "-classpath", root / "gradle/wrapper/gradle-wrapper.jar", "org.gradle.wrapper.GradleWrapperMain",
         "--offline", "--no-daemon", "--console=plain", "--project-dir", root,
         "--project-cache-dir", root / "gradle-cache", ":app:assembleDebug"], env=env)
    apk = B.require_file(root / APK, "SDL check APK")
    apk_validation = verify_apk(apk, root, aapt2, args.java, apksigner)
    verify_owned(root, marker)
    if any(B.fingerprint(repo / name) != record for name, record in project_inputs.items()):
        raise BuildError("A project input changed during the build. No successful APK receipt written.")
    receipt = {"schema_version": 1, "kind": KIND, "profile": dict(B.SELECTED_PROFILE), "sdl_version": SDL_VERSION,
               "package_name": PACKAGE, "activity": ACTIVITY,
               "inputs": {"project_files": project_inputs, "archive": pin, "wrapper_sha256": WRAPPER_HASH},
               "tools": versions, "validation": {"libraries": validation, "apk": apk_validation},
               "artifacts": {name: B.fingerprint(root / name) for name in ARTIFACTS}, "device_executed": False}
    (root / "receipt.json").write_bytes(B.json_bytes(receipt))
    print(f"SDL CHECK APK BUILD PASS: {apk}", flush=True)
    print("This is the isolated SDL graphics/input check, not the Wesnoth game. Next: Test-AndroidSdlCheck.ps1.", flush=True)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("ndk", "cmake", "ninja", "java", "sdk"):
        parser.add_argument("--" + option, required=True, type=Path)
    parser.add_argument("--gradle-user-home", type=Path, default=Path.home() / ".gradle")
    parser.add_argument("--archive-directory", type=Path)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--native-only", action="store_true", help="Compile the shared libraries without Gradle/APK packaging")
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.jobs <= 64:
            raise BuildError("--jobs must be between 1 and 64.")
        for name, value in vars(args).items():
            if isinstance(value, Path):
                setattr(args, name, value.resolve())
        build(REPO, args)
    except (BuildError, OSError, ValueError, UnicodeError, tarfile.TarError, zipfile.BadZipFile) as error:
        print(f"BLOCKED: {error}", file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
