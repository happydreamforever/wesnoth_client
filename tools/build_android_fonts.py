#!/usr/bin/env python3
"""Build reusable Android FreeType/HarfBuzz libraries and a font probe offline.

The probe reads fonts only when it runs on the selected Android device. This
builder preserves the completed media, SDL APK and game-source checkpoints.
"""

import argparse
import importlib.util
from pathlib import Path
import re
import struct
import sys
import tarfile


REPO = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("wesnoth_font_media_helpers", REPO / "tools/build_android_media.py")
M = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(M)
B = M.B
BuildError = B.BuildError
KIND = "wesnoth-android-fonts"
MARKER = ".wesnoth-android-fonts.json"
PREFIX_STATE = ".prefix-state.json"
PROJECT_INPUTS = (
    "tools/build_android_fonts.py", "tools/build_android_media.py", "tools/build_android_bootstrap.py",
    "tools/android-build-profile.json", "tools/android-native-archives.json",
    "tools/native-fonts/build-plan.json", "tools/native-fonts/CMakeLists.txt",
    "tools/native-fonts/probe.cpp", "tools/native-fonts/assets.json",
)
PACKAGES = (
    ("freetype", "freetype-2.13.0.tar.xz", "freetype-2.13.0"),
    ("harfbuzz", "harfbuzz-7.1.0.tar.xz", "harfbuzz-7.1.0"),
)
ARTIFACTS = ("prefix/bin/wesnoth-fonts-check", "prefix/lib/libc++_shared.so")
REQUIRED_ARCHIVES = ("libfreetype.a", "libharfbuzz.a")
REQUIRED_HEADERS = (
    "freetype2/ft2build.h", "freetype2/freetype/freetype.h",
    "harfbuzz/hb.h", "harfbuzz/hb-ft.h", "harfbuzz/hb-ot.h",
)


def selected_inputs(repo):
    profile = B.read_json(repo / "tools/android-build-profile.json")
    if profile.get("schema_version") != 1 or any(profile.get(key) != value for key, value in B.SELECTED_PROFILE.items()):
        raise BuildError("The font build requires the selected Wesnoth/NDK/x86_64/API23 profile.")
    inputs = {name: B.fingerprint(B.require_file(repo / name, "Font build input")) for name in PROJECT_INPUTS}
    plan = B.read_json(repo / "tools/native-fonts/build-plan.json")
    if plan.get("schema_version") != 1 or not isinstance(plan.get("packages"), list):
        raise BuildError("Unrecognized native font build plan.")
    packages = plan["packages"]
    if [(p.get("name"), p.get("archive"), p.get("source_dir")) for p in packages] != list(PACKAGES):
        raise BuildError("The font plan must build the selected FreeType and HarfBuzz archives in dependency order.")
    for package in packages:
        if (not isinstance(package.get("cmake_options"), list)
                or not all(isinstance(option, str) and re.match(r"^[A-Za-z0-9_]+(?::(?:BOOL|STRING|PATH|FILEPATH))?=", option)
                           for option in package["cmake_options"])
                or not isinstance(package.get("license_files"), list) or not package["license_files"]):
            raise BuildError(f"Invalid options/license declaration for {package['name']}")
        for name in package["license_files"]:
            M.safe_name(name)
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
    return dict(B.SELECTED_PROFILE), inputs, packages, pins


def verify_sources(root, marker):
    if (marker.get("schema_version") != 1 or marker.get("kind") != KIND
            or not isinstance(marker.get("source_files"), dict)):
        raise BuildError("Existing font source marker is not recognized. Nothing overwritten.")
    if M.inventory(root, "sources") != marker["source_files"]:
        raise BuildError("Managed font source files were changed, added or removed. Nothing overwritten.")


