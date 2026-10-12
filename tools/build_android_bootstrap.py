#!/usr/bin/env python3
"""Build a small real Android dependency and executable from offline inputs.

Uses native Windows Python/CMake/Ninja or their Linux counterparts with NDK r26d.
This is a native compiler/dependency checkpoint, not the Wesnoth client APK.
"""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import struct
import subprocess
import sys
import tarfile


KIND = "wesnoth-android-native-bootstrap"
MARKER = ".wesnoth-native-bootstrap.json"
ARCHIVE_NAME = "bzip2-1.0.8.tar.gz"
SOURCE_ROOT = "bzip2-1.0.8"
SELECTED_PROFILE = {"wesnoth_version": "1.19.26", "abi": "x86_64", "min_sdk": 23, "ndk_version": "26.3.11579264"}
PROJECT_INPUTS = (
    "tools/build_android_bootstrap.py", "tools/android-build-profile.json",
    "tools/android-native-archives.json", "tools/native-bootstrap/CMakeLists.txt",
    "tools/native-bootstrap/probe.cpp",
)
ARTIFACTS = ("out/bin/wesnoth-native-check", "out/bin/libc++_shared.so", "out/lib/libbz2.a")


class BuildError(Exception):
    """An input, existing work directory, tool or generated artifact is invalid."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def fingerprint(path):
    data = path.read_bytes()
    return {"bytes": len(data), "sha256": sha256(data)}


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BuildError(f"Cannot read {path}: {error}") from error


def reject_link(path):
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise BuildError(f"A managed build file/directory is a symbolic link or junction: {path}")


def managed_path(root, relative):
    path = root
    for part in PurePosixPath(relative).parts:
        path /= part
        reject_link(path)
    return path


def require_file(path, description):
    if not path.is_file() or path.stat().st_size == 0:
        raise BuildError(f"{description} is missing or empty: {path}")
    return path


def profile_and_inputs(repo):
    profile = read_json(repo / "tools/android-build-profile.json")
    if profile.get("schema_version") != 1 or any(profile.get(k) != v for k, v in SELECTED_PROFILE.items()):
        raise BuildError("The bootstrap requires the selected 1.19.26 / x86_64 / API23 / NDK26.3 profile.")
    inputs = {}
    for name in PROJECT_INPUTS:
        inputs[name] = fingerprint(require_file(repo / name, "Native bootstrap input"))
    return dict(SELECTED_PROFILE), inputs


def archive_pin(repo):
    manifest = read_json(repo / "tools/android-native-archives.json")
    entries = [entry for entry in manifest.get("archives", []) if entry.get("name") == ARCHIVE_NAME]
    if len(entries) != 1:
        raise BuildError("The native archive manifest must identify exactly one bzip2 1.0.8 archive.")
    entry = entries[0]
    if (type(entry.get("bytes")) is not int or not 0 < entry["bytes"] <= 2_000_000
            or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", "")))):
        raise BuildError("Invalid bzip2 archive size/checksum declaration.")
    return {key: entry[key] for key in ("name", "bytes", "sha256")}


def find_archive(repo, pin, explicit_directory=None):
    roots = ([explicit_directory] if explicit_directory is not None else [
        repo / "packaging/android/download", repo / "packaging/android/downloads",
        repo / "engine/packaging/android/download", repo / "engine/packaging/android/downloads",
    ])
    if explicit_directory is None and sys.platform == "win32":
        roots.append(Path("D:/Test/msys64/tmp/android-dl"))
    for root in roots:
        candidate = root / pin["name"]
        if not candidate.exists():
            continue
        require_file(candidate, "bzip2 archive")
        if candidate.stat().st_size != pin["bytes"]:
            raise BuildError(f"bzip2 archive size differs from its recorded pin: {candidate}. Nothing overwritten.")
        data = candidate.read_bytes()
        if sha256(data) != pin["sha256"]:
            raise BuildError(f"bzip2 archive SHA256 differs from its recorded pin: {candidate}. Nothing overwritten.")
        return candidate, data
    raise BuildError("Verified bzip2 archive is missing. Supply --archive-directory with the existing archive folder; this build never downloads.")


def archive_files(data):
    """Read verified archive members without tar's filesystem extraction helpers."""
    files = {}
    folded = set()
    size = 0
    windows_reserved = {"CON", "PRN", "AUX", "NUL"} | {f"{kind}{i}" for kind in ("COM", "LPT") for i in range(1, 10)}
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            for entry in archive:
                name = entry.name
                parts = name.split("/")
                if (not parts or parts[0] != SOURCE_ROOT or len(parts) < 2
                        or any(p in ("", ".", "..") or p.endswith((" ", ".")) for p in parts)
                        or "\\" in name or ":" in name
                        or any(p.split(".")[0].upper() in windows_reserved for p in parts)):
                    raise BuildError(f"Unsafe or unexpected bzip2 archive member: {name}")
                if entry.isdir():
                    continue
                if not entry.isfile() or name.casefold() in folded:
                    raise BuildError(f"Linked, special or duplicate bzip2 archive member: {name}")
                folded.add(name.casefold())
                size += entry.size
                if size > 32_000_000 or len(folded) > 2000:
                    raise BuildError("bzip2 archive exceeds the expected source size/member limits.")
                stream = archive.extractfile(entry)
                if stream is None:
                    raise BuildError(f"Cannot read bzip2 archive member: {name}")
                with stream:
                    content = stream.read()
                if len(content) != entry.size:
                    raise BuildError(f"Incomplete bzip2 archive member: {name}")
                files["sources/" + name] = content
    except (tarfile.TarError, EOFError) as error:
        raise BuildError(f"Cannot read the bzip2 archive: {error}") from error
    required = {SOURCE_ROOT + "/" + name for name in (
        "blocksort.c", "huffman.c", "crctable.c", "randtable.c", "compress.c",
        "decompress.c", "bzlib.c", "bzlib.h", "bzlib_private.h", "LICENSE",
    )}
    if not required.issubset({name.removeprefix("sources/") for name in files}):
        raise BuildError("The bzip2 archive is missing required library source or license files.")
    return files


