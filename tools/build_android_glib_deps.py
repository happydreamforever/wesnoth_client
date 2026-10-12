#!/usr/bin/env python3
"""Build cached GNU gettext runtime, PCRE2 and libffi for Android, without downloads.

The verified text-base prefix is read-only. Native Windows Python may verify
receipts; the build itself runs with MSYS Python on Windows or Linux Python.
"""

import argparse
import importlib.util
import io
import os
from pathlib import Path
import re
import shlex
import sys
import tarfile

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("wesnoth_glib_text_base", REPO / "tools/build_android_text_base.py")
T = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(T)
M, B = T.M, T.B
BuildError = B.BuildError
KIND = "wesnoth-android-glib-deps"
MARKER = ".wesnoth-android-glib-deps.json"
PREFIX_STATE = ".prefix-state.json"
FIXTURE = "tools/native-glib-deps/fixtures/locale/fr/LC_MESSAGES/wesnoth-glib-deps.mo"
PROJECT_INPUTS = (*T.PROJECT_INPUTS, "tools/build_android_glib_deps.py", "tools/native-glib-deps/build-plan.json",
                  "tools/native-glib-deps/probe.c", "tools/native-glib-deps/fixtures/catalog.po", FIXTURE)
PACKAGES = (("intl", "gettext-0.21.1.tar.gz", "gettext-0.21.1"),
            ("pcre2", "pcre2-10.42.tar.bz2", "pcre2-10.42"),
            ("ffi", "libffi-3.4.4.tar.gz", "libffi-3.4.4"))
ARTIFACTS = ("prefix/bin/wesnoth-glib-deps-check",
             "prefix/share/wesnoth-glib-deps/locale/fr/LC_MESSAGES/wesnoth-glib-deps.mo")
REQUIRED_ARCHIVES = ("libintl.a", "libpcre2-8.a", "libffi.a")
REQUIRED_HEADERS = ("libintl.h", "pcre2.h", "ffi.h", "ffitarget.h")
REQUIRED_PC = ("intl.pc", "libpcre2-8.pc", "libffi.pc")
PRIVATE_ALIASES = {
    "build/intl/intl/.libs/libintl.la": "../libintl.la",
    "build/pcre2/.libs/libpcre2-8.la": "../libpcre2-8.la",
    "build/ffi/.libs/libffi.la": "../libffi.la",
}


def selected_inputs(repo):
    profile, _, _, _ = T.selected_inputs(repo)
    inputs = {name: B.fingerprint(B.require_file(repo / name, "GLib dependency input")) for name in PROJECT_INPUTS}
    plan = B.read_json(repo / "tools/native-glib-deps/build-plan.json")
    packages = plan.get("packages", [])
    if (plan.get("schema_version") != 1 or not isinstance(packages, list)
            or [(p.get("name"), p.get("archive"), p.get("source_dir")) for p in packages] != list(PACKAGES)):
        raise BuildError("The GLib dependency plan must select gettext 0.21.1, PCRE2 10.42 and libffi 3.4.4.")
    for package, subdir in zip(packages, ("gettext-runtime", "", "")):
        options = package.get("configure_options")
        if (package.get("configure_subdir") != subdir or not isinstance(options, list)
                or not all(isinstance(x, str) and x.startswith("--") and "\n" not in x for x in options)
                or not {"--enable-static", "--disable-shared", "--with-pic", "--prefix=/usr", "--libdir=/usr/lib"}.issubset(options)
                or not isinstance(package.get("license_files"), list) or not package["license_files"]):
            raise BuildError("Unrecognized GLib dependency build options/licenses.")
        for name in package["license_files"]:
            M.safe_name(name)
        edits = package.get("source_edits", [])
        if not isinstance(edits, list) or (edits and package["name"] not in ("intl", "ffi")):
            raise BuildError("Only the recorded gettext/libffi source fixes are selected.")
        for edit in edits:
            if (not isinstance(edit, dict) or set(edit) != {"path", "before", "after"}
                    or not all(isinstance(value, str) and value for value in edit.values())):
                raise BuildError("Invalid recorded GLib dependency source edit.")
            M.safe_name(edit["path"])
    if "--disable-nls" in packages[0]["configure_options"] or "--with-included-gettext" not in packages[0]["configure_options"]:
        raise BuildError("The gettext stage must build the real included libintl with NLS enabled.")
    if not {"--enable-unicode", "--disable-jit", "--enable-pcre2-8", "--disable-pcre2-16", "--disable-pcre2-32"}.issubset(packages[1]["configure_options"]):
        raise BuildError("PCRE2 must use its Unicode-enabled 8-bit interpreter for this checkpoint.")
    manifest, pins = B.read_json(repo / "tools/android-native-archives.json"), {}
    for _, name, _ in PACKAGES:
        entries = [entry for entry in manifest.get("archives", []) if entry.get("name") == name]
        if (len(entries) != 1 or type(entries[0].get("bytes")) is not int
                or not 0 < entries[0]["bytes"] < 50_000_000
                or not re.fullmatch(r"[0-9a-f]{64}", str(entries[0].get("sha256", "")))):
            raise BuildError(f"Invalid or missing GLib source archive pin: {name}")
        pins[name] = {key: entries[0][key] for key in ("bytes", "sha256")}
    return profile, inputs, packages, pins


