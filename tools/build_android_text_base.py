#!/usr/bin/env python3
"""Build offline libiconv/libcharset and Pixman using MSYS or Linux build tools.

Uses Autoconf's release configure script and Meson without source regeneration,
downloads, target execution, or changes to completed native checkpoints.
"""

import argparse
import importlib.util
import io
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("wesnoth_text_media", REPO / "tools/build_android_media.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
B = M.B
BuildError = B.BuildError
KIND = "wesnoth-android-text-base"
MARKER = ".wesnoth-android-text-base.json"
PREFIX_STATE = ".prefix-state.json"
PROJECT_INPUTS = (
    "tools/build_android_text_base.py", "tools/build_android_media.py", "tools/build_android_bootstrap.py",
    "tools/android-build-profile.json", "tools/android-native-archives.json",
    "tools/native-text-base/build-plan.json", "tools/native-text-base/probe.c",
)
PACKAGES = (("iconv", "libiconv-1.17.tar.gz", "libiconv-1.17"),
            ("pixman", "pixman-0.42.2.tar.gz", "pixman-0.42.2"))
ARTIFACTS = ("prefix/bin/wesnoth-text-base-check",)
REQUIRED_ARCHIVES = ("libiconv.a", "libcharset.a", "libpixman-1.a")
REQUIRED_HEADERS = ("iconv.h", "libcharset.h", "localcharset.h", "pixman-1/pixman.h", "pixman-1/pixman-version.h")
REQUIRED_PC = ("iconv.pc", "libcharset.pc", "pixman-1.pc")


def selected_inputs(repo):
    profile = B.read_json(repo / "tools/android-build-profile.json")
    if profile.get("schema_version") != 1 or any(profile.get(k) != v for k, v in B.SELECTED_PROFILE.items()):
        raise BuildError("The text base requires the selected Wesnoth/NDK/x86_64/API23 profile.")
    inputs = {name: B.fingerprint(B.require_file(repo / name, "Text base input")) for name in PROJECT_INPUTS}
    plan = B.read_json(repo / "tools/native-text-base/build-plan.json")
    packages = plan.get("packages", [])
    if (plan.get("schema_version") != 1 or not isinstance(packages, list)
            or [(p.get("name"), p.get("archive"), p.get("source_dir")) for p in packages] != list(PACKAGES)):
        raise BuildError("The text base plan must select libiconv 1.17 then Pixman 0.42.2.")
    for package, key in zip(packages, ("configure_options", "meson_options")):
        if (not isinstance(package.get(key), list) or not package[key]
                or not all(isinstance(x, str) and x.startswith("-") and "\n" not in x for x in package[key])
                or not isinstance(package.get("license_files"), list) or not package["license_files"]):
            raise BuildError("Unrecognized text base build options/licenses.")
        for name in package["license_files"]:
            M.safe_name(name)
    if "--wrap-mode=nodownload" not in packages[1]["meson_options"]:
        raise BuildError("The Pixman plan must forbid Meson downloads.")
    pins = {}
    manifest = B.read_json(repo / "tools/android-native-archives.json")
    for _, name, _ in PACKAGES:
        entries = [entry for entry in manifest.get("archives", []) if entry.get("name") == name]
        if (len(entries) != 1 or type(entries[0].get("bytes")) is not int
                or not 0 < entries[0]["bytes"] < 50_000_000
                or not re.fullmatch(r"[0-9a-f]{64}", str(entries[0].get("sha256", "")))):
            raise BuildError(f"Invalid or missing source archive pin: {name}")
        pins[name] = {key: entries[0][key] for key in ("bytes", "sha256")}
    return dict(B.SELECTED_PROFILE), inputs, packages, pins


def archive_files(data, source_dir):
    files, executables, seen = {}, [], set()
    size = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        for entry in archive:
            name = entry.name.rstrip("/") if entry.isdir() else entry.name
            parts = M.safe_name(name)
            if parts[0] != source_dir:
                raise BuildError(f"Unexpected text archive root: {name}")
            if entry.isdir():
                continue
            if not entry.isfile() or len(parts) < 2 or name.casefold() in seen:
                raise BuildError(f"Linked, special or duplicate text archive member: {name}")
            seen.add(name.casefold())
            size += entry.size
            if size > 50_000_000 or len(seen) > 5000:
                raise BuildError("Text source archive exceeds the expected size/member limits.")
            stream = archive.extractfile(entry)
            if stream is None:
                raise BuildError(f"Cannot read text source member: {name}")
            content = stream.read()
            if len(content) != entry.size:
                raise BuildError(f"Incomplete text source member: {name}")
            relative = "sources/" + name
            files[relative] = content
            if entry.mode & 0o111:
                executables.append(relative)
    required = "/configure" if source_dir.startswith("libiconv-") else "/meson.build"
    if "sources/" + source_dir + required not in files:
        raise BuildError(f"Text source archive lacks its build entry point: {source_dir}")
    return files, executables


def run(arguments, env=None, cwd=None, capture=False):
    print("RUN: " + shlex.join([str(arg) for arg in arguments]), flush=True)
    # All build tools are noninteractive. A Windows GUI/process launcher can
    # leave stdin invalid; libtool still duplicates fd 0 while reading .lo
    # metadata. Give every child a readable EOF stream instead of inheriting it.
    result = subprocess.run([str(arg) for arg in arguments], env=env, cwd=cwd, check=False,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.STDOUT if capture else None,
                            text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        if capture:
            print(result.stdout, end="", flush=True)
        raise BuildError(f"Tool failed with exit {result.returncode}: {arguments[0]}")
    return result.stdout if capture else ""


def is_msys():
    return sys.platform in ("cygwin", "msys")


def input_path(value):
    if is_msys() and re.match(r"^[A-Za-z]:[/\\]", value):
        value = run(["/usr/bin/cygpath", "-u", "--", value], capture=True).strip()
    return Path(value).resolve()


def isolated_environment():
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("PKG_CONFIG_", "PYTHON", "MESON_")) or key in (
            "CC", "CXX", "AR", "AS", "LD", "NM", "RANLIB", "STRIP", "CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS",
            "CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "LIBRARY_PATH", "INCLUDE", "LIB",
            "MAKEFLAGS", "MFLAGS", "GNUMAKEFLAGS", "MAKEFILES", "MAKEOVERRIDES", "BASH_ENV", "ENV",
            "MSYS2_ARG_CONV_EXCL", "MSYS_NO_PATHCONV", "CONFIG_SITE", "CONFIG_SHELL"):
            env.pop(key, None)
    env.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", CONFIG_SITE="/dev/null",
               LC_ALL="C", LANG="C", PKG_CONFIG_PATH="", PKG_CONFIG_LIBDIR="", PKG_CONFIG_SYSROOT_DIR="")
    if is_msys():
        env.update(PATH="/usr/bin:/bin", MSYSTEM="MSYS")
    return env


