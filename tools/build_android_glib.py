#!/usr/bin/env python3
"""Build the cached GLib 2.82.1 static Android libraries using verified prerequisites.

No downloads, target execution or changes to completed stages. Build with MSYS
Python on Windows or Linux Python; native Windows Python can verify receipts.
"""

import argparse
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tarfile

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("wesnoth_glib_prerequisites", REPO / "tools/build_android_glib_deps.py")
G = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(G)
T, M, B = G.T, G.M, G.B
BuildError = B.BuildError
KIND = "wesnoth-android-glib"
MARKER = ".wesnoth-android-glib.json"
PREFIX_STATE = ".prefix-state.json"
PROJECT_INPUTS = (*G.PROJECT_INPUTS, "tools/build_android_glib.py", "tools/native-glib/build-plan.json", "tools/native-glib/probe.c")
ARTIFACTS = ("prefix/bin/wesnoth-glib-check",)
TARGETS = ("glib/libglib-2.0.a", "gobject/libgobject-2.0.a", "gio/libgio-2.0.a", "gmodule/libgmodule-2.0.a", "gthread/libgthread-2.0.a")
REQUIRED_ARCHIVES = tuple(Path(name).name for name in TARGETS)
REQUIRED_HEADERS = ("glib.h", "glib-object.h", "gmodule.h", "gio/gio.h")
REQUIRED_PC = ("glib-2.0.pc", "gobject-2.0.pc", "gio-2.0.pc", "gio-unix-2.0.pc", "gmodule-2.0.pc", "gmodule-no-export-2.0.pc", "gmodule-export-2.0.pc", "gthread-2.0.pc")
ARCHIVE = "glib-2.82.1.tar.xz"
SOURCE = "glib-2.82.1"
WRAP_LOCK = "sources/" + SOURCE + "/subprojects/.wraplock"
LICENSE_ALIASES = {"glib-2.82.1/COPYING": "LICENSES/LGPL-2.1-or-later.txt", "glib-2.82.1/gmodule/COPYING": "../LICENSES/LGPL-2.1-or-later.txt"}


def selected_inputs(repo):
    profile, _, _, _ = G.selected_inputs(repo)
    inputs = {name: B.fingerprint(B.require_file(repo / name, "GLib input")) for name in PROJECT_INPUTS}
    plan = B.read_json(repo / "tools/native-glib/build-plan.json")
    package = plan.get("package", {})
    required = {"--wrap-mode=nodownload", "-Ddefault_library=static", "-Db_staticpic=true", "-Dtests=false", "-Dinstalled_tests=false", "-Dintrospection=disabled", "-Ddocumentation=false", "-Dnls=disabled"}
    options = package.get("meson_options", [])
    if (plan.get("schema_version") != 1 or package.get("name") != "glib" or package.get("archive") != ARCHIVE
            or package.get("source_dir") != SOURCE or package.get("targets") != list(TARGETS)
            or not isinstance(options, list) or not all(isinstance(x, str) and x.startswith("-") and "\n" not in x for x in options)
            or not required.issubset(options) or not isinstance(package.get("license_files"), list) or not package["license_files"]
            or not package.get("zlib_backend", "").startswith("Android NDK r26d API23 system libz")):
        raise BuildError("GLib requires its selected static offline 2.82.1 plan and NDK system zlib.")
    for name in package["license_files"]:
        M.safe_name(name)
    manifest = B.read_json(repo / "tools/android-native-archives.json")
    pins = [entry for entry in manifest.get("archives", []) if entry.get("name") == ARCHIVE]
    if (len(pins) != 1 or type(pins[0].get("bytes")) is not int or not 0 < pins[0]["bytes"] < 10_000_000
            or not re.fullmatch(r"[0-9a-f]{64}", str(pins[0].get("sha256", "")))):
        raise BuildError("The cached GLib source pin is invalid or missing.")
    return profile, inputs, package, {ARCHIVE: {name: pins[0][name] for name in ("bytes", "sha256")}}


def prior_inputs(repo):
    receipt = G.verify_receipt(repo)
    return {"text_base": G.text_base_inputs(repo), "glib_deps": {
        "receipt": B.fingerprint(B.require_file(repo / "build/android-glib-deps/receipt.json", "GLib prerequisite receipt")),
        "prefix_files": receipt["prefix_files"]}}