def text_base_inputs(repo):
    receipt = T.verify_receipt(repo)
    return {"receipt": B.fingerprint(B.require_file(repo / "build/android-text-base/receipt.json", "Text base receipt")),
            "prefix_files": receipt["prefix_files"]}


def archive_files(data, source_dir):
    files, executables, seen, spelling, size = {}, [], set(), {}, 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        for entry in archive:
            name = entry.name.rstrip("/") if entry.isdir() else entry.name
            parts = M.safe_name(name)
            if parts[0] != source_dir:
                raise BuildError(f"Unexpected GLib dependency archive root: {name}")
            for end in range(1, len(parts) + 1):
                component = "/".join(parts[:end])
                folded = component.casefold()
                if spelling.setdefault(folded, component) != component:
                    raise BuildError(f"GLib dependency archive paths collide on Windows: {name}")
            if entry.isdir():
                continue
            if not entry.isfile() or len(parts) < 2 or name.casefold() in seen:
                raise BuildError(f"Linked, special or duplicate GLib dependency archive member: {name}")
            seen.add(name.casefold())
            size += entry.size
            if size > 140_000_000 or len(seen) > 7000:
                raise BuildError("GLib dependency source exceeds its size/member limits.")
            stream = archive.extractfile(entry)
            if stream is None:
                raise BuildError(f"Cannot read GLib dependency source member: {name}")
            content = stream.read()
            if len(content) != entry.size:
                raise BuildError(f"Incomplete GLib dependency source member: {name}")
            relative = "sources/" + name
            files[relative] = content
            if entry.mode & 0o111:
                executables.append(relative)
    if "sources/" + source_dir + "/configure" not in files:
        raise BuildError(f"GLib dependency source lacks its release configure script: {source_dir}")
    return files, executables


def generated_files(root, ndk_tools, tools):
    generated = T.generated_files(root, ndk_tools, tools)
    # gettext embeds target locale defaults; never let MSYS rewrite them to a
    # build-host drive while preserving normal conversion of -I/-L paths.
    if T.is_msys():
        generated["toolchain/cc"] = generated["toolchain/cc"].replace(
            b"-DLIBDIR=;-DINSTALLDIR=", b"-DLIBDIR=;-DINSTALLDIR=;-DLOCALEDIR=;-DLOCALE_ALIAS_PATH=")
    generated["toolchain/cxx"] = ("#!/usr/bin/env bash\nexec " + shlex.join(
        [str(ndk_tools["clang++"]), "--target=x86_64-linux-android23"]) + ' "$@"\n').encode()
    return generated


def isolated_environment():
    env = T.isolated_environment()
    for name in list(env):
        if (name.startswith(("ac_cv_", "am_cv_", "gl_cv_", "lt_cv_", "libffi_cv_"))
                or name in ("CPP", "CXXCPP", "CCAS", "CCASFLAGS", "LIBS", "PKG_CONFIG",
                            "LIBICONV", "LTLIBICONV", "INTLLIBS", "LIBINTL", "LTLIBINTL")):
            env.pop(name, None)
    return env


def apply_source_edits(package, files):
    for edit in package.get("source_edits", []):
        name = "sources/" + package["source_dir"] + "/" + edit["path"]
        before, after = edit["before"].encode(), edit["after"].encode()
        if name not in files or files[name].count(before) != 1:
            raise BuildError(f"Pinned source does not match its recorded source fix: {name}")
        files[name] = files[name].replace(before, after, 1)