def tools_and_versions(ndk, env):
    if platform.machine().lower() not in ("amd64", "x86_64") or not (is_msys() or sys.platform.startswith("linux")):
        raise BuildError("Build with MSYS /usr/bin/python3 on Windows or Linux Python. Native Windows Python supports --verify-only.")
    tag = "windows-x86_64" if is_msys() else "linux-x86_64"
    ndk_tools = B.ndk_paths(ndk, tag)
    suffix = ".exe" if is_msys() else ""
    for name in ("llvm-ranlib", "llvm-strip", "llvm-nm", "llvm-objdump"):
        ndk_tools[name] = B.require_file(ndk_tools["clang"].parent / (name + suffix), "NDK tool")
    tools, versions = {}, {"host": tag, "python": platform.python_version(), "python_executable": sys.executable}
    for name in ("bash", "make", "meson", "ninja", "pkg-config", "patch", "gperf", "perl"):
        path = Path("/usr/bin") / name if is_msys() else Path(shutil.which(name) or "/missing/" + name)
        tools[name] = B.require_file(path, "Text build host tool")
        argument = ["-e", 'printf "Perl %vd", $^V'] if name == "perl" else ["--version"]
        lines = run([path, *argument], env, capture=True).splitlines()
        if not lines:
            raise BuildError(f"Host tool returned no version: {path}")
        versions[name] = lines[0]
    lines = run([ndk_tools["clang"], "--version"], env, capture=True).splitlines()
    if not lines or "clang version 17.0.2" not in lines[0]:
        raise BuildError("NDK clang must report the selected r26d version 17.0.2.")
    versions["clang"] = lines[0]
    print("Selected host tools: " + str(versions), flush=True)
    return ndk_tools, tools, versions