def prepare_sources(root, source_files, profile, pins):
    B.reject_link(root.parent)
    B.reject_link(root)
    marker = {"schema_version": 1, "kind": KIND, "profile": profile, "archives": pins,
              "source_files": {name: {"bytes": len(data), "sha256": B.sha256(data)}
                               for name, data in sorted(source_files.items())}}
    marker_path = B.managed_path(root, MARKER)
    if root.exists():
        if not marker_path.is_file() or B.read_json(marker_path) != marker:
            raise BuildError("Existing font directory is unmanaged or uses different source archives. Nothing overwritten.")
        verify_sources(root, marker)
        for relative in ("build", "prefix"):
            directory = B.managed_path(root, relative)
            if directory.exists():
                for child in directory.rglob("*"):
                    B.reject_link(child)
        return marker
    root.mkdir(parents=True)
    for name, content in source_files.items():
        path = B.managed_path(root, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(content)
    with marker_path.open("xb") as stream:
        stream.write(B.json_bytes(marker))
    return marker


def verify_prefix(root):
    records = M.inventory(root)
    state_path = B.managed_path(root, PREFIX_STATE)
    if state_path.exists():
        state = B.read_json(state_path)
        if state.get("schema_version") != 1 or state.get("kind") != KIND or state.get("prefix_files") != records:
            raise BuildError("Installed font prefix changed or an earlier install stopped before completion. Nothing overwritten.")
    elif records:
        raise BuildError("Existing font prefix has no owned file record. Nothing overwritten.")
    receipt_path = B.managed_path(root, "receipt.json")
    if receipt_path.exists():
        receipt = B.read_json(receipt_path)
        if (receipt.get("schema_version") != 1 or receipt.get("kind") != KIND
                or receipt.get("profile") != B.SELECTED_PROFILE or receipt.get("prefix_files") != records
                or set(receipt.get("artifacts", {})) != set(ARTIFACTS)):
            raise BuildError("The previous font receipt differs from its installed prefix. Nothing overwritten.")
        for name, record in receipt["artifacts"].items():
            if records.get(name) != record:
                raise BuildError(f"Recorded font artifact changed: {name}. Nothing overwritten.")


def save_prefix_state(root):
    records = M.inventory(root)
    (root / PREFIX_STATE).write_bytes(B.json_bytes({"kind": KIND, "schema_version": 1, "prefix_files": records}))
    return records


def normalize_prefix(prefix):
    for path in prefix.rglob("*.pc"):
        B.reject_link(path)
        content = path.read_text(encoding="utf-8")
        content = re.sub(r"(?m)^prefix=.*$", "prefix=${pcfiledir}/../..", content)
        content = content.replace(prefix.as_posix(), "${prefix}").replace(str(prefix), "${prefix}")
        path.write_text(content, encoding="utf-8", newline="\n")
    # HarfBuzz 7.1's generated export stores FindFreetype's absolute archive
    # path. Its export computes _IMPORT_PREFIX from its own installed location.
    # Rewrite only that inspected dependency, not arbitrary host search paths.
    export = prefix / "lib/cmake/harfbuzz/harfbuzzConfig.cmake"
    if export.exists():
        B.reject_link(export)
        content = export.read_text(encoding="utf-8")
        absolute = (prefix / "lib/libfreetype.a").as_posix()
        if absolute in content:
            if ('get_filename_component(_IMPORT_PREFIX' not in content
                    or 'set_target_properties(harfbuzz::harfbuzz PROPERTIES' not in content):
                raise BuildError("HarfBuzz's CMake export changed; its FreeType dependency cannot be safely relocated.")
            content = content.replace(absolute, "${_IMPORT_PREFIX}/lib/libfreetype.a")
            export.write_text(content, encoding="utf-8", newline="\n")


def inspect_probe(path):
    result = B.inspect_shared_elf(path.read_bytes())
    if (result["interpreter"] != "/system/bin/linker64" or 23 not in result["android_api_notes"]
            or not {"libc++_shared.so", "libc.so"}.issubset(result["needed"])):
        raise BuildError("Font probe must identify Android linker64, API23 and the selected shared C++ runtime.")
    return result


def validate_prefix(root, ndk_tools):
    prefix = root / "prefix"
    for relative in REQUIRED_HEADERS:
        B.require_file(prefix / "include" / relative, "Installed font header")
    static = {}
    for name in REQUIRED_ARCHIVES:
        B.require_file(prefix / "lib" / name, "Installed font static library")
    for path in sorted(prefix.rglob("*.a")):
        B.reject_link(path)
        static[path.relative_to(prefix).as_posix()] = M.inspect_static_archive(path)
    if set(static) != {"lib/" + name for name in REQUIRED_ARCHIVES}:
        raise BuildError("The font prefix contains unexpected static libraries.")
    probe = B.require_file(root / ARTIFACTS[0], "Installed font probe")
    runtime = B.require_file(root / ARTIFACTS[1], "Installed shared C++ runtime")
    validation = {"static_libraries": static, "runtime": {
        ARTIFACTS[0]: inspect_probe(probe), ARTIFACTS[1]: B.inspect_shared_elf(runtime.read_bytes()),
    }}
    records = M.inventory(root)
    ndk_root = ndk_tools["cmake_toolchain"].parents[2]
    for name, record in records.items():
        if record["bytes"] == 0:
            raise BuildError(f"Installed font prefix contains an empty file: {name}")
        if name.endswith((".pc", ".cmake")):
            text = (root / name).read_text(encoding="utf-8")
            if any(path.as_posix() in text or str(path) in text for path in (root.parents[1], ndk_root)):
                raise BuildError(f"Installed font metadata retains a build-host path: {name}")
    shared = {name for name in records if name.endswith(".so") or ".so." in name}
    if shared != {ARTIFACTS[1]}:
        raise BuildError(f"Unexpected shared libraries in font prefix: {sorted(shared)}")
    return validation, records


def build(repo, args):
    profile, project_inputs, packages, pins = selected_inputs(repo)
    source_files = {}
    for package in packages:
        data = M.archive_data(repo, package["archive"], pins[package["archive"]], args.archive_directory)
        source_files.update(M.archive_files(data, package["source_dir"]))
        for license_file in package["license_files"]:
            if not source_files.get("sources/" + package["source_dir"] + "/" + license_file):
                raise BuildError(f"Source archive lacks its declared license: {package['name']}/{license_file}")
    ndk_tools = B.ndk_paths(args.ndk, B.host_tag())
    versions = B.check_tools(args.cmake, args.ninja, ndk_tools)
    root = repo / "build/android-fonts"
    marker = prepare_sources(root, source_files, profile, pins)
    verify_prefix(root)
    receipt_path = B.managed_path(root, "receipt.json")
    if receipt_path.exists():
        receipt_path.unlink()
    prefix = root / "prefix"
    prefix.mkdir(exist_ok=True)
    env = M.isolated_environment(prefix)
    save_prefix_state(root)
    for package in packages:
        name = package["name"]
        print(f"FONT PACKAGE: {name}", flush=True)
        source = root / "sources" / package["source_dir"]
        output = root / "build" / name
        options = ["-D" + option.replace("@PREFIX@", prefix.as_posix()) for option in package["cmake_options"]]
        M.run(M.cmake_arguments(args, ndk_tools, prefix, source, output) + options, env)
        M.run([args.cmake, "--build", output, "--parallel", str(args.jobs)], env)
        M.run([args.cmake, "--install", output], env)
        normalize_prefix(prefix)
        for license_file in package["license_files"]:
            destination = prefix / "share/licenses/wesnoth-fonts" / name / license_file
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((source / license_file).read_bytes())
        save_prefix_state(root)
    (prefix / "lib/libc++_shared.so").write_bytes(ndk_tools["runtime"].read_bytes())
    save_prefix_state(root)
    probe_build = root / "build/probe"
    M.run(M.cmake_arguments(args, ndk_tools, prefix, repo / "tools/native-fonts", probe_build)
          + ["-DFONTS_PREFIX=" + prefix.as_posix()], env)
    M.run([args.cmake, "--build", probe_build, "--parallel", str(args.jobs)], env)
    M.run([args.cmake, "--install", probe_build], env)
    normalize_prefix(prefix)
    save_prefix_state(root)
    validation, prefix_files = validate_prefix(root, ndk_tools)
    verify_sources(root, marker)
    if any(B.fingerprint(repo / name) != record for name, record in project_inputs.items()):
        raise BuildError("A font build input changed during compilation. No success receipt written.")
    receipt = {"schema_version": 1, "kind": KIND, "profile": profile,
               "inputs": {"project_files": project_inputs, "archives": pins}, "tools": versions,
               "artifacts": {name: prefix_files[name] for name in ARTIFACTS}, "prefix_files": prefix_files,
               "validation": validation, "device_executed": False}
    with receipt_path.open("xb") as stream:
        stream.write(B.json_bytes(receipt))
    print(f"ANDROID FONTS BUILD PASS: {prefix}", flush=True)
    print(f"Receipt: {receipt_path}", flush=True)
    print("Font libraries and probe built; game fonts and Android execution were not accessed by this build.", flush=True)
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