def verify_sources(root, marker):
    if (marker.get("schema_version") != 1 or marker.get("kind") != KIND
            or M.inventory(root, "sources") != marker.get("source_files")
            or M.inventory(root, "toolchain") != marker.get("generated_files")):
        raise BuildError("Managed GLib dependency source/toolchain files were changed, added or removed. Nothing overwritten.")


def prepare_sources(root, files, executables, generated, profile, pins, upstream):
    B.reject_link(root.parent)
    B.reject_link(root)
    records = lambda data: {name: {"bytes": len(value), "sha256": B.sha256(value)} for name, value in sorted(data.items())}
    marker = {"schema_version": 1, "kind": KIND, "profile": profile, "archives": pins, "text_base": upstream,
              "source_files": records(files), "generated_files": records(generated)}
    if root.exists():
        path = B.managed_path(root, MARKER)
        if not path.is_file() or B.read_json(path) != marker:
            raise BuildError("Existing GLib dependency directory is unmanaged or uses different source/toolchain inputs. Nothing overwritten.")
        verify_sources(root, marker)
        for relative in ("build", "prefix"):
            directory = root / relative
            B.reject_link(directory)
            for child in directory.rglob("*"):
                child_name = child.relative_to(root).as_posix()
                expected = PRIVATE_ALIASES.get(child_name)
                # PCRE2's release configure points its private generated name
                # at the bundled tables. Accept only this exact owned source;
                # no other source/build/prefix links are permitted.
                if child_name == "build/pcre2/src/pcre2_chartables.c":
                    expected = str(root / "sources/pcre2-10.42/src/pcre2_chartables.c.dist")
                if child.is_symlink() and expected is not None and os.readlink(child) == expected:
                    B.reject_link(child.parent / expected)
                    B.require_file(child.parent / expected, "Known private libtool alias target")
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
        # Release-generated configure/Makefile files must not look older than
        # their input templates merely because extraction happened in order.
        os.utime(path, (1577836800, 1577836800))
    (root / MARKER).write_bytes(B.json_bytes(marker))
    return marker


def verify_prefix(root):
    files = M.inventory(root)
    path = B.managed_path(root, PREFIX_STATE)
    if path.exists():
        if B.read_json(path) != {"schema_version": 1, "kind": KIND, "prefix_files": files}:
            raise BuildError("Installed GLib dependency prefix changed or an install was interrupted. Nothing overwritten.")
    elif files:
        raise BuildError("Existing GLib dependency prefix has no owned file record. Nothing overwritten.")
    return files


def save_prefix_state(root):
    files = M.inventory(root)
    (root / PREFIX_STATE).write_bytes(B.json_bytes({"schema_version": 1, "kind": KIND, "prefix_files": files}))
    return files


def write_pc(directory, name, library, version, extra=""):
    (directory / (name + ".pc")).write_text(
        "prefix=${pcfiledir}/../..\nlibdir=${prefix}/lib\nincludedir=${prefix}/include\n"
        f"Name: {name}\nDescription: Android static {name} library\nVersion: {version}\n"
        f"Libs: -L${{libdir}} -l{library}\nCflags: -I${{includedir}}\n{extra}", encoding="utf-8", newline="\n")


def config_guess(source, package_root):
    # gettext-runtime shares the release's top-level build-aux directory.
    for candidate in (source / "build-aux/config.guess", source / "config.guess",
                      package_root / "build-aux/config.guess", package_root / "config.guess"):
        B.reject_link(candidate)
        if candidate.is_file():
            return B.require_file(candidate, "Release config.guess")
    raise BuildError(f"Source release has no config.guess: {package_root.name}")