def archive_files(data):
    files, executables, seen, spelling, links, size = {}, [], set(), {}, {}, 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        for entry in archive:
            name = entry.name.rstrip("/") if entry.isdir() else entry.name
            parts = M.safe_name(name)
            if parts[0] != SOURCE:
                raise BuildError(f"Unexpected GLib source root: {name}")
            if ("sources/" + name).casefold() == WRAP_LOCK.casefold():
                raise BuildError("GLib archive must not supply the managed Meson lock file.")
            for end in range(1, len(parts) + 1):
                component = "/".join(parts[:end])
                if spelling.setdefault(component.casefold(), component) != component:
                    raise BuildError(f"GLib source paths collide on Windows: {name}")
            if entry.isdir():
                continue
            if len(parts) < 2 or name.casefold() in seen:
                raise BuildError(f"Duplicate or invalid GLib archive member: {name}")
            seen.add(name.casefold())
            if entry.issym() and LICENSE_ALIASES.get(name) == entry.linkname:
                links[name] = entry.linkname
                continue
            if not entry.isfile():
                raise BuildError(f"Unrecognized linked or special GLib archive member: {name}")
            size += entry.size
            if size > 70_000_000 or len(seen) > 3000:
                raise BuildError("GLib source archive exceeds its size/member limits.")
            stream = archive.extractfile(entry)
            if stream is None:
                raise BuildError(f"Cannot read GLib source: {name}")
            content = stream.read()
            if len(content) != entry.size:
                raise BuildError(f"Incomplete GLib source: {name}")
            relative = "sources/" + name
            files[relative] = content
            if entry.mode & 0o111:
                executables.append(relative)
    if links != LICENSE_ALIASES:
        raise BuildError("GLib archive must contain exactly its two recorded license aliases.")
    target = "sources/" + SOURCE + "/LICENSES/LGPL-2.1-or-later.txt"
    if not files.get(target) or "sources/" + SOURCE + "/meson.build" not in files:
        raise BuildError("GLib archive lacks its build entry point or regular license target.")
    for name in links:
        files["sources/" + name] = files[target]
    # Meson 1.12 uses an empty, persistent DirectoryLock file while resolving
    # bundled gvdb. Linux and the selected MSYS Python both use its POSIX
    # implementation (open w+, flock, close; no writes). Record this exact
    # empty control file in the source inventory instead of ignoring it.
    files[WRAP_LOCK] = b""
    return files, executables


def generated_files(root, repo, ndk_tools, tools):
    generated = G.generated_files(root, ndk_tools, tools)
    prefixes = [repo / "build/android-text-base/prefix", repo / "build/android-glib-deps/prefix"]
    includes = ["-I" + str(path / "include") for path in prefixes]
    libraries = ["-L" + str(path / "lib") for path in prefixes]
    cross = "[binaries]\n"
    for key, name in (("c", "cc"), ("cpp", "cxx"), ("ar", "ar"), ("strip", "strip")):
        cross += f"{key} = [{str(root / 'toolchain' / name)!r}]\n"
    cross += f"pkg-config = [{str(tools['pkg-config'])!r}]\n"
    cross += "[host_machine]\nsystem = 'android'\ncpu_family = 'x86_64'\ncpu = 'x86_64'\nendian = 'little'\n"
    cross += "[properties]\nneeds_exe_wrapper = true\n"
    cross += f"pkg_config_libdir = {[str(path / 'lib/pkgconfig') for path in prefixes]!r}\n"
    cross += "[built-in options]\n"
    cross += f"c_args = {['-O2', '-fPIC', *includes]!r}\ncpp_args = {['-O2', '-fPIC', *includes]!r}\n"
    cross += f"c_link_args = {libraries!r}\ncpp_link_args = {libraries!r}\n"
    generated["toolchain/android.ini"] = cross.encode()
    if T.is_msys():
        generated["toolchain/cc"] = generated["toolchain/cc"].replace(
            b"-DLOCALE_ALIAS_PATH=", b"-DLOCALE_ALIAS_PATH=;-DGIO_MODULE_DIR=;-DGIO_LAUNCH_DESKTOP=;-DGIO_QUERYMODULES=;-DGLIB_CHARSETALIAS_DIR=;-DLOCALSTATEDIR=")
        conversion = next(line for line in generated["toolchain/cc"].splitlines() if line.startswith(b"export MSYS2_ARG_CONV_EXCL="))
        generated["toolchain/cxx"] = generated["toolchain/cxx"].replace(b"#!/usr/bin/env bash\n", b"#!/usr/bin/env bash\n" + conversion + b"\n", 1)
    return generated