def generated_files(root, ndk_tools, tools):
    generated = {}
    for name, tool, arguments in (("cc", "clang", ["--target=x86_64-linux-android23"]),
                                   ("ar", "llvm-ar", []), ("ranlib", "llvm-ranlib", []), ("strip", "llvm-strip", []),
                                   ("nm", "llvm-nm", []), ("objdump", "llvm-objdump", [])):
        command = [str(ndk_tools[tool]), *arguments]
        # Preserve Android runtime directory literals; all source/include/output
        # paths still receive the normal MSYS-to-native argument conversion.
        conversion = "export MSYS2_ARG_CONV_EXCL='-DLIBDIR=;-DINSTALLDIR='\n" if is_msys() and name == "cc" else ""
        generated["toolchain/" + name] = ("#!/usr/bin/env bash\n" + conversion + "exec " + shlex.join(command) + ' "$@"\n').encode()
    cross = "[binaries]\n"
    for key, name in (("c", "cc"), ("ar", "ar"), ("strip", "strip")):
        cross += f"{key} = [{str(root / 'toolchain' / name)!r}]\n"
    cross += f"pkg-config = [{str(tools['pkg-config'])!r}]\n"
    cross += "[host_machine]\nsystem = 'android'\ncpu_family = 'x86_64'\ncpu = 'x86_64'\nendian = 'little'\n"
    cross += "[properties]\nneeds_exe_wrapper = true\n[built-in options]\nc_args = ['-O2', '-fPIC']\n"
    generated["toolchain/android.ini"] = cross.encode()
    return generated


def verify_sources(root, marker):
    if (marker.get("schema_version") != 1 or marker.get("kind") != KIND
            or M.inventory(root, "sources") != marker.get("source_files")
            or M.inventory(root, "toolchain") != marker.get("generated_files")):
        raise BuildError("Managed text source/toolchain files were changed, added or removed. Nothing overwritten.")


def prepare_sources(root, files, executables, generated, profile, pins):
    B.reject_link(root.parent)
    B.reject_link(root)
    records = lambda data: {name: {"bytes": len(value), "sha256": B.sha256(value)} for name, value in sorted(data.items())}
    marker = {"schema_version": 1, "kind": KIND, "profile": profile, "archives": pins,
              "source_files": records(files), "generated_files": records(generated)}
    if root.exists():
        path = B.managed_path(root, MARKER)
        if not path.is_file() or B.read_json(path) != marker:
            raise BuildError("Existing text directory is unmanaged or uses different source/toolchain inputs. Nothing overwritten.")
        verify_sources(root, marker)
        for relative in ("build", "prefix"):
            directory = root / relative
            B.reject_link(directory)
            for child in directory.rglob("*"):
                # Libtool owns these two relative aliases in its private build
                # directory. No installed files or build directories may link.
                aliases = {
                    "build/iconv/lib/.libs/libiconv.la": "../libiconv.la",
                    "build/iconv/libcharset/lib/.libs/libcharset.la": "../libcharset.la",
                }
                expected = aliases.get(child.relative_to(root).as_posix())
                if child.is_symlink() and expected is not None and os.readlink(child) == expected:
                    target = child.parent / expected
                    B.reject_link(target)
                    B.require_file(target, "Known private libtool alias target")
                    continue
                B.reject_link(child)
        return marker
    root.mkdir(parents=True)
    for name, content in {**files, **generated}.items():
        path = B.managed_path(root, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(content)
        path.chmod(0o755 if name in executables or name.startswith("toolchain/") and name != "toolchain/android.ini" else 0o644)
    (root / MARKER).write_bytes(B.json_bytes(marker))
    return marker


def verify_prefix(root):
    files = M.inventory(root)
    state_path = B.managed_path(root, PREFIX_STATE)
    if state_path.exists():
        state = B.read_json(state_path)
        if state != {"schema_version": 1, "kind": KIND, "prefix_files": files}:
            raise BuildError("Installed text prefix changed or an install was interrupted. Nothing overwritten.")
    elif files:
        raise BuildError("Existing text prefix has no owned file record. Nothing overwritten.")
    return files


def save_prefix_state(root):
    files = M.inventory(root)
    (root / PREFIX_STATE).write_bytes(B.json_bytes({"schema_version": 1, "kind": KIND, "prefix_files": files}))
    return files


def copy_file(source, destination):
    B.reject_link(source)
    B.reject_link(destination)
    B.require_file(source, "Generated text library/header")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())