def host_tag():
    if platform.machine().lower() not in ("amd64", "x86_64"):
        raise BuildError("The native bootstrap currently supports only x86_64 host computers.")
    if sys.platform == "win32":
        return "windows-x86_64"
    if sys.platform.startswith("linux"):
        return "linux-x86_64"
    raise BuildError("Use native Windows Python or Linux Python for this CMake checkpoint, not MSYS2 Python.")


def ndk_paths(ndk, tag):
    properties = require_file(ndk / "source.properties", "NDK package metadata").read_text(encoding="utf-8")
    match = re.search(r"^Pkg\.Revision\s*=\s*(\S+)\s*$", properties, re.M)
    if match is None or match.group(1) != SELECTED_PROFILE["ndk_version"]:
        raise BuildError("NDK package revision is not the selected 26.3.11579264.")
    toolchain = ndk / "toolchains/llvm/prebuilt" / tag
    suffix = ".exe" if tag == "windows-x86_64" else ""
    paths = {name: require_file(toolchain / "bin" / (name + suffix), "NDK tool")
             for name in ("clang", "clang++", "llvm-ar", "llvm-readelf")}
    paths["cmake_toolchain"] = require_file(ndk / "build/cmake/android.toolchain.cmake", "NDK CMake toolchain")
    paths["runtime"] = require_file(toolchain / "sysroot/usr/lib/x86_64-linux-android/libc++_shared.so", "Android C++ runtime")
    return paths