def verify_sources(root, marker):
    if (marker.get("schema_version") != 1 or marker.get("kind") != KIND
            or M.inventory(root, "sources") != marker.get("source_files")
            or M.inventory(root, "toolchain") != marker.get("generated_files")):
        raise BuildError("Managed GLib source/toolchain files were changed, added or removed. Nothing overwritten.")


def prepare_sources(root, files, executables, generated, profile, pins, prior):
    B.reject_link(root.parent)
    B.reject_link(root)
    records = lambda data: {name: {"bytes": len(value), "sha256": B.sha256(value)} for name, value in sorted(data.items())}
    marker = {"schema_version": 1, "kind": KIND, "profile": profile, "archives": pins, "prerequisites": prior,
              "source_files": records(files), "generated_files": records(generated)}
    if root.exists():
        path = B.managed_path(root, MARKER)
        if not path.is_file() or B.read_json(path) != marker:
            raise BuildError("Existing GLib directory is unmanaged or uses different source/toolchain inputs. Nothing overwritten.")
        verify_sources(root, marker)
        for relative in ("build", "prefix"):
            directory = root / relative
            B.reject_link(directory)
            for child in directory.rglob("*"):
                B.reject_link(child)
        return marker
    root.mkdir(parents=True)
    for name, content in {**files, **generated}.items():
        path = B.managed_path(root, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(content)
        path.chmod(0o755 if name in executables or name.startswith("toolchain/") and name != "toolchain/android.ini" else 0o644)
        os.utime(path, (1577836800, 1577836800))
    (root / MARKER).write_bytes(B.json_bytes(marker))
    return marker


def verify_prefix(root):
    files = M.inventory(root)
    path = B.managed_path(root, PREFIX_STATE)
    if path.exists():
        if B.read_json(path) != {"schema_version": 1, "kind": KIND, "prefix_files": files}:
            raise BuildError("Installed GLib prefix changed or an install was interrupted. Nothing overwritten.")
    elif files:
        raise BuildError("Existing GLib prefix has no owned file record. Nothing overwritten.")
    return files


def save_prefix_state(root):
    files = M.inventory(root)
    (root / PREFIX_STATE).write_bytes(B.json_bytes({"schema_version": 1, "kind": KIND, "prefix_files": files}))
    return files


def install_development_files(root):
    private, source = root / "build/glib", root / "sources" / SOURCE
    mapping = B.read_json(B.require_file(private / "meson-info/intro-installed.json", "Meson installation manifest"))
    selected = {}
    for name, destination in mapping.items():
        if not isinstance(destination, str):
            raise BuildError("Unexpected GLib Meson installation manifest entry.")
        relative = destination.removeprefix("/usr/")
        public_header = relative in {"include/glib-2.0/" + name for name in ("glib.h", "glib-object.h", "glib-unix.h", "gmodule.h")}
        public_header = public_header or any(relative.startswith("include/glib-2.0/" + name + "/") for name in ("glib", "gobject", "gio", "gmodule"))
        public_header = public_header or relative.startswith("include/gio-unix-2.0/gio/")
        if not (public_header and relative.endswith(".h")
                or relative == "include/glib-2.0/gobject/gobjectnotifyqueue.c"
                or relative == "lib/glib-2.0/include/glibconfig.h"
                or relative in {"lib/pkgconfig/" + name for name in REQUIRED_PC}):
            continue
        if not destination.startswith("/usr/"):
            raise BuildError("GLib installation manifest has an unexpected prefix.")
        M.safe_name(relative)
        path = Path(name)
        if not path.is_absolute() or not (path.is_relative_to(source) or path.is_relative_to(private)):
            raise BuildError(f"GLib install source escapes its owned source/build: {name}")
        owned_relative = path.relative_to(root).as_posix()
        M.safe_name(owned_relative)
        path = B.managed_path(root, owned_relative)
        if relative in selected:
            raise BuildError(f"Duplicate GLib installation destination: {relative}")
        selected[relative] = path
    if not selected:
        raise BuildError("GLib Meson manifest contains no selected public development files.")
    for relative, path in selected.items():
        T.copy_file(path, root / "prefix" / relative)
    for target in TARGETS:
        T.copy_file(private / target, root / "prefix/lib" / Path(target).name)


def normalize_metadata(root, repo):
    directory = root / "prefix/lib/pkgconfig"
    # GLib finds libintl and iconv via Meson's builtin dependency detectors.
    # Their absolute build-time archives must become package dependencies.
    for name in REQUIRED_PC:
        path = B.require_file(directory / name, "GLib package metadata")
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"(?m)^prefix=.*$", "prefix=${pcfiledir}/../..", text)
        text = re.sub(r"(?m)^bindir=.*$", "bindir=${prefix}/bin", text)
        text = re.sub(r"(?m)^libdir=.*$", "libdir=${prefix}/lib", text)
        # This bounded prefix contains target libraries and headers. Do not
        # advertise host code generators or utilities which were not built.
        uninstalled = ("glib_genmarshal", "gobject_query", "glib_mkenums", "glib_valgrind_suppressions",
                       "gio", "gio_querymodules", "glib_compile_schemas", "glib_compile_resources",
                       "gdbus", "gdbus_codegen", "gresource", "gsettings")
        text = re.sub(r"(?m)^(?:" + "|".join(uninstalled) + r")=.*\n?", "", text)
        for stage, library in (("android-text-base", "iconv"), ("android-glib-deps", "intl")):
            archive = str(repo / "build" / stage / "prefix/lib" / ("lib" + library + ".a"))
            text = text.replace(archive, "-l" + library)
        if name == "glib-2.0.pc":
            for dependency in ("intl", "iconv"):
                if re.search(r"(?m)^Requires.private:.*\b" + dependency + r"\b", text) is None:
                    if re.search(r"(?m)^Requires.private:", text):
                        text = re.sub(r"(?m)^Requires.private:.*$", lambda match: match[0] + " " + dependency, text)
                    else:
                        text += "Requires.private: " + dependency + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")