def normalize_pc(path):
    content = path.read_text(encoding="utf-8")
    content = re.sub(r"(?m)^prefix=.*$", "prefix=${pcfiledir}/../..", content)
    path.write_text(content, encoding="utf-8", newline="\n")


def write_library_pc(directory, name, library, version):
    # pkg-config already escapes spaces in its built-in pcfiledir expansion.
    # Extra quotes here would escape those backslashes a second time.
    (directory / (name + ".pc")).write_text(
        "prefix=${pcfiledir}/../..\nlibdir=${prefix}/lib\nincludedir=${prefix}/include\n"
        f"Name: {name}\nDescription: GNU {name} static Android library\nVersion: {version}\n"
        f"Libs: -L${{libdir}} -l{library}\nCflags: -I${{includedir}}\n", encoding="utf-8", newline="\n")


def validate_prefix(root):
    prefix = root / "prefix"
    for name in REQUIRED_HEADERS:
        B.require_file(prefix / "include" / name, "Installed text header")
    for name in REQUIRED_PC:
        B.require_file(prefix / "lib/pkgconfig" / name, "Installed text package metadata")
    static = {path.relative_to(prefix).as_posix(): M.inspect_static_archive(path) for path in sorted(prefix.rglob("*.a"))}
    if set(static) != {"lib/" + name for name in REQUIRED_ARCHIVES}:
        raise BuildError("The text prefix must contain exactly libiconv, libcharset and Pixman static archives.")
    probe = B.require_file(root / ARTIFACTS[0], "Installed text probe")
    elf = B.inspect_shared_elf(probe.read_bytes())
    if (elf["interpreter"] != "/system/bin/linker64" or elf["android_api_notes"] != [23]
            or "libc.so" not in elf["needed"] or not set(elf["needed"]).issubset({"libc.so", "libm.so", "libdl.so"})):
        raise BuildError("Text probe must be an Android x86_64/API23 C executable with only NDK system dependencies.")
    files = M.inventory(root)
    for name, record in files.items():
        if record["bytes"] == 0 or name.endswith((".so", ".la")) or ".so." in name:
            raise BuildError(f"Unexpected empty/shared/libtool file in text prefix: {name}")
        if name.endswith(".pc"):
            text = (root / name).read_text(encoding="utf-8")
            if "prefix=${pcfiledir}/../.." not in text or re.search(r"[A-Za-z]:[/\\]|/(?:home|workspace|tmp|opt)/", text):
                raise BuildError(f"Installed text metadata retains a build-host path: {name}")
    return {"static_libraries": static, "runtime": {ARTIFACTS[0]: elf}}, files


