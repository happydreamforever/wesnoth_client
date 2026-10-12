#!/usr/bin/env python3
"""Build the reusable Android media dependency prefix from cached source archives.

Uses the selected NDK/CMake/Ninja without downloads. Existing game source,
bootstrap and SDL APK directories are not modified.
"""

import argparse
import importlib.util
import io
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tarfile


REPO = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("wesnoth_native_bootstrap", REPO / "tools/build_android_bootstrap.py")
B = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(B)
BuildError = B.BuildError
KIND = "wesnoth-android-media"
MARKER = ".wesnoth-android-media.json"
PREFIX_STATE = ".prefix-state.json"
PROJECT_INPUTS = (
    "tools/build_android_media.py", "tools/build_android_bootstrap.py",
    "tools/android-build-profile.json", "tools/android-native-archives.json",
    "tools/native-media/build-plan.json", "tools/native-media/CMakeLists.txt", "tools/native-media/probe.cpp",
)
PACKAGES = (
    ("png", "libpng-1.6.39.tar.xz", "libpng-1.6.39"),
    ("webp", "libwebp-1.4.0.tar.gz", "libwebp-1.4.0"),
    ("ogg", "libogg-1.3.5.tar.xz", "libogg-1.3.5"),
    ("vorbis", "libvorbis-1.3.7.tar.xz", "libvorbis-1.3.7"),
    ("sdl3", "SDL3-3.4.2.tar.gz", "SDL3-3.4.2"),
    ("sdl3_image", "SDL3_image-3.4.0.tar.gz", "SDL3_image-3.4.0"),
    ("sdl3_mixer", "SDL3_mixer-3.2.0.tar.gz", "SDL3_mixer-3.2.0"),
)
ARTIFACTS = (
    "prefix/bin/wesnoth-media-check", "prefix/lib/libSDL3.so", "prefix/lib/libSDL3_image.so",
    "prefix/lib/libSDL3_mixer.so", "prefix/lib/libc++_shared.so",
)
REQUIRED_HEADERS = (
    "png.h", "webp/decode.h", "webp/encode.h", "ogg/ogg.h", "vorbis/vorbisfile.h", "vorbis/vorbisenc.h",
    "SDL3/SDL.h", "SDL3_image/SDL_image.h", "SDL3_mixer/SDL_mixer.h",
)
REQUIRED_ARCHIVES = (
    "libpng16.a", "libwebp.a", "libwebpdemux.a", "libwebpmux.a", "libsharpyuv.a",
    "libogg.a", "libvorbis.a", "libvorbisenc.a", "libvorbisfile.a",
)
KNOWN_XCODE_LINKS = {
    "Xcode/macOS/SDL3.framework/Versions/Current": "A",
    "Xcode/macOS/SDL3.framework/Resources": "Versions/Current/Resources",
    "Xcode/macOS/SDL3.framework/SDL3.tbd": "Versions/Current/SDL3.tbd",
    "Xcode/macOS/SDL3.framework/Headers": "Versions/Current/Headers",
}


def run(arguments, env, capture=False):
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


def safe_name(name):
    parts = name.split("/")
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"{kind}{i}" for kind in ("COM", "LPT") for i in range(1, 10)}
    if (any(part in ("", ".", "..") or part.endswith((".", " ")) for part in parts)
            or "\\" in name or ":" in name
            or any(part.split(".")[0].upper() in reserved for part in parts)):
        raise BuildError(f"Unsafe media archive/receipt path: {name}")
    return parts