def validate_prefix(root):
    prefix = root / "prefix"
    for name in REQUIRED_HEADERS:
        B.require_file(prefix / "include/glib-2.0" / name, "Installed GLib public header")
    B.require_file(prefix / "lib/glib-2.0/include/glibconfig.h", "Installed GLib configuration header")
    for name in REQUIRED_PC:
        B.require_file(prefix / "lib/pkgconfig" / name, "Installed GLib package metadata")
    static = {path.relative_to(prefix).as_posix(): M.inspect_static_archive(path) for path in sorted(prefix.rglob("*.a"))}
    if set(static) != {"lib/" + name for name in REQUIRED_ARCHIVES}:
        raise BuildError("GLib prefix must contain exactly GLib, GObject, GIO, GModule and GThread static archives.")
    probe = B.require_file(root / ARTIFACTS[0], "Installed GLib probe")
    elf = B.inspect_shared_elf(probe.read_bytes())
    if (elf["interpreter"] != "/system/bin/linker64" or elf["android_api_notes"] != [23]
            or not {"libc.so", "libz.so"}.issubset(elf["needed"])
            or not set(elf["needed"]).issubset({"libc.so", "libm.so", "libdl.so", "libz.so"})):
        raise BuildError("GLib probe must be Android x86_64/API23 with NDK system dependencies and system zlib.")
    files = M.inventory(root)
    for name, record in files.items():
        if record["bytes"] == 0 or name.endswith((".so", ".la")) or ".so." in name:
            raise BuildError(f"Unexpected empty/shared/libtool file in GLib prefix: {name}")
        if name.endswith(".pc"):
            content = (root / name).read_text(encoding="utf-8")
            if "prefix=${pcfiledir}/../.." not in content or re.search(r"[A-Za-z]:[/\\]|/(?:home|workspace|tmp|opt)/", content):
                raise BuildError(f"GLib package metadata retains a build-host path: {name}")
    return {"static_libraries": static, "runtime": {ARTIFACTS[0]: elf}, "zlib_backend": "NDK API23 system libz.so"}, files


def verify_receipt(repo):
    profile, inputs, _, pins = selected_inputs(repo)
    prior = prior_inputs(repo)
    root = repo / "build/android-glib"
    B.reject_link(root.parent)
    B.reject_link(root)
    receipt = B.read_json(B.managed_path(root, "receipt.json"))
    validation, files = validate_prefix(root)
    if (receipt.get("schema_version") != 1 or receipt.get("kind") != KIND or receipt.get("profile") != profile
            or receipt.get("inputs") != {"project_files": inputs, "archives": pins, "prerequisites": prior}
            or receipt.get("prefix_files") != files or receipt.get("validation") != validation
            or receipt.get("artifacts") != {name: files[name] for name in ARTIFACTS}):
        raise BuildError("GLib receipt/input/artifact hashes differ. Preserve local changes before rebuilding.")
    print("ANDROID GLIB ARTIFACTS VERIFIED", flush=True)
    return receipt