def validate_prefix(root):
    prefix = root / "prefix"
    for name in REQUIRED_HEADERS:
        B.require_file(prefix / "include" / name, "Installed GLib dependency header")
    for name in REQUIRED_PC:
        B.require_file(prefix / "lib/pkgconfig" / name, "Installed GLib dependency metadata")
    static = {path.relative_to(prefix).as_posix(): M.inspect_static_archive(path) for path in sorted(prefix.rglob("*.a"))}
    if set(static) != {"lib/" + name for name in REQUIRED_ARCHIVES}:
        raise BuildError("The GLib dependency prefix must contain exactly libintl, PCRE2-8 and libffi static archives.")
    probe = B.require_file(root / ARTIFACTS[0], "Installed GLib dependency probe")
    elf = B.inspect_shared_elf(probe.read_bytes())
    if (elf["interpreter"] != "/system/bin/linker64" or elf["android_api_notes"] != [23]
            or "libc.so" not in elf["needed"] or not set(elf["needed"]).issubset({"libc.so", "libm.so", "libdl.so"})):
        raise BuildError("GLib dependency probe must be an Android x86_64/API23 C executable with only NDK system dependencies.")
    B.require_file(root / ARTIFACTS[1], "Installed gettext fixture")
    files = M.inventory(root)
    for name, record in files.items():
        if record["bytes"] == 0 or name.endswith((".so", ".la")) or ".so." in name:
            raise BuildError(f"Unexpected empty/shared/libtool file in GLib dependency prefix: {name}")
        if name.endswith(".pc"):
            content = (root / name).read_text(encoding="utf-8")
            if "prefix=${pcfiledir}/../.." not in content or re.search(r"[A-Za-z]:[/\\]|/(?:home|workspace|tmp|opt)/", content):
                raise BuildError(f"Installed GLib dependency metadata retains a build-host path: {name}")
    return {"static_libraries": static, "runtime": {ARTIFACTS[0]: elf}}, files


def verify_receipt(repo):
    profile, inputs, _, pins = selected_inputs(repo)
    upstream = text_base_inputs(repo)
    root = repo / "build/android-glib-deps"
    B.reject_link(root.parent)
    B.reject_link(root)
    receipt = B.read_json(B.managed_path(root, "receipt.json"))
    validation, files = validate_prefix(root)
    if (receipt.get("schema_version") != 1 or receipt.get("kind") != KIND or receipt.get("profile") != profile
            or receipt.get("inputs") != {"project_files": inputs, "archives": pins, "text_base": upstream}
            or receipt.get("prefix_files") != files or receipt.get("validation") != validation
            or receipt.get("artifacts") != {name: files[name] for name in ARTIFACTS}
            or files[ARTIFACTS[1]] != inputs[FIXTURE]):
        raise BuildError("GLib dependency receipt/input/artifact hashes differ. Preserve local changes before rebuilding.")
    print("ANDROID GLIB DEPS ARTIFACTS VERIFIED", flush=True)
    return receipt