def selected_inputs(repo):
    profile = B.read_json(repo / "tools/android-build-profile.json")
    if profile.get("schema_version") != 1 or any(profile.get(key) != value for key, value in B.SELECTED_PROFILE.items()):
        raise BuildError("The media build requires the selected Wesnoth/NDK/x86_64/API23 profile.")
    project_inputs = {name: B.fingerprint(B.require_file(repo / name, "Media build input")) for name in PROJECT_INPUTS}
    plan = B.read_json(repo / "tools/native-media/build-plan.json")
    if plan.get("schema_version") != 1 or not isinstance(plan.get("packages"), list):
        raise BuildError("Unrecognized native media build plan.")
    packages = plan["packages"]
    if [(p.get("name"), p.get("archive"), p.get("source_dir")) for p in packages] != list(PACKAGES):
        raise BuildError("The media plan must use the seven selected source archives in dependency order.")
    for package in packages:
        if (not isinstance(package.get("cmake_options"), list)
                or not all(isinstance(option, str) and re.match(r"^[A-Za-z0-9_]+(?::(?:BOOL|STRING|PATH|FILEPATH))?=", option)
                           for option in package["cmake_options"])
                or not isinstance(package.get("license_files"), list) or not package["license_files"]):
            raise BuildError(f"Invalid options/license declaration for {package['name']}")
        for name in package["license_files"]:
            safe_name(name)
    manifest = B.read_json(repo / "tools/android-native-archives.json")
    pins = {}
    for _, archive, _ in PACKAGES:
        entries = [entry for entry in manifest.get("archives", []) if entry.get("name") == archive]
        if len(entries) != 1:
            raise BuildError(f"The native archive manifest must identify {archive} exactly once.")
        entry = entries[0]
        if (type(entry.get("bytes")) is not int or not 0 < entry["bytes"] < 100_000_000
                or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", "")))):
            raise BuildError(f"Invalid archive pin for {archive}")
        pins[archive] = {key: entry[key] for key in ("bytes", "sha256")}
    return dict(B.SELECTED_PROFILE), project_inputs, packages, pins


def archive_data(repo, name, pin, directory=None):
    folders = [directory] if directory is not None else [repo / relative for relative in (
        "packaging/android/download", "packaging/android/downloads",
        "engine/packaging/android/download", "engine/packaging/android/downloads",
    )]
    if directory is None and sys.platform == "win32":
        folders.append(Path("D:/Test/msys64/tmp/android-dl"))
    for folder in folders:
        path = folder / name
        if path.exists():
            data = B.require_file(path, "Cached media source archive").read_bytes()
            if len(data) != pin["bytes"] or B.sha256(data) != pin["sha256"]:
                raise BuildError(f"Cached source archive does not match its size/hash: {path}")
            print(f"Verified offline archive: {path}", flush=True)
            return data
    raise BuildError(f"Missing {name}. Supply --archive-directory with the existing cache; this build never downloads.")


def archive_files(data, source_dir):
    files, seen, skipped_links = {}, set(), set()
    total = 0
    omit_xcode = source_dir in ("SDL3_image-3.4.0", "SDL3_mixer-3.2.0")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        for member in archive:
            name = member.name.rstrip("/") if member.isdir() else member.name
            parts = safe_name(name)
            if parts[0] != source_dir:
                raise BuildError(f"Unexpected root in {source_dir} source archive: {name}")
            if member.isdir():
                continue
            if len(parts) < 2 or name.casefold() in seen:
                raise BuildError(f"Duplicate or unexpected media archive member: {name}")
            seen.add(name.casefold())
            relative = "/".join(parts[1:])
            if member.issym() and omit_xcode and KNOWN_XCODE_LINKS.get(relative) == member.linkname:
                skipped_links.add(relative)
                continue
            if not member.isfile():
                raise BuildError(f"Unexpected link/special file in media source archive: {name}")
            if omit_xcode and parts[1] == "Xcode":
                continue
            total += member.size
            if total > 150_000_000 or len(seen) > 10000:
                raise BuildError(f"Media source archive exceeds expected limits: {source_dir}")
            stream = archive.extractfile(member)
            if stream is None:
                raise BuildError(f"Cannot read media archive member: {name}")
            with stream:
                content = stream.read()
            if len(content) != member.size:
                raise BuildError(f"Incomplete media archive member: {name}")
            files["sources/" + name] = content
    if omit_xcode and skipped_links != set(KNOWN_XCODE_LINKS):
        raise BuildError(f"The unused Xcode links differ from the inspected {source_dir} archive.")
    if "sources/" + source_dir + "/CMakeLists.txt" not in files:
        raise BuildError(f"Media archive lacks its CMake recipe: {source_dir}")
    return files


def inventory(root, relative="prefix"):
    directory = B.managed_path(root, relative)
    if not directory.exists():
        return {}
    records, seen = {}, set()
    for path in sorted(directory.rglob("*")):
        B.reject_link(path)
        name = path.relative_to(root).as_posix()
        if relative == "prefix":
            if name.casefold() in seen:
                raise BuildError(f"Installed media paths collide on Windows: {name}")
            seen.add(name.casefold())
        if path.is_file():
            safe_name(name)
            records[name] = B.fingerprint(path)
    return records


def verify_sources(root, marker):
    if marker.get("schema_version") != 1 or marker.get("kind") != KIND or not isinstance(marker.get("source_files"), dict):
        raise BuildError("Existing media source marker is not recognized. Nothing overwritten.")
    if inventory(root, "sources") != marker["source_files"]:
        raise BuildError("Managed media source files were changed, added or removed. Nothing overwritten.")


def prepare_sources(root, source_files, profile, pins):
    B.reject_link(root.parent)
    B.reject_link(root)
    marker = {"schema_version": 1, "kind": KIND, "profile": profile, "archives": pins,
              "source_files": {name: {"bytes": len(data), "sha256": B.sha256(data)} for name, data in sorted(source_files.items())}}
    path = B.managed_path(root, MARKER)
    if root.exists():
        if not path.is_file() or B.read_json(path) != marker:
            raise BuildError("Existing media directory is unmanaged or uses different source archives. Nothing overwritten.")
        verify_sources(root, marker)
        for relative in ("build", "prefix"):
            directory = B.managed_path(root, relative)
            if directory.exists():
                for child in directory.rglob("*"):
                    aliases = {"build/png/libpng.a": "libpng16.a", "build/png/libpng.pc": "libpng16.pc",
                               "build/png/libpng-config": "libpng16-config"}
                    expected = aliases.get(child.relative_to(root).as_posix())
                    if child.is_symlink() and expected is not None and os.readlink(child) == expected:
                        target = child.parent / expected
                        B.reject_link(target)
                        B.require_file(target, "Known private libpng build alias target")
                        continue
                    B.reject_link(child)
        return marker
    root.mkdir(parents=True)
    for name, content in source_files.items():
        path = B.managed_path(root, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(content)
    (root / MARKER).write_bytes(B.json_bytes(marker))
    return marker


def verify_prefix(root):
    records = inventory(root)
    state_path = B.managed_path(root, PREFIX_STATE)
    if state_path.exists():
        state = B.read_json(state_path)
        if state.get("kind") != KIND or state.get("prefix_files") != records:
            raise BuildError("Installed media prefix changed or a previous install stopped before completion. Nothing overwritten.")
    elif records:
        raise BuildError("Existing media prefix has no owned file record. Nothing overwritten.")
    receipt_path = B.managed_path(root, "receipt.json")
    if receipt_path.exists():
        receipt = B.read_json(receipt_path)
        if (receipt.get("kind") != KIND or receipt.get("prefix_files") != records
                or set(receipt.get("artifacts", {})) != set(ARTIFACTS)):
            raise BuildError("The previous media receipt differs from its installed prefix. Nothing overwritten.")
        for name, record in receipt["artifacts"].items():
            if records.get(name) != record:
                raise BuildError(f"Recorded media artifact changed: {name}. Nothing overwritten.")


def save_prefix_state(root):
    records = inventory(root)
    (root / PREFIX_STATE).write_bytes(B.json_bytes({"kind": KIND, "schema_version": 1, "prefix_files": records}))
    return records


def normalize_prefix(prefix):
    # libpng's CMake recipe creates aliases on Unix and copies on native
    # Windows. Materialize only the two inspected, local file aliases.
    aliases = {"lib/libpng.a": "libpng16.a", "lib/pkgconfig/libpng.pc": "libpng16.pc"}
    for relative, target_name in aliases.items():
        path = prefix / relative
        if path.is_symlink():
            if os.readlink(path) != target_name:
                raise BuildError(f"Unexpected installed libpng alias: {path}")
            target = path.parent / target_name
            B.reject_link(target)
            content = B.require_file(target, "libpng alias target").read_bytes()
            path.unlink()
            path.write_bytes(content)
    # These shell helpers contain host-specific paths, are not Android
    # executables and are not installed by libpng on native Windows.
    for name in ("libpng-config", "libpng16-config"):
        path = prefix / "bin" / name
        if path.is_symlink():
            if name != "libpng-config" or os.readlink(path) != "libpng16-config":
                raise BuildError(f"Unexpected installed libpng helper link: {path}")
            path.unlink()
        elif path.exists():
            if not path.is_file():
                raise BuildError(f"Unexpected libpng helper path: {path}")
            path.unlink()
    # Keep .pc files relocatable for the eventual game build. CMake exports
    # retain their own generated _IMPORT_PREFIX/PACKAGE_PREFIX_DIR mechanism.
    for path in prefix.rglob("*.pc"):
        B.reject_link(path)
        content = path.read_text(encoding="utf-8")
        content = re.sub(r"(?m)^prefix=.*$", "prefix=${pcfiledir}/../..", content)
        content = content.replace(prefix.as_posix(), "${prefix}").replace(str(prefix), "${prefix}")
        path.write_text(content, encoding="utf-8", newline="\n")


def isolated_environment(prefix):
    env = os.environ.copy()
    for key in ("CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH",
                "LIBRARY_PATH", "LIB", "INCLUDE", "CMAKE_PREFIX_PATH", "CMAKE_TOOLCHAIN_FILE", "DESTDIR"):
        env.pop(key, None)
    env["PKG_CONFIG_PATH"] = ""
    env["PKG_CONFIG_LIBDIR"] = str(prefix / "lib/pkgconfig")
    return env


def cmake_arguments(args, ndk_tools, prefix, source, output):
    return [args.cmake, "-S", source, "-B", output, "-G", "Ninja", "-Wno-deprecated",
            "-DCMAKE_MAKE_PROGRAM=" + args.ninja.as_posix(),
            "-DCMAKE_TOOLCHAIN_FILE=" + ndk_tools["cmake_toolchain"].as_posix(),
            "-DANDROID_ABI=x86_64", "-DANDROID_PLATFORM=android-23", "-DANDROID_STL=c++_shared",
            "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_POSITION_INDEPENDENT_CODE=ON",
            "-DCMAKE_POLICY_VERSION_MINIMUM=3.5", "-DCMAKE_INSTALL_LIBDIR=lib",
            "-DCMAKE_INSTALL_PREFIX=" + prefix.as_posix(), "-DCMAKE_PREFIX_PATH=" + prefix.as_posix(),
            "-DCMAKE_FIND_ROOT_PATH=" + prefix.as_posix(), "-DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=ONLY",
            "-DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=ONLY", "-DCMAKE_FIND_ROOT_PATH_MODE_PACKAGE=ONLY",
            "-DCMAKE_FIND_ROOT_PATH_MODE_PROGRAM=NEVER", "-DCMAKE_DISABLE_FIND_PACKAGE_PkgConfig=ON",
            "-DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF", "-DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=OFF",
            "-DFETCHCONTENT_FULLY_DISCONNECTED=ON", "-DFETCHCONTENT_UPDATES_DISCONNECTED=ON"]


def inspect_static_archive(path):
    data = path.read_bytes()
    if not data.startswith(b"!<arch>\n"):
        raise BuildError(f"Media static library is not a regular ar archive: {path}")
    position, count = 8, 0
    while position < len(data):
        header = data[position:position + 60]
        if len(header) != 60 or header[58:60] != b"`\n":
            raise BuildError(f"Malformed media static archive header: {path}")
        size = int(header[48:58])
        position += 60
        content = data[position:position + size]
        if size < 0 or len(content) != size:
            raise BuildError(f"Truncated media static archive: {path}")
        if header[:16].strip() not in (b"/", b"//", b"/SYM64/"):
            if B.elf_header(content) != 1:
                raise BuildError(f"Static media library contains a non-x86_64 relocatable object: {path}")
            count += 1
        position += size + size % 2
    if position != len(data) or not count:
        raise BuildError(f"Static media archive is empty or has invalid padding: {path}")
    return {"machine": "x86_64", "elf_object_count": count}


def inspect_dynamic(path, readelf, env, executable=False, runtime=False):
    if runtime:
        return B.inspect_shared_elf(path.read_bytes())
    if B.elf_header(path.read_bytes()) != 3:
        raise BuildError(f"Media binary must be x86_64 ELF64 ET_DYN: {path}")
    output = run([readelf, "--dynamic", "--notes", "--program-headers", path], env, capture=True)
    needed = re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", output)
    allowed = {"libSDL3.so", "libSDL3_image.so", "libSDL3_mixer.so", "libc++_shared.so", "libc.so", "libm.so",
               "libdl.so", "liblog.so", "libandroid.so", "libGLESv1_CM.so", "libGLESv2.so", "libEGL.so",
               "libOpenSLES.so", "libz.so"}
    if not needed or set(needed) - allowed or re.search(r"\((?:RPATH|RUNPATH)\)", output):
        raise BuildError(f"Media binary has an unexpected dynamic dependency or host RPATH: {path}; {needed}")
    if not re.search(r"Android\s+0x[0-9a-f]+\s+NT_ANDROID_TYPE_IDENT\s+description data: 17 00 00 00", output):
        raise BuildError(f"Android API23 note is missing from media binary: {path}")
    if executable and ("[Requesting program interpreter: /system/bin/linker64]" not in output
                       or not {"libSDL3.so", "libSDL3_image.so", "libSDL3_mixer.so", "libc++_shared.so"}.issubset(needed)):
        raise BuildError("The media executable does not have the expected Android loader/dependencies.")
    return {"machine": "x86_64", "android_api": 23, "needed": needed, "host_rpath": False}


def validate_prefix(root, ndk_tools, env):
    prefix = root / "prefix"
    for relative in REQUIRED_HEADERS:
        B.require_file(prefix / "include" / relative, "Installed media header")
    static = {}
    for name in REQUIRED_ARCHIVES:
        B.require_file(prefix / "lib" / name, "Installed media static library")
    for path in sorted(prefix.rglob("*.a")):
        B.reject_link(path)
        static[path.relative_to(prefix).as_posix()] = inspect_static_archive(path)
    dynamic = {}
    for name in ARTIFACTS:
        path = B.require_file(root / name, "Installed media runtime artifact")
        dynamic[name] = inspect_dynamic(path, ndk_tools["llvm-readelf"], env,
                                        executable=name == ARTIFACTS[0], runtime=name.endswith("libc++_shared.so"))
    files = inventory(root)
    for name, record in files.items():
        if record["bytes"] == 0:
            raise BuildError(f"Installed media prefix contains an empty file: {name}")
        if name.endswith((".pc", ".cmake")):
            text = (root / name).read_text(encoding="utf-8")
            ndk_root = ndk_tools["cmake_toolchain"].parents[2]
            # Cached FILEPATH options can also expand a bare library name
            # relative to the repository, outside this particular build tree.
            if any(path.as_posix() in text or str(path) in text for path in (root.parents[1], ndk_root)):
                raise BuildError(f"Installed package metadata retains a build-host path: {name}")
    shared = {name for name in files if name.endswith(".so") or ".so." in name}
    if shared != set(ARTIFACTS[1:]):
        raise BuildError(f"Unexpected shared libraries in media prefix: {sorted(shared)}")
    return {"static_libraries": static, "runtime": dynamic}, files


def build(repo, args):
    profile, project_inputs, packages, pins = selected_inputs(repo)
    source_files = {}
    for package in packages:
        data = archive_data(repo, package["archive"], pins[package["archive"]], args.archive_directory)
        source_files.update(archive_files(data, package["source_dir"]))
        for license_file in package["license_files"]:
            key = "sources/" + package["source_dir"] + "/" + license_file
            if not source_files.get(key):
                raise BuildError(f"Source archive lacks its declared license input: {key}")
    ndk_tools = B.ndk_paths(args.ndk, B.host_tag())
    versions = B.check_tools(args.cmake, args.ninja, ndk_tools)
    root = repo / "build/android-media"
    marker = prepare_sources(root, source_files, profile, pins)
    verify_prefix(root)
    receipt_path = B.managed_path(root, "receipt.json")
    if receipt_path.exists():
        receipt_path.unlink()
    prefix = root / "prefix"
    prefix.mkdir(exist_ok=True)
    env = isolated_environment(prefix)
    save_prefix_state(root)
    for package in packages:
        name = package["name"]
        print(f"MEDIA PACKAGE: {name}", flush=True)
        source = root / "sources" / package["source_dir"]
        output = root / "build" / name
        options = ["-D" + option.replace("@PREFIX@", prefix.as_posix()) for option in package["cmake_options"]]
        run(cmake_arguments(args, ndk_tools, prefix, source, output) + options, env)
        run([args.cmake, "--build", output, "--parallel", str(args.jobs)], env)
        run([args.cmake, "--install", output], env)
        normalize_prefix(prefix)
        for license_file in package["license_files"]:
            # Upstream SDL installs mixed-case package directories. Keep our
            # additional notices separate so Windows and Linux have one layout.
            destination = prefix / "share/licenses/wesnoth-media" / name / license_file
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((source / license_file).read_bytes())
        save_prefix_state(root)
    runtime = prefix / "lib/libc++_shared.so"
    runtime.write_bytes(ndk_tools["runtime"].read_bytes())
    save_prefix_state(root)
    probe_build = root / "build/probe"
    run(cmake_arguments(args, ndk_tools, prefix, repo / "tools/native-media", probe_build)
        + ["-DMEDIA_PREFIX=" + prefix.as_posix()], env)
    run([args.cmake, "--build", probe_build, "--parallel", str(args.jobs)], env)
    run([args.cmake, "--install", probe_build], env)
    normalize_prefix(prefix)
    save_prefix_state(root)
    validation, prefix_files = validate_prefix(root, ndk_tools, env)
    verify_sources(root, marker)
    if any(B.fingerprint(repo / name) != record for name, record in project_inputs.items()):
        raise BuildError("A media build input changed during compilation. No success receipt written.")
    receipt = {"schema_version": 1, "kind": KIND, "profile": profile,
               "inputs": {"project_files": project_inputs, "archives": pins}, "tools": versions,
               "artifacts": {name: prefix_files[name] for name in ARTIFACTS}, "prefix_files": prefix_files,
               "validation": validation, "device_executed": False}
    with receipt_path.open("xb") as stream:
        stream.write(B.json_bytes(receipt))
    print(f"ANDROID MEDIA BUILD PASS: {prefix}", flush=True)
    print(f"Receipt: {receipt_path}", flush=True)
    print("Reusable media dependencies and codec probe built; no APK or Android execution was performed.", flush=True)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("ndk", "cmake", "ninja"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--archive-directory", type=Path)
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.jobs <= 64:
            raise BuildError("--jobs must be between 1 and 64.")
        for name, value in vars(args).items():
            if isinstance(value, Path):
                setattr(args, name, value.resolve())
        build(REPO, args)
    except (BuildError, OSError, ValueError, UnicodeError, tarfile.TarError, struct.error) as error:
        print(f"BLOCKED: {error}", file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