def build(repo, args):
    profile, inputs, package, pins = selected_inputs(repo)
    prior = prior_inputs(repo)
    directory = args.archive_directory
    if directory is None and T.is_msys() and (Path("/tmp/android-dl") / ARCHIVE).exists():
        directory = Path("/tmp/android-dl")
    data = M.archive_data(repo, ARCHIVE, pins[ARCHIVE], directory)
    files, executables = archive_files(data)
    for name in package["license_files"]:
        if not files.get("sources/" + SOURCE + "/" + name):
            raise BuildError(f"GLib archive lacks its recorded license: {name}")
    root = repo / "build/android-glib"
    if re.search(r"[\s'\"$`#;&<>|?!*]", str(root)):
        raise BuildError("Use the existing workspace path without spaces or shell metacharacters; NDK paths may contain spaces.")
    env = G.isolated_environment()
    ndk_tools, tools, versions = T.tools_and_versions(args.ndk, env)
    marker = prepare_sources(root, files, executables, generated_files(root, repo, ndk_tools, tools), profile, pins, prior)
    verify_prefix(root)
    receipt_path = B.managed_path(root, "receipt.json")
    if receipt_path.exists():
        receipt_path.unlink()
    (root / "prefix").mkdir(exist_ok=True)
    save_prefix_state(root)
    dependencies = [repo / "build/android-text-base/prefix", repo / "build/android-glib-deps/prefix"]
    env.update(PKG_CONFIG_LIBDIR=os.pathsep.join(str(path / "lib/pkgconfig") for path in dependencies), PKG_CONFIG_PATH="")
    private = root / "build/glib"
    setup = [tools["meson"], "setup", private, root / "sources" / SOURCE, "--cross-file", root / "toolchain/android.ini", *package["meson_options"]]
    if (private / "meson-private/coredata.dat").exists():
        setup.append("--reconfigure")
    T.run(setup, env)
    T.run([tools["ninja"], "-C", private, "-j", str(args.jobs), *TARGETS], env)
    install_development_files(root)
    normalize_metadata(root, repo)
    for name in package["license_files"]:
        T.copy_file(root / "sources" / SOURCE / name, root / "prefix/share/licenses/wesnoth-glib" / name)
    save_prefix_state(root)
    probe = root / ARTIFACTS[0]
    probe.parent.mkdir(exist_ok=True)
    probe_env = dict(env, PKG_CONFIG_LIBDIR=os.pathsep.join([str(root / "prefix/lib/pkgconfig"), *(str(path / "lib/pkgconfig") for path in dependencies)]))
    flags = shlex.split(T.run([tools["pkg-config"], "--cflags", "--libs", "--static", "gio-2.0", "gobject-2.0", "gmodule-2.0", "gthread-2.0"], probe_env, capture=True))
    link_map = root / "build/probe.map"
    T.run([root / "toolchain/cc", "-std=c11", "-O2", "-fPIE", "-pie", repo / "tools/native-glib/probe.c", *flags, "-Xlinker", "-Map", "-Xlinker", link_map, "-o", probe], env)
    mapping = B.require_file(link_map, "GLib probe linker map").read_text(encoding="utf-8")
    for name in ("libglib-2.0.a", "libgobject-2.0.a", "libgio-2.0.a", "libgmodule-2.0.a", "libpcre2-8.a", "libffi.a", "libiconv.a"):
        if name + "(" not in mapping:
            raise BuildError(f"GLib probe link map does not identify archive members from {name}.")
    save_prefix_state(root)
    validation, records = validate_prefix(root)
    verify_sources(root, marker)
    if any(B.fingerprint(repo / name) != record for name, record in inputs.items()):
        raise BuildError("A GLib input changed during compilation. No success receipt written.")
    if prior_inputs(repo) != prior:
        raise BuildError("A completed prerequisite changed during GLib compilation. No success receipt written.")
    receipt = {"schema_version": 1, "kind": KIND, "profile": profile,
               "inputs": {"project_files": inputs, "archives": pins, "prerequisites": prior}, "tools": versions,
               "artifacts": {name: records[name] for name in ARTIFACTS}, "prefix_files": records,
               "validation": validation, "device_executed": False}
    with receipt_path.open("xb") as stream:
        stream.write(B.json_bytes(receipt))
    print("ANDROID GLIB BUILD PASS", flush=True)
    print(f"Prefix: {root / 'prefix'}\nReceipt: {receipt_path}", flush=True)
    print("GLib libraries and probe built offline; Android execution remains untested.", flush=True)
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
            args.ndk = T.input_path(args.ndk)
            args.archive_directory = T.input_path(args.archive_directory) if args.archive_directory else None
            build(REPO, args)
    except (BuildError, OSError, ValueError, tarfile.TarError) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
