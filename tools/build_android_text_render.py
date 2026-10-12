#!/usr/bin/env python3
"""Build cached Android Expat, Fontconfig and Cairo without changing prior stages."""

import argparse
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]
def load_helper(name, filename):
    spec = importlib.util.spec_from_file_location(name, REPO / "tools" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
T = load_helper("wesnoth_render_text", "build_android_text_base.py")
F = load_helper("wesnoth_render_fonts", "build_android_fonts.py")
M, B = T.M, T.B
BuildError = B.BuildError
KIND = "wesnoth-android-text-render"
MARKER = ".wesnoth-android-text-render.json"
PREFIX_STATE = ".prefix-state.json"
PROJECT_INPUTS = tuple(dict.fromkeys((*T.PROJECT_INPUTS, *F.PROJECT_INPUTS,
    "tools/build_android_text_render.py", "tools/native-text-render/build-plan.json", "tools/native-text-render/probe.c")))
ARTIFACTS = ("prefix/bin/wesnoth-text-render-check",)
PACKAGES = (("expat", "expat-2.5.0.tar.xz", "expat-2.5.0"),
            ("fontconfig", "fontconfig-2.17.1.tar.xz", "fontconfig-2.17.1"),
            ("cairo", "cairo-1.16.0.tar.xz", "cairo-1.16.0"))
REQUIRED_ARCHIVES = ("libexpat.a", "libfontconfig.a", "libcairo.a")
REQUIRED_HEADERS = ("expat.h", "expat_external.h", "fontconfig/fontconfig.h", "fontconfig/fcfreetype.h",
                    "fontconfig/fcprivate.h", "cairo/cairo.h", "cairo/cairo-ft.h", "cairo/cairo-deprecated.h",
                    "cairo/cairo-features.h", "cairo/cairo-version.h")
REQUIRED_PC = ("expat.pc", "fontconfig.pc", "cairo.pc", "cairo-ft.pc", "cairo-fc.pc")


def font_call(function, *args):
    # The unchanged font helper loads its own bootstrap exception class.
    try:
        return function(*args)
    except F.BuildError as error:
        raise BuildError(str(error)) from error


def archive_files(data, source_dir):
    files, executables, seen, spelling, size = {}, [], set(), {}, 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        for entry in archive:
            name = entry.name.rstrip("/") if entry.isdir() else entry.name
            parts = M.safe_name(name)
            if parts[0] != source_dir:
                raise BuildError(f"Unexpected Text rendering archive root: {name}")
            for end in range(1, len(parts) + 1):
                component = "/".join(parts[:end])
                folded = component.casefold()
                if spelling.setdefault(folded, component) != component:
                    raise BuildError(f"Text rendering archive paths collide on Windows: {name}")
            if entry.isdir():
                continue
            if not entry.isfile() or len(parts) < 2 or name.casefold() in seen:
                raise BuildError(f"Linked, special or duplicate Text rendering archive member: {name}")
            seen.add(name.casefold())
            size += entry.size
            if size > 140_000_000 or len(seen) > 7000:
                raise BuildError("Text rendering source exceeds its size/member limits.")
            stream = archive.extractfile(entry)
            if stream is None:
                raise BuildError(f"Cannot read Text rendering source member: {name}")
            content = stream.read()
            if len(content) != entry.size:
                raise BuildError(f"Incomplete Text rendering source member: {name}")
            relative = "sources/" + name
            files[relative] = content
            if entry.mode & 0o111:
                executables.append(relative)
    if "sources/" + source_dir + "/configure" not in files:
        raise BuildError(f"Text rendering source lacks its release configure script: {source_dir}")
    return files, executables


def apply_source_edits(package, files):
    for edit in package.get("source_edits", []):
        if package["name"] != "fontconfig" or edit["path"] != "meson.build":
            raise BuildError("Only the recorded Fontconfig Meson correction may change source bytes.")
        name = "sources/" + package["source_dir"] + "/" + edit["path"]
        before, after = edit["before"].encode(), edit["after"].encode()
        if not before or name not in files or files[name].count(before) != 1:
            raise BuildError(f"Pinned source does not match its recorded source fix: {name}")
        files[name] = files[name].replace(before, after, 1)

def isolated_environment():
    env = T.isolated_environment()
    for name in list(env):
        if (name.startswith(("ac_cv_", "am_cv_", "gl_cv_", "lt_cv_", "libffi_cv_", "cairo_cv_"))
                or name in ("CPP", "CXXCPP", "CCAS", "CCASFLAGS", "LIBS", "PKG_CONFIG",
                            "LIBICONV", "LTLIBICONV", "INTLLIBS", "LIBINTL", "LTLIBINTL")):
            env.pop(name, None)
    return env

def config_guess(source, package_root):
    for candidate in (source / "build-aux/config.guess", source / "config.guess",
                      package_root / "conftools/config.guess", package_root / "build/config.guess"):
        B.reject_link(candidate)
        if candidate.is_file():
            return B.require_file(candidate, "Release config.guess")
    raise BuildError(f"Source release has no config.guess: {package_root.name}")

def write_pc(directory, name, library, version, extra=""):
    (directory / (name + ".pc")).write_text(
        "prefix=${pcfiledir}/../..\nlibdir=${prefix}/lib\nincludedir=${prefix}/include\n"
        f"Name: {name}\nDescription: Android static {name} library\nVersion: {version}\n"
        f"Libs: -L${{libdir}} -l{library}\nCflags: -I${{includedir}}\n{extra}", encoding="utf-8", newline="\n")

def verify_sources(root, marker):
    if (marker.get("schema_version") != 1 or marker.get("kind") != KIND
            or M.inventory(root, "sources") != marker.get("source_files")
            or M.inventory(root, "toolchain") != marker.get("generated_files")):
        raise BuildError("Managed text-render source/toolchain files were changed, added or removed. Nothing overwritten.")

def prepare_sources(root, files, executables, generated, profile, pins, prior):
    B.reject_link(root.parent)
    B.reject_link(root)
    records = lambda data: {name: {"bytes": len(value), "sha256": B.sha256(value)} for name, value in sorted(data.items())}
    marker = {"schema_version": 1, "kind": KIND, "profile": profile, "archives": pins, "prerequisites": prior,
              "source_files": records(files), "generated_files": records(generated)}
    if root.exists():
        path = B.managed_path(root, MARKER)
        if not path.is_file() or B.read_json(path) != marker:
            raise BuildError("Existing text-render directory is unmanaged or uses different source/toolchain inputs. Nothing overwritten.")
        verify_sources(root, marker)
        for relative in ("build", "prefix"):
            directory = root / relative
            B.reject_link(directory)
            for child in directory.rglob("*"):
                # Only these exact private Libtool aliases are permitted.
                aliases = {
                    "build/expat/lib/.libs/libexpat.la": "../libexpat.la",
                    "build/expat/lib/.libs/libexpatinternal.la": "../libexpatinternal.la",
                    "build/cairo/src/.libs/libcairo.la": "../libcairo.la",
                }
                expected = aliases.get(child.relative_to(root).as_posix())
                if child.is_symlink() and expected is not None and os.readlink(child) == expected:
                    target = child.parent / expected
                    B.reject_link(target)
                    B.require_file(target, "Known private Libtool alias target")
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
        os.utime(path, (1577836800, 1577836800))
    (root / MARKER).write_bytes(B.json_bytes(marker))
    return marker

def verify_prefix(root):
    files = M.inventory(root)
    path = B.managed_path(root, PREFIX_STATE)
    if path.exists():
        if B.read_json(path) != {"schema_version": 1, "kind": KIND, "prefix_files": files}:
            raise BuildError("Installed text-render prefix changed or an install was interrupted. Nothing overwritten.")
    elif files:
        raise BuildError("Existing text-render prefix has no owned file record. Nothing overwritten.")
    return files

def save_prefix_state(root):
    files = M.inventory(root)
    (root / PREFIX_STATE).write_bytes(B.json_bytes({"schema_version": 1, "kind": KIND, "prefix_files": files}))
    return files


def selected_inputs(repo):
    profile, _, _, _ = T.selected_inputs(repo)
    font_call(F.selected_inputs, repo)
    inputs = {name: B.fingerprint(B.require_file(repo / name, "Text rendering input")) for name in PROJECT_INPUTS}
    plan = B.read_json(repo / "tools/native-text-render/build-plan.json")
    packages = plan.get("packages", [])
    if (plan.get("schema_version") != 1 or not isinstance(packages, list)
            or [(p.get("name"), p.get("archive"), p.get("source_dir")) for p in packages] != list(PACKAGES)):
        raise BuildError("The rendering plan must select cached Expat, Fontconfig and Cairo in dependency order.")
    for package in packages:
        key = "meson_options" if package["name"] == "fontconfig" else "configure_options"
        options = package.get(key)
        if (not isinstance(options, list) or not options
                or not all(isinstance(x, str) and x.startswith("-") and "\n" not in x for x in options)
                or not isinstance(package.get("license_files"), list) or not package["license_files"]):
            raise BuildError("Unrecognized rendering options/licenses.")
        for name in package["license_files"]:
            M.safe_name(name)
        edits = package.get("source_edits", [])
        if (not isinstance(edits, list) or len(edits) != (1 if package["name"] == "fontconfig" else 0)
                or any(not isinstance(edit, dict) or set(edit) != {"path", "before", "after", "reason"}
                       or edit["path"] != "meson.build"
                       or not all(isinstance(edit[key], str) and edit[key] for key in edit) for edit in edits)):
            raise BuildError("The rendering plan must retain only its one recorded Fontconfig iconv correction.")
        required = ({"--wrap-mode=nodownload", "-Ddefault_library=static", "-Db_staticpic=true", "-Dtests=disabled",
                     "-Dtools=disabled", "-Dcache-build=disabled", "-Ddoc=disabled", "-Dnls=disabled", "-Dfontations=disabled",
                     "-Dxml-backend=expat", "-Diconv=enabled", "-Ddefault-fonts-dirs=[]", "-Dadditional-fonts-dirs=[]"}
                    if key == "meson_options" else {"--disable-shared", "--enable-static", "--with-pic"})
        if package["name"] == "cairo":
            required.update(("--enable-ft", "--enable-fc", "--disable-png", "--disable-ps", "--disable-pdf", "--disable-svg",
                             "--disable-xlib", "--disable-xcb", "--disable-gl", "--disable-gobject", "--disable-script", "--disable-interpreter"))
        if not required.issubset(options):
            raise BuildError("The rendering plan must retain static isolated library-only features.")
    pins, manifest = {}, B.read_json(repo / "tools/android-native-archives.json")
    for _, archive, _ in PACKAGES:
        entries = [x for x in manifest.get("archives", []) if x.get("name") == archive]
        if (len(entries) != 1 or type(entries[0].get("bytes")) is not int or not 0 < entries[0]["bytes"] < 50_000_000
                or not re.fullmatch(r"[0-9a-f]{64}", str(entries[0].get("sha256", "")))):
            raise BuildError(f"Missing or invalid rendering source pin: {archive}")
        pins[archive] = {key: entries[0][key] for key in ("bytes", "sha256")}
    return profile, inputs, packages, pins


def verify_font_receipt(repo):
    """Validate the existing font contract without an NDK or external tools."""
    profile, inputs, _, pins = font_call(F.selected_inputs, repo)
    root = repo / "build/android-fonts"
    B.reject_link(root.parent)
    B.reject_link(root)
    receipt = B.read_json(B.managed_path(root, "receipt.json"))
    for name in F.REQUIRED_HEADERS:
        B.require_file(root / "prefix/include" / name, "Completed font header")
    static = {path.relative_to(root / "prefix").as_posix(): M.inspect_static_archive(path)
              for path in sorted((root / "prefix").rglob("*.a"))}
    if set(static) != {"lib/" + name for name in F.REQUIRED_ARCHIVES}:
        raise BuildError("Completed font prefix has unexpected static archives.")
    probe = B.require_file(root / F.ARTIFACTS[0], "Completed font probe")
    runtime = B.require_file(root / F.ARTIFACTS[1], "Completed font runtime")
    validation = {"static_libraries": static, "runtime": {
        F.ARTIFACTS[0]: font_call(F.inspect_probe, probe), F.ARTIFACTS[1]: B.inspect_shared_elf(runtime.read_bytes())}}
    files = M.inventory(root)
    shared = {name for name in files if name.endswith(".so") or ".so." in name}
    if shared != {F.ARTIFACTS[1]} or any(record["bytes"] == 0 for record in files.values()):
        raise BuildError("Completed font prefix contains unexpected shared or empty files.")
    for name in files:
        if name.endswith((".pc", ".cmake")):
            text = (root / name).read_text(encoding="utf-8")
            if re.search(r"\b[A-Za-z]:[/\\]|/(?:home|workspace|tmp|opt)/", text):
                raise BuildError(f"Completed font metadata retains a build-host path: {name}")
    if (receipt.get("schema_version") != 1 or receipt.get("kind") != F.KIND or receipt.get("profile") != profile
            or receipt.get("inputs") != {"project_files": inputs, "archives": pins}
            or receipt.get("prefix_files") != files or receipt.get("validation") != validation
            or receipt.get("artifacts") != {name: files[name] for name in F.ARTIFACTS}):
        raise BuildError("Font receipt/input/artifact hashes differ. Preserve prior files.")
    print("ANDROID FONTS ARTIFACTS VERIFIED", flush=True)
    return receipt


def prior_inputs(repo):
    text, fonts = T.verify_receipt(repo), verify_font_receipt(repo)
    return {key: {"receipt": B.fingerprint(B.require_file(repo / "build" / stage / "receipt.json", "Prerequisite receipt")),
                  "prefix_files": receipt["prefix_files"]}
            for key, stage, receipt in (("text_base", "android-text-base", text), ("fonts", "android-fonts", fonts))}


def generated_files(root, repo, ndk_tools, tools):
    generated = T.generated_files(root, ndk_tools, tools)
    generated["toolchain/cxx"] = ("#!/usr/bin/env bash\nexec " + shlex.join(
        [str(ndk_tools["clang++"]), "--target=x86_64-linux-android23"]) + ' "$@"\n').encode()
    prefixes = (root / "prefix", repo / "build/android-fonts/prefix", repo / "build/android-text-base/prefix")
    cross = "[binaries]\n"
    for key, name in (("c", "cc"), ("cpp", "cxx"), ("ar", "ar"), ("strip", "strip")):
        cross += f"{key} = [{str(root / 'toolchain' / name)!r}]\n"
    cross += f"pkg-config = [{str(tools['pkg-config'])!r}]\n"
    cross += "[host_machine]\nsystem = 'android'\ncpu_family = 'x86_64'\ncpu = 'x86_64'\nendian = 'little'\n"
    cross += "[properties]\nneeds_exe_wrapper = true\n"
    cross += f"pkg_config_libdir = {[str(path / 'lib/pkgconfig') for path in prefixes]!r}\n[built-in options]\n"
    text = prefixes[-1]
    cross += f"c_args = {['-O2', '-fPIC', '-I' + str(text / 'include')]!r}\n"
    cross += f"c_link_args = {['-L' + str(text / 'lib')]!r}\n"
    generated["toolchain/android.ini"] = cross.encode()
    return generated


def normalize_pc(path):
    text = B.require_file(path, "Rendering package metadata").read_text(encoding="utf-8")
    text = re.sub(r"(?m)^prefix=.*$", "prefix=${pcfiledir}/../..", text)
    if path.name == "fontconfig.pc":
        # Meson's builtin iconv lookup emits -liconv without its include/lib
        # directory; the existing package supplies both transitively.
        if re.search(r"(?m)^Requires.private:", text):
            text = re.sub(r"(?m)^Requires.private:.*$", lambda m: m[0] + " iconv", text)
        else:
            text += "Requires.private: iconv\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def validate_prefix(root):
    prefix = root / "prefix"
    for name in REQUIRED_HEADERS:
        B.require_file(prefix / "include" / name, "Rendering public header")
    for name in REQUIRED_PC:
        B.require_file(prefix / "lib/pkgconfig" / name, "Rendering package metadata")
    static = {path.relative_to(prefix).as_posix(): M.inspect_static_archive(path) for path in sorted(prefix.rglob("*.a"))}
    if set(static) != {"lib/" + name for name in REQUIRED_ARCHIVES}:
        raise BuildError("Rendering prefix must contain exactly Expat, Fontconfig and Cairo static archives.")
    elf = B.inspect_shared_elf(B.require_file(root / ARTIFACTS[0], "Rendering probe").read_bytes())
    if (elf["interpreter"] != "/system/bin/linker64" or elf["android_api_notes"] != [23]
            or "libc.so" not in elf["needed"] or not set(elf["needed"]).issubset({"libc.so", "libm.so", "libdl.so"})):
        raise BuildError("Rendering probe must be Android x86_64/API23 with only its NDK C system dependencies.")
    files = M.inventory(root)
    for name, record in files.items():
        if record["bytes"] == 0 or name.endswith((".so", ".la")) or ".so." in name:
            raise BuildError(f"Unexpected empty/shared/libtool file in rendering prefix: {name}")
        if name.endswith(".pc"):
            text = (root / name).read_text(encoding="utf-8")
            if "prefix=${pcfiledir}/../.." not in text or re.search(r"\b[A-Za-z]:[/\\]|/(?:home|workspace|tmp|opt)/", text):
                raise BuildError(f"Rendering metadata retains a build-host path: {name}")
    return {"static_libraries": static, "runtime": {ARTIFACTS[0]: elf}}, files


def verify_receipt(repo):
    profile, inputs, _, pins = selected_inputs(repo)
    prior = prior_inputs(repo)
    root = repo / "build/android-text-render"
    B.reject_link(root.parent)
    B.reject_link(root)
    receipt = B.read_json(B.managed_path(root, "receipt.json"))
    validation, files = validate_prefix(root)
    if (receipt.get("schema_version") != 1 or receipt.get("kind") != KIND or receipt.get("profile") != profile
            or receipt.get("inputs") != {"project_files": inputs, "archives": pins, "prerequisites": prior}
            or receipt.get("prefix_files") != files or receipt.get("validation") != validation
            or receipt.get("artifacts") != {name: files[name] for name in ARTIFACTS}):
        raise BuildError("Rendering receipt/input/artifact hashes differ. Preserve local changes before rebuilding.")
    print("ANDROID TEXT RENDER ARTIFACTS VERIFIED", flush=True)
    return receipt

def run_cairo_configure(command, env, root):
    """Retain diagnostics and retry only Cairo's observed Bash temp-file failure."""
    private = B.managed_path(root, "build/cairo")
    attempts = B.managed_path(root, "build/cairo-configure-attempts")
    attempts.mkdir(parents=True, exist_ok=True)
    for number in range(1, 4):
        attempt = Path(tempfile.mkdtemp(prefix="attempt-", dir=attempts))
        temp = attempt / "tmp"
        temp.mkdir()
        attempt_env = dict(env, TMPDIR=str(temp))
        log = attempt / "configure.log"
        print(f"Cairo configure attempt {number}/3; retained log: {log}; TMPDIR: {temp}", flush=True)
        print("RUN: " + shlex.join([str(arg) for arg in command]), flush=True)
        busy, write_failed = False, False
        with log.open("x", encoding="utf-8", newline="\n") as output:
            process = subprocess.Popen([str(arg) for arg in command], cwd=private, env=attempt_env,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, encoding="utf-8", errors="replace", bufsize=1)
            try:
                for line in process.stdout:
                    print(line, end="", flush=True)
                    output.write(line)
                    output.flush()
                    message = line.rstrip("\r\n")
                    busy |= re.fullmatch(r".+: line [0-9]+: cannot create temp file for here-document: Device or resource busy", message) is not None
                    write_failed |= message == "configure: error: write failure creating ./config.status"
                status = process.wait()
            finally:
                process.stdout.close()
                if process.poll() is None:
                    process.kill()
                    process.wait()
        if status == 0:
            return
        for name in ("config.log", "config.status"):
            source = B.managed_path(root, "build/cairo/" + name)
            if source.exists():
                if not source.is_file():
                    raise BuildError(f"Cairo diagnostic is not a regular file; nothing copied: {source}")
                with (attempt / name).open("xb") as snapshot:
                    snapshot.write(source.read_bytes())
        if not (busy and write_failed) or number == 3:
            raise BuildError(f"Cairo configure failed with exit {status}. Logs and temporary files retained in {attempts}.")
        print(f"Observed Cairo here-document busy temporary-file failure; retrying configure in {number}s with a fresh TMPDIR.", flush=True)
        time.sleep(number)


def build(repo, args):
    profile, inputs, packages, pins = selected_inputs(repo)
    prior = prior_inputs(repo)
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
                raise BuildError(f"Archive lacks its declared license: {name}")
    root = repo / "build/android-text-render"
    if re.search(r"[\s'\"$`#;&<>|?!*]", str(root)):
        raise BuildError("Use the existing workspace without spaces or shell metacharacters; NDK paths may contain spaces.")
    env = isolated_environment()
    ndk_tools, tools, versions = T.tools_and_versions(args.ndk, env)
    marker = prepare_sources(root, files, executables, generated_files(root, repo, ndk_tools, tools), profile, pins, prior)
    verify_prefix(root)
    receipt_path = B.managed_path(root, "receipt.json")
    if receipt_path.exists():
        receipt_path.unlink()
    prefix = root / "prefix"
    (prefix / "lib/pkgconfig").mkdir(parents=True, exist_ok=True)
    save_prefix_state(root)
    env.update(CC=str(root / "toolchain/cc"), CXX=str(root / "toolchain/cxx"),
               AR=str(root / "toolchain/ar"), RANLIB=str(root / "toolchain/ranlib"),
               NM=str(root / "toolchain/nm"), OBJDUMP=str(root / "toolchain/objdump"), STRIP=str(root / "toolchain/strip"),
               CFLAGS="-O2 -fPIC", CXXFLAGS="-O2 -fPIC", CONFIG_SHELL=str(tools["bash"]), PKG_CONFIG=str(tools["pkg-config"]),
               PKG_CONFIG_LIBDIR=os.pathsep.join(str(path / "lib/pkgconfig") for path in
                   (prefix, repo / "build/android-fonts/prefix", repo / "build/android-text-base/prefix")), PKG_CONFIG_PATH="")
    for package in packages:
        name = package["name"]
        source = root / "sources" / package["source_dir"]
        private = root / "build" / name
        private.mkdir(parents=True, exist_ok=True)
        if name == "fontconfig":
            command = [tools["meson"], "setup", private, source, "--cross-file", root / "toolchain/android.ini", *package["meson_options"]]
            if (private / "meson-private/coredata.dat").exists():
                command.append("--reconfigure")
            T.run(command, env)
            T.run([tools["ninja"], "-C", private, "-j", str(args.jobs), "libfontconfig.a"], env)
            outputs = ((private / "libfontconfig.a", "lib/libfontconfig.a"),
                       (private / "fontconfig/fontconfig.h", "include/fontconfig/fontconfig.h"),
                       (source / "fontconfig/fcfreetype.h", "include/fontconfig/fcfreetype.h"),
                       (source / "fontconfig/fcprivate.h", "include/fontconfig/fcprivate.h"),
                       (private / "meson-private/fontconfig.pc", "lib/pkgconfig/fontconfig.pc"))
        else:
            triplet = T.run([tools["bash"], config_guess(source, source)], env, capture=True).strip()
            if not re.fullmatch(r"[A-Za-z0-9_.+-]+", triplet):
                raise BuildError("Unexpected config.guess build-machine identity.")
            command = [tools["bash"], source / "configure", "--host=x86_64-linux-android", "--build=" + triplet,
                       *package["configure_options"]]
            if name == "cairo":
                run_cairo_configure(command, env, root)
            else:
                T.run(command, env, private)
            subdir, target = ("lib", "libexpat.la") if name == "expat" else ("src", "libcairo.la")
            T.run([tools["make"], "-C", private / subdir, "-j", str(args.jobs), target], env)
            if name == "expat":
                outputs = ((private / "lib/.libs/libexpat.a", "lib/libexpat.a"),
                           (source / "lib/expat.h", "include/expat.h"),
                           (source / "lib/expat_external.h", "include/expat_external.h"))
            else:
                outputs = [(private / "src/.libs/libcairo.a", "lib/libcairo.a"),
                           (private / "src/cairo-features.h", "include/cairo/cairo-features.h"),
                           (source / "cairo-version.h", "include/cairo/cairo-version.h")]
                outputs += [(source / "src" / header, "include/cairo/" + header)
                            for header in ("cairo.h", "cairo-deprecated.h", "cairo-ft.h")]
                outputs += [(private / "src" / pc, "lib/pkgconfig/" + pc) for pc in ("cairo.pc", "cairo-ft.pc", "cairo-fc.pc")]
        for src, dest in outputs:
            T.copy_file(src, prefix / dest)
            if dest.endswith(".pc"):
                normalize_pc(prefix / dest)
        if name == "expat":
            write_pc(prefix / "lib/pkgconfig", "expat", "expat", "2.5.0", "Libs.private: -lm\n")
        for license_file in package["license_files"]:
            T.copy_file(source / license_file, prefix / "share/licenses/wesnoth-text-render" / name / license_file)
        save_prefix_state(root)
    probe = root / ARTIFACTS[0]
    probe.parent.mkdir(exist_ok=True)
    flags = shlex.split(T.run([tools["pkg-config"], "--cflags", "--libs", "--static", "cairo-ft", "fontconfig", "expat"], env, capture=True))
    link_map = root / "build/probe.map"
    T.run([root / "toolchain/cc", "-std=c11", "-O2", "-fPIE", "-pie", repo / "tools/native-text-render/probe.c",
           *flags, "-Xlinker", "-Map", "-Xlinker", link_map, "-o", probe], env)
    mapping = B.require_file(link_map, "Rendering linker map").read_text(encoding="utf-8")
    for name in (*REQUIRED_ARCHIVES, "libfreetype.a", "libpixman-1.a", "libiconv.a"):
        if name + "(" not in mapping:
            raise BuildError(f"Rendering probe link map has no members from {name}.")
    save_prefix_state(root)
    validation, records = validate_prefix(root)
    verify_sources(root, marker)
    if any(B.fingerprint(repo / name) != record for name, record in inputs.items()):
        raise BuildError("A rendering input changed during compilation. No success receipt written.")
    if prior_inputs(repo) != prior:
        raise BuildError("A completed prerequisite changed during compilation. No success receipt written.")
    receipt = {"schema_version": 1, "kind": KIND, "profile": profile,
               "inputs": {"project_files": inputs, "archives": pins, "prerequisites": prior}, "tools": versions,
               "prefix_files": records, "artifacts": {name: records[name] for name in ARTIFACTS},
               "validation": validation, "device_executed": False}
    with receipt_path.open("xb") as stream:
        stream.write(B.json_bytes(receipt))
    print("ANDROID TEXT RENDER BUILD PASS", flush=True)
    print(f"Prefix: {prefix}\nReceipt: {receipt_path}", flush=True)
    print("Rendering libraries built offline; Android execution remains untested.", flush=True)
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