def run(arguments, cwd=None, capture=False):
    print("RUN: " + subprocess.list2cmdline([str(arg) for arg in arguments]), flush=True)
    # Use argument arrays with no shell; native Windows paths (including spaces)
    # go directly to CMake/NDK. No MSYS argument rewriting is involved.
    result = subprocess.run([str(arg) for arg in arguments], cwd=cwd, check=False,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.STDOUT if capture else None,
                            text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        if capture and result.stdout:
            print(result.stdout, end="", flush=True)
        raise BuildError(f"Tool failed with exit {result.returncode}: {arguments[0]}")
    return result.stdout if capture else ""


def check_tools(cmake, ninja, ndk_tools):
    cmake_lines = run([require_file(cmake, "Native CMake"), "--version"], capture=True).splitlines()
    cmake_version = cmake_lines[0] if cmake_lines else ""
    ninja_version = run([require_file(ninja, "Native Ninja"), "--version"], capture=True).strip()
    clang_lines = run([ndk_tools["clang++"], "--version"], capture=True).splitlines()
    clang_version = clang_lines[0] if clang_lines else ""
    # Native Windows reports the plain version; the verification container's
    # CMake package supplies the same Ninja release with a vendor suffix.
    if (cmake_version != "cmake version 4.3.0"
            or not re.fullmatch(r"1\.13\.2(?:[.-][A-Za-z0-9_.-]+)?", ninja_version)):
        raise BuildError(f"Use the confirmed CMake 4.3.0 / Ninja 1.13.2: got {cmake_version}; Ninja {ninja_version}.")
    if "clang version 17.0.2" not in clang_version:
        raise BuildError(f"NDK clang does not report the expected r26d version 17.0.2: {clang_version}")
    for line in (cmake_version, "Ninja " + ninja_version, clang_version):
        print(line, flush=True)
    return {"cmake": cmake_version, "ninja": ninja_version, "clang": clang_version,
            "python": platform.python_version(), "host": host_tag()}


def verify_saved_outputs(build_root):
    receipt_path = managed_path(build_root, "receipt.json")
    if not receipt_path.exists():
        return
    receipt = read_json(receipt_path)
    if (receipt.get("schema_version") != 1 or receipt.get("kind") != KIND
            or receipt.get("profile") != SELECTED_PROFILE
            or set(receipt.get("artifacts", {})) != set(ARTIFACTS)):
        raise BuildError("Existing bootstrap receipt is not recognized. Nothing overwritten.")
    for name, expected in receipt["artifacts"].items():
        if fingerprint(managed_path(build_root, name)) != expected:
            raise BuildError(f"Existing native artifact was edited: {name}. Nothing overwritten.")


def prepare_work(build_root, pin, profile, source_files, ndk, cmake, ninja):
    marker = {"schema_version": 1, "kind": KIND, "profile": profile, "archive": pin,
              "ndk_path": str(ndk), "cmake_path": str(cmake), "ninja_path": str(ninja),
              "source_files": {name: {"bytes": len(data), "sha256": sha256(data)} for name, data in sorted(source_files.items())}}
    marker_data = json_bytes(marker)
    reject_link(build_root)
    if build_root.exists():
        if not build_root.is_dir():
            raise BuildError("The native bootstrap destination is not a directory. Nothing overwritten.")
        marker_path = managed_path(build_root, MARKER)
        if not marker_path.is_file() or marker_path.read_bytes() != marker_data:
            raise BuildError("Existing native bootstrap directory is unmanaged or uses different inputs. Nothing overwritten.")
        source_root = managed_path(build_root, "sources")
        actual = set()
        for path in source_root.rglob("*"):
            reject_link(path)
            if path.is_file():
                actual.add(path.relative_to(build_root).as_posix())
        if actual != set(source_files):
            raise BuildError("Managed bzip2 source contains added or missing files. Nothing overwritten.")
        for name, data in source_files.items():
            if managed_path(build_root, name).read_bytes() != data:
                raise BuildError(f"Managed bzip2 source was edited: {name}. Nothing overwritten.")
        # Never direct CMake or the runtime copy through a preexisting link.
        out = managed_path(build_root, "out")
        if out.exists():
            for path in out.rglob("*"):
                reject_link(path)
        verify_saved_outputs(build_root)
        return
    build_root.parent.mkdir(parents=True, exist_ok=True)
    build_root.mkdir()
    for name, content in source_files.items():
        path = build_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(content)
    # Completed source marker last; interrupted extraction is never overwritten.
    with (build_root / MARKER).open("xb") as stream:
        stream.write(marker_data)


def elf_header(data):
    if len(data) < 64 or data[:6] != b"\x7fELF\x02\x01":
        raise BuildError("Artifact is not a little-endian ELF64 file.")
    kind, machine, version = struct.unpack_from("<HHI", data, 16)
    if machine != 62 or version != 1:
        raise BuildError("ELF artifact is not x86_64 with the expected ELF version.")
    return kind


def inspect_shared_elf(data, require_executable=False):
    if elf_header(data) != 3:
        raise BuildError("Android executable/runtime must use ELF ET_DYN (PIE/shared library).")
    phoff = struct.unpack_from("<Q", data, 32)[0]
    phsize, phcount = struct.unpack_from("<HH", data, 54)
    if phsize != 56 or phcount > 256 or phoff + phsize * phcount > len(data):
        raise BuildError("Invalid ELF program-header table.")
    segments = []
    for i in range(phcount):
        entry = struct.unpack_from("<IIQQQQQQ", data, phoff + i * phsize)
        kind, flags, offset, virtual, physical, filesz, memsz, align = entry
        if offset + filesz > len(data):
            raise BuildError("ELF segment extends beyond the artifact.")
        segments.append((kind, offset, virtual, filesz))
    interpreter = None
    notes = []
    dynamic = []
    for kind, offset, virtual, size in segments:
        if kind == 3:
            interpreter = data[offset:offset + size].rstrip(b"\0").decode("ascii")
        if kind == 2:
            if size % 16:
                raise BuildError("Invalid ELF dynamic table size.")
            for position in range(offset, offset + size, 16):
                tag, value = struct.unpack_from("<qQ", data, position)
                if tag == 0:
                    break
                dynamic.append((tag, value))
        if kind == 4:
            position = offset
            while position + 12 <= offset + size:
                namesz, descsz, note_type = struct.unpack_from("<III", data, position)
                position += 12
                if position + ((namesz + 3) & ~3) + ((descsz + 3) & ~3) > offset + size:
                    raise BuildError("Invalid ELF note length.")
                name = data[position:position + namesz].rstrip(b"\0")
                position += (namesz + 3) & ~3
                description = data[position:position + descsz]
                position += (descsz + 3) & ~3
                if name == b"Android" and note_type == 1 and descsz >= 4:
                    notes.append(struct.unpack_from("<I", description)[0])
    if require_executable and (interpreter != "/system/bin/linker64" or 23 not in notes):
        raise BuildError(f"Executable does not identify Android linker64 and API23: interpreter={interpreter}; APIs={notes}")
    if any(tag in (15, 29) for tag, _ in dynamic):
        raise BuildError("Android artifacts must not contain build-host RPATH/RUNPATH entries.")
    string_table = next((value for tag, value in dynamic if tag == 5), None)
    string_size = next((value for tag, value in dynamic if tag == 10), None)
    if string_table is None or string_size is None:
        raise BuildError("Missing ELF dynamic string table.")
    strings = None
    for kind, offset, virtual, size in segments:
        if kind == 1 and virtual <= string_table and string_table + string_size <= virtual + size:
            start = offset + string_table - virtual
            strings = data[start:start + string_size]
            break
    if strings is None:
        raise BuildError("ELF dynamic strings are outside the loadable segments.")
    needed = []
    for tag, value in dynamic:
        if tag != 1:
            continue
        end = strings.find(b"\0", value)
        if value >= len(strings) or end < 0:
            raise BuildError("Invalid ELF needed-library name.")
        needed.append(strings[value:end].decode("ascii"))
    allowed = {"libc++_shared.so", "libz.so", "libc.so", "libm.so", "libdl.so"}
    if set(needed) - allowed:
        raise BuildError(f"Unexpected dynamic libraries in Android artifact: {needed}")
    if require_executable and not {"libc++_shared.so", "libz.so", "libc.so"}.issubset(needed):
        raise BuildError(f"Native executable is missing expected Android runtime dependencies: {needed}")
    return {"elf_class": 64, "machine": "x86_64", "android_api_notes": notes,
            "interpreter": interpreter, "needed": needed, "host_rpath": False}


def inspect_static_archive(data):
    if not data.startswith(b"!<arch>\n"):
        raise BuildError("bzip2 library is not a regular static archive.")
    position = 8
    objects = 0
    while position < len(data):
        header = data[position:position + 60]
        if len(header) != 60 or header[58:60] != b"`\n":
            raise BuildError("Invalid static archive header.")
        name = header[:16].strip()
        try:
            size = int(header[48:58])
        except ValueError as error:
            raise BuildError("Invalid static archive member size.") from error
        position += 60
        content = data[position:position + size]
        if size < 0 or len(content) != size:
            raise BuildError("Truncated static archive member.")
        if name not in (b"/", b"//", b"/SYM64/"):
            if elf_header(content) != 1:
                raise BuildError("bzip2 static archive member is not an x86_64 ELF relocatable object.")
            objects += 1
        position += size + size % 2
    if position != len(data):
        raise BuildError("Invalid static archive padding.")
    if objects != 7:
        raise BuildError(f"Expected the seven bzip2 library objects; found {objects}.")
    return {"format": "ar", "machine": "x86_64", "elf_object_count": objects}


def build(repo, ndk, cmake, ninja, jobs, archive_directory=None):
    profile, project_inputs = profile_and_inputs(repo)
    pin = archive_pin(repo)
    archive, data = find_archive(repo, pin, archive_directory)
    sources = archive_files(data)
    tag = host_tag()
    paths = ndk_paths(ndk, tag)
    tool_versions = check_tools(cmake, ninja, paths)
    build_root = repo / "build/android-native-bootstrap"
    # Reject an aliased build parent before any creation, extraction or CMake.
    reject_link(repo / "build")
    prepare_work(build_root, pin, profile, sources, ndk, cmake, ninja)
    receipt_path = build_root / "receipt.json"
    # Invalidate the last success only after verifying its existing outputs.
    # A failed rebuild must never leave a success receipt for an ADB run.
    if receipt_path.exists():
        receipt_path.unlink()
    out = build_root / "out"
    print(f"Verified offline archive: {archive}", flush=True)
    run([cmake, "-S", repo / "tools/native-bootstrap", "-B", out, "-G", "Ninja",
         "-DCMAKE_MAKE_PROGRAM=" + ninja.as_posix(),
         "-DCMAKE_TOOLCHAIN_FILE=" + paths["cmake_toolchain"].as_posix(),
         "-DANDROID_ABI=x86_64", "-DANDROID_PLATFORM=android-23", "-DANDROID_STL=c++_shared",
         "-DCMAKE_BUILD_TYPE=Release", "-DBZIP2_SOURCE_DIR=" + (build_root / "sources" / SOURCE_ROOT).as_posix()])
    run([cmake, "--build", out, "--parallel", str(jobs)])
    runtime_bytes = paths["runtime"].read_bytes()
    runtime_validation = inspect_shared_elf(runtime_bytes)
    runtime = managed_path(build_root, ARTIFACTS[1])
    runtime.parent.mkdir(parents=True, exist_ok=True)
    if runtime.exists() and runtime.read_bytes() != runtime_bytes:
        raise BuildError("Existing copied Android runtime differs from the selected NDK. Nothing overwritten.")
    if not runtime.exists():
        with runtime.open("xb") as stream:
            stream.write(runtime_bytes)
    executable = require_file(managed_path(build_root, ARTIFACTS[0]), "Native Android executable")
    library = require_file(managed_path(build_root, ARTIFACTS[2]), "Android bzip2 library")
    validation = {ARTIFACTS[0]: inspect_shared_elf(executable.read_bytes(), require_executable=True),
                  ARTIFACTS[1]: runtime_validation, ARTIFACTS[2]: inspect_static_archive(library.read_bytes())}
    # Inputs edited while a build runs must not receive a success receipt.
    if any(fingerprint(repo / name) != expected for name, expected in project_inputs.items()):
        raise BuildError("A project build input changed during compilation. No success receipt written.")
    if any(managed_path(build_root, name).read_bytes() != content for name, content in sources.items()):
        raise BuildError("Managed bzip2 source changed during compilation. No success receipt written.")
    receipt = {"schema_version": 1, "kind": KIND, "profile": profile,
               "inputs": {"project_files": project_inputs, "archive": pin}, "tools": tool_versions,
               "artifacts": {name: fingerprint(build_root / name) for name in ARTIFACTS},
               "validation": validation, "device_executed": False}
    with receipt_path.open("xb") as stream:
        stream.write(json_bytes(receipt))
    print("NATIVE BUILD PASS: bzip2 static library and Android x86_64/API23 executable.", flush=True)
    print(f"Executable: {executable}", flush=True)
    print(f"Receipt: {receipt_path}", flush=True)
    print("This verifies a native build, not Android execution or the Wesnoth APK. Next: Test-AndroidBootstrap.ps1.", flush=True)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ndk", type=Path, required=True)
    parser.add_argument("--cmake", type=Path, required=True)
    parser.add_argument("--ninja", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--archive-directory", type=Path)
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.jobs <= 64:
            raise BuildError("--jobs must be between 1 and 64.")
        build(Path(__file__).resolve().parent.parent, args.ndk.resolve(), args.cmake.resolve(), args.ninja.resolve(),
              args.jobs, args.archive_directory.resolve() if args.archive_directory is not None else None)
    except (BuildError, OSError, ValueError, UnicodeError, struct.error) as error:
        print(f"BLOCKED: {error}", file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