def build(repo, args):
    profile, inputs, packages, pins = selected_inputs(repo)
    upstream = text_base_inputs(repo)
    text_prefix = repo / "build/android-text-base/prefix"
    files, executables = {}, []
    for package in packages:
        directory = args.archive_directory
        if directory is None and T.is_msys() and (Path("/tmp/android-dl") / package["archive"]).exists():
            directory = Path("/tmp/android-dl")
        data = M.archive_data(repo, package["archive"], pins[package["archive"]], directory)
        members, modes = archive_files(data, package["source_dir"])
        apply_source_edits(package, members)
        files.update(members)
        executables.extend(modes)
        for name in package["license_files"]:
            if not files.get("sources/" + package["source_dir"] + "/" + name):
                raise BuildError(f"Source archive lacks its declared license: {name}")
    root = repo / "build/android-glib-deps"
    if re.search(r"[\s'\"$`#;&<>|?!*]", str(root)):
        raise BuildError("This Autoconf stage requires a workspace path without spaces or shell metacharacters; NDK paths may contain spaces.")
    env = isolated_environment()
    ndk_tools, tools, versions = T.tools_and_versions(args.ndk, env)
    generated = generated_files(root, ndk_tools, tools)
    marker = prepare_sources(root, files, executables, generated, profile, pins, upstream)
    verify_prefix(root)
    receipt_path = B.managed_path(root, "receipt.json")
    if receipt_path.exists():
        receipt_path.unlink()
    prefix = root / "prefix"
    prefix.mkdir(exist_ok=True)
    pc_dir = prefix / "lib/pkgconfig"
    pc_dir.mkdir(parents=True, exist_ok=True)
    save_prefix_state(root)
    env.update(CC=str(root / "toolchain/cc"), CXX=str(root / "toolchain/cxx"),
               AR=str(root / "toolchain/ar"), RANLIB=str(root / "toolchain/ranlib"),
               NM=str(root / "toolchain/nm"), OBJDUMP=str(root / "toolchain/objdump"), STRIP=str(root / "toolchain/strip"),
               CFLAGS="-O2 -fPIC", CXXFLAGS="-O2 -fPIC", CONFIG_SHELL=str(tools["bash"]), PKG_CONFIG=str(tools["pkg-config"]))
    for package in packages:
        package_root = root / "sources" / package["source_dir"]
        source = package_root / package["configure_subdir"]
        private = root / "build" / package["name"]
        private.mkdir(parents=True, exist_ok=True)
        guess = config_guess(source, package_root)
        build_triplet = T.run([tools["bash"], guess], env, capture=True).strip()
        if not re.fullmatch(r"[A-Za-z0-9_.+-]+", build_triplet):
            raise BuildError("Unexpected config.guess build-machine identity.")
        options = list(package["configure_options"])
        package_env = dict(env)
        if package["name"] == "intl":
            options.append("--with-libiconv-prefix=" + str(text_prefix))
            package_env.update(CPPFLAGS="-I" + str(text_prefix / "include"),
                               LDFLAGS="-L" + str(text_prefix / "lib"))
        T.run([tools["bash"], source / "configure", "--host=x86_64-linux-android", "--build=" + build_triplet, *options], package_env, private)
        target, directory = {"intl": ("all", private / "intl"), "pcre2": ("libpcre2-8.la", private), "ffi": ("libffi.la", private)}[package["name"]]
        T.run([tools["make"], "-C", directory, "-j", str(args.jobs), target], package_env)
        outputs = {
            "intl": (("intl/.libs/libintl.a", "lib/libintl.a"), ("intl/libintl.h", "include/libintl.h")),
            "pcre2": ((".libs/libpcre2-8.a", "lib/libpcre2-8.a"), ("src/pcre2.h", "include/pcre2.h")),
            "ffi": ((".libs/libffi.a", "lib/libffi.a"), ("include/ffi.h", "include/ffi.h"), ("include/ffitarget.h", "include/ffitarget.h")),
        }[package["name"]]
        for src, dest in outputs:
            T.copy_file(private / src, prefix / dest)
        for name in package["license_files"]:
            T.copy_file(root / "sources" / package["source_dir"] / name,
                        prefix / "share/licenses/wesnoth-glib-deps" / package["name"] / name)
        save_prefix_state(root)
    write_pc(pc_dir, "intl", "intl", "0.21.1", "Requires.private: iconv\n")
    write_pc(pc_dir, "libpcre2-8", "pcre2-8", "10.42")
    write_pc(pc_dir, "libffi", "ffi", "3.4.4", "Libs.private: -ldl\n")
    T.copy_file(repo / FIXTURE, root / ARTIFACTS[1])
    save_prefix_state(root)
    probe = root / ARTIFACTS[0]
    probe.parent.mkdir(exist_ok=True)
    probe_env = dict(env, PKG_CONFIG_LIBDIR=os.pathsep.join((str(pc_dir), str(text_prefix / "lib/pkgconfig"))), PKG_CONFIG_PATH="")
    flags = shlex.split(T.run([tools["pkg-config"], "--cflags", "--libs", "--static", "intl", "libpcre2-8", "libffi"], probe_env, capture=True))
    link_map = root / "build/probe.map"
    T.run([root / "toolchain/cc", "-std=c11", "-O2", "-fPIE", "-pie", repo / "tools/native-glib-deps/probe.c",
           *flags, "-lm", "-Xlinker", "-Map", "-Xlinker", link_map, "-o", probe], env)
    mapping = B.require_file(link_map, "GLib dependency probe linker map").read_text(encoding="utf-8")
    for name in (*REQUIRED_ARCHIVES, "libiconv.a"):
        if name + "(" not in mapping:
            raise BuildError(f"Probe linker map does not identify members from {name}.")
    save_prefix_state(root)
    validation, records = validate_prefix(root)
    verify_sources(root, marker)
    if any(B.fingerprint(repo / name) != record for name, record in inputs.items()):
        raise BuildError("A GLib dependency build input changed during compilation. No success receipt written.")
    if text_base_inputs(repo) != upstream:
        raise BuildError("The completed text-base input changed during compilation. No success receipt written.")
    receipt = {"schema_version": 1, "kind": KIND, "profile": profile,
               "inputs": {"project_files": inputs, "archives": pins, "text_base": upstream}, "tools": versions,
               "artifacts": {name: records[name] for name in ARTIFACTS}, "prefix_files": records,
               "validation": validation, "device_executed": False}
    with receipt_path.open("xb") as stream:
        stream.write(B.json_bytes(receipt))
    print("ANDROID GLIB DEPS BUILD PASS", flush=True)
    print(f"Prefix: {prefix}\nReceipt: {receipt_path}", flush=True)
    print("Libraries and Android probe built offline; Android execution and GLib itself remain untested.", flush=True)
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