def verify_receipt(repo):
    profile, inputs, _, pins = selected_inputs(repo)
    root = repo / "build/android-text-base"
    B.reject_link(root.parent)
    B.reject_link(root)
    receipt = B.read_json(B.managed_path(root, "receipt.json"))
    validation, files = validate_prefix(root)
    if (receipt.get("schema_version") != 1 or receipt.get("kind") != KIND or receipt.get("profile") != profile
            or receipt.get("inputs") != {"project_files": inputs, "archives": pins}
            or receipt.get("prefix_files") != files or receipt.get("validation") != validation
            or receipt.get("artifacts") != {name: files[name] for name in ARTIFACTS}):
        raise BuildError("Text receipt/input/artifact hashes differ. Rebuild only after preserving any local changes.")
    print("ANDROID TEXT BASE ARTIFACTS VERIFIED", flush=True)
    return receipt


def build(repo, args):
    profile, inputs, packages, pins = selected_inputs(repo)
    files, executables = {}, []
    for package in packages:
        directory = args.archive_directory
        if directory is None and is_msys() and (Path("/tmp/android-dl") / package["archive"]).exists():
            directory = Path("/tmp/android-dl")
        data = M.archive_data(repo, package["archive"], pins[package["archive"]], directory)
        members, modes = archive_files(data, package["source_dir"])
        files.update(members)
        executables.extend(modes)
        for name in package["license_files"]:
            if not files.get("sources/" + package["source_dir"] + "/" + name):
                raise BuildError(f"Source archive lacks its declared license: {name}")
    root = repo / "build/android-text-base"
    # GNU release Makefiles do not consistently quote source/build directory
    # variables. The user's existing workspace satisfies this constraint;
    # NDK paths may contain spaces because only owned quoted wrappers use them.
    if re.search(r"[\s'\"$`#;&<>|?!*]", str(root)):
        raise BuildError("This Autoconf stage requires a workspace path without spaces or shell metacharacters; NDK paths may contain spaces.")
    env = isolated_environment()
    ndk_tools, tools, versions = tools_and_versions(args.ndk, env)
    generated = generated_files(root, ndk_tools, tools)
    marker = prepare_sources(root, files, executables, generated, profile, pins)
    verify_prefix(root)
    receipt_path = B.managed_path(root, "receipt.json")
    if receipt_path.exists():
        receipt_path.unlink()
    prefix = root / "prefix"
    prefix.mkdir(exist_ok=True)
    save_prefix_state(root)
    env.update(CC=str(root / "toolchain/cc"), AR=str(root / "toolchain/ar"), RANLIB=str(root / "toolchain/ranlib"),
               NM=str(root / "toolchain/nm"), OBJDUMP=str(root / "toolchain/objdump"), STRIP=str(root / "toolchain/strip"),
               CFLAGS="-O2 -fPIC", CONFIG_SHELL=str(tools["bash"]))
    iconv_source = root / "sources" / packages[0]["source_dir"]
    iconv_build = root / "build/iconv"
    iconv_build.mkdir(parents=True, exist_ok=True)
    build_triplet = run([tools["bash"], iconv_source / "build-aux/config.guess"], env, capture=True).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.+-]+", build_triplet):
        raise BuildError("Unexpected config.guess build-machine identity.")
    run([tools["bash"], iconv_source / "configure", "--host=x86_64-linux-android", "--build=" + build_triplet,
         *packages[0]["configure_options"]], env, iconv_build)
    run([tools["make"], "-C", iconv_build / "libcharset", "-j", str(args.jobs), "all"], env)
    copy_file(iconv_build / "libcharset/include/localcharset.h", iconv_build / "lib/localcharset.h")
    run([tools["make"], "-C", iconv_build / "lib", "-j", str(args.jobs), "all"], env)
    for source, destination in (
        ("lib/.libs/libiconv.a", "lib/libiconv.a"), ("libcharset/lib/.libs/libcharset.a", "lib/libcharset.a"),
        ("include/iconv.h.inst", "include/iconv.h"), ("libcharset/include/libcharset.h.inst", "include/libcharset.h"),
        ("libcharset/include/localcharset.h.inst", "include/localcharset.h"),
    ):
        copy_file(iconv_build / source, prefix / destination)
    pc_dir = prefix / "lib/pkgconfig"
    pc_dir.mkdir(parents=True, exist_ok=True)
    for name, library, version in (("iconv", "iconv", "1.17"), ("libcharset", "charset", "1.5")):
        write_library_pc(pc_dir, name, library, version)
    save_prefix_state(root)
    pixman_build = root / "build/pixman"
    setup = [tools["meson"], "setup", pixman_build, root / "sources" / packages[1]["source_dir"],
             "--cross-file", root / "toolchain/android.ini", *packages[1]["meson_options"]]
    if (pixman_build / "meson-private/coredata.dat").exists():
        setup.append("--reconfigure")
    run(setup, env)
    run([tools["ninja"], "-C", pixman_build, "-j", str(args.jobs)], env)
    install = root / "build/pixman-install"
    run([tools["meson"], "install", "-C", pixman_build, "--no-rebuild", "--destdir", install], env)
    for name in ("lib/libpixman-1.a", "include/pixman-1/pixman.h", "include/pixman-1/pixman-version.h", "lib/pkgconfig/pixman-1.pc"):
        copy_file(install / "usr" / name, prefix / name)
    normalize_pc(pc_dir / "pixman-1.pc")
    for package in packages:
        for name in package["license_files"]:
            copy_file(root / "sources" / package["source_dir"] / name,
                      prefix / "share/licenses/wesnoth-text-base" / package["name"] / name)
    save_prefix_state(root)
    probe = prefix / "bin/wesnoth-text-base-check"
    probe.parent.mkdir(exist_ok=True)
    probe_env = dict(env, PKG_CONFIG_LIBDIR=str(pc_dir), PKG_CONFIG_PATH="")
    flags = shlex.split(run([tools["pkg-config"], "--cflags", "--libs", "--static", "libcharset", "iconv", "pixman-1"], probe_env, capture=True))
    link_map = root / "build/probe.map"
    # Keep the map filename as its own argument so MSYS converts its path
    # before native clang passes it to lld; do not hide it inside -Wl,... .
    run([root / "toolchain/cc", "-std=c11", "-O2", "-fPIE", "-pie", repo / "tools/native-text-base/probe.c",
         *flags, "-lm", "-Xlinker", "-Map", "-Xlinker", link_map, "-o", probe], env)
    mapping = B.require_file(link_map, "Native probe linker map").read_text(encoding="utf-8")
    for name in REQUIRED_ARCHIVES:
        if name + "(" not in mapping:
            raise BuildError(f"Probe linker map does not identify members from {name}.")
    save_prefix_state(root)
    validation, records = validate_prefix(root)
    verify_sources(root, marker)
    if any(B.fingerprint(repo / name) != record for name, record in inputs.items()):
        raise BuildError("A text build input changed during compilation. No success receipt written.")
    receipt = {"schema_version": 1, "kind": KIND, "profile": profile,
               "inputs": {"project_files": inputs, "archives": pins}, "tools": versions,
               "artifacts": {name: records[name] for name in ARTIFACTS}, "prefix_files": records,
               "validation": validation, "device_executed": False}
    with receipt_path.open("xb") as stream:
        stream.write(B.json_bytes(receipt))
    print("ANDROID TEXT BASE BUILD PASS", flush=True)
    print(f"Prefix: {prefix}\nReceipt: {receipt_path}", flush=True)
    print("Libraries and Android probe built offline; Android execution and full text layout remain untested.", flush=True)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ndk")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--archive-directory")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    try:
        if args.verify_only:
            verify_receipt(REPO)
        else:
            if not args.ndk or not 1 <= args.jobs <= 32:
                raise BuildError("Build requires --ndk PATH and --jobs between 1 and 32.")
            args.ndk = input_path(args.ndk)
            args.archive_directory = input_path(args.archive_directory) if args.archive_directory else None
            build(REPO, args)
    except (BuildError, OSError, ValueError, tarfile.TarError) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
