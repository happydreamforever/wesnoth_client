#!/usr/bin/env python3
"""Build pinned FriBidi/Pango Android static libraries offline with prior receipts.

The new stage owns extraction, native generators and installed files. Completed
font, text-base, GLib prerequisites, GLib and rendering stages remain read-only.
"""
import argparse
import io
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
import tarfile
import importlib.util

REPO = Path(__file__).resolve().parents[1]
def load_helper(name, filename):
    spec = importlib.util.spec_from_file_location(name, REPO / "tools" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
R = load_helper("wesnoth_layout_render", "build_android_text_render.py")
G = load_helper("wesnoth_layout_glib", "build_android_glib.py")
T, M, B = R.T, R.M, R.B
BuildError = B.BuildError
KIND = "wesnoth-android-text-layout"
MARKER = ".wesnoth-android-text-layout.json"
PREFIX_STATE = ".prefix-state.json"
PROJECT_INPUTS = tuple(dict.fromkeys((*R.PROJECT_INPUTS, *G.PROJECT_INPUTS,
    "tools/build_android_text_layout.py", "tools/native-text-layout/build-plan.json",
    "tools/native-text-layout/sources.json", "tools/native-text-layout/probe.c")))
ARTIFACTS = ("prefix/bin/wesnoth-text-layout-check", "prefix/lib/libc++_shared.so")
PACKAGES = (("fribidi", "fribidi-1.0.17.tar.xz", "fribidi-1.0.17"),
            ("pango", "pango-1.50.14.tar.xz", "pango-1.50.14"))
TARGETS = {"fribidi": ["lib/libfribidi.a"], "pango": ["pango/libpango-1.0.a", "pango/libpangoft2-1.0.a", "pango/libpangocairo-1.0.a"]}
REQUIRED_ARCHIVES = tuple(Path(name).name for targets in TARGETS.values() for name in targets)
REQUIRED_HEADERS = ("fribidi/fribidi.h", "fribidi/fribidi-config.h", "fribidi/fribidi-unicode-version.h",
                    "pango-1.0/pango/pango.h", "pango-1.0/pango/pango-layout.h", "pango-1.0/pango/pango-enum-types.h",
                    "pango-1.0/pango/pango-features.h", "pango-1.0/pango/pangoft2.h",
                    "pango-1.0/pango/pangofc-fontmap.h", "pango-1.0/pango/pangocairo.h")
REQUIRED_PC = ("fribidi.pc", "pango.pc", "pangoft2.pc", "pangofc.pc", "pangoot.pc", "pangocairo.pc")
COMMON_OPTIONS = ["--prefix=/usr", "--libdir=lib", "--wrap-mode=nodownload", "-Ddefault_library=static", "-Db_staticpic=true", "-Dbuildtype=release", "-Doptimization=2"]
OPTIONS = {"fribidi": COMMON_OPTIONS + ["-Ddocs=false", "-Dbin=false", "-Dtests=false"],
           "pango": COMMON_OPTIONS + ["-Dgtk_doc=false", "-Dintrospection=disabled", "-Dinstall-tests=false", "-Dlibthai=disabled", "-Dxft=disabled", "-Dsysprof=disabled", "-Dfontconfig=enabled", "-Dfreetype=enabled", "-Dcairo=enabled"]}
OMITTED = {"fribidi": {"lib/fribidi-unicode-version.h", "lib/bidi-type.tab.i", "lib/joining-type.tab.i", "lib/arabic-shaping.tab.i", "lib/mirroring.tab.i", "lib/brackets.tab.i", "lib/brackets-type.tab.i"},
           "pango": {"docs/Pango/func.VERSION_CHECK.html", "docs/Pango/func.version_check.html"}}
GLIB_ARCHIVE = "glib-2.82.1.tar.xz"
STAGES = ("android-text-base", "android-fonts", "android-glib-deps", "android-glib", "android-text-render")


def glib_call(function, *args):
    try:
        return function(*args)
    except G.BuildError as error:
        raise BuildError(str(error)) from error


def selected_inputs(repo):
    profile, _, _, _ = R.selected_inputs(repo)
    glib_call(G.selected_inputs, repo)
    inputs = {name: B.fingerprint(B.require_file(repo / name, "Layout input")) for name in PROJECT_INPUTS}
    plan = B.read_json(repo / "tools/native-text-layout/build-plan.json")
    packages = plan.get("packages", [])
    if (plan.get("schema_version") != 1 or not isinstance(packages, list)
            or [(p.get("name"), p.get("archive"), p.get("source_dir")) for p in packages] != list(PACKAGES)):
        raise BuildError("Layout plan must select pinned FriBidi 1.0.17 and Pango 1.50.14.")
    for package in packages:
        name = package["name"]
        omit = package.get("omitted_generated_files", {})
        if (package.get("targets") != TARGETS[name] or package.get("meson_options") != OPTIONS[name]
                or package.get("license_files") != (["COPYING", "gen.tab/unidata/ReadMe.txt"] if name == "fribidi" else ["COPYING"])
                or not isinstance(omit, dict)
                or set(omit) != OMITTED[name]):
            raise BuildError("Layout plan must retain exact static targets, offline options and bounded generated omissions.")
        for relative, pin in omit.items():
            M.safe_name(relative)
            if not valid_pin(pin):
                raise BuildError("Layout generated source omission lacks a valid size/hash pin.")
    host = plan.get("host_generator", {})
    if (host.get("archive") != GLIB_ARCHIVE or host.get("source_dir") != "glib-2.82.1"
            or host.get("path") != "gobject/glib-mkenums.in" or not valid_pin(host)
            or host.get("license") != "LICENSES/GPL-2.0-or-later.txt"):
        raise BuildError("Layout requires the pinned GLib host enum generator and license.")
    manifest = B.read_json(repo / "tools/android-native-archives.json")
    extra = B.read_json(repo / "tools/native-text-layout/sources.json")
    if extra.get("schema_version") != 1 or extra.get("kind") != "wesnoth-android-layout-sources":
        raise BuildError("Layout source manifest kind/schema is invalid.")
    pins = {}
    for archive in (PACKAGES[0][1], PACKAGES[1][1], GLIB_ARCHIVE):
        source = extra if archive == PACKAGES[0][1] else manifest
        candidates = [x for x in source.get("archives", []) if x.get("name") == archive]
        if len(candidates) != 1 or not valid_pin(candidates[0]):
            raise BuildError(f"Missing or invalid layout archive pin: {archive}")
        pins[archive] = {key: candidates[0][key] for key in ("bytes", "sha256")}
    return profile, inputs, plan, pins


def valid_pin(pin):
    return (isinstance(pin, dict) and type(pin.get("bytes")) is int and 0 < pin["bytes"] < 50_000_000
            and re.fullmatch(r"[0-9a-f]{64}", str(pin.get("sha256", ""))) is not None)


def prior_inputs(repo):
    render = R.verify_receipt(repo)
    glib = glib_call(G.verify_receipt, repo)
    return {key: {"receipt": B.fingerprint(B.require_file(repo / "build" / stage / "receipt.json", "Layout prerequisite receipt")),
                  "prefix_files": receipt["prefix_files"]}
            for key, stage, receipt in (("text_render", "android-text-render", render), ("glib", "android-glib", glib))}


def archive_files(data, package):
    files, executables, seen, spelling, omitted, total = {}, [], set(), {}, set(), 0
    source = package["source_dir"]
    omissions = package["omitted_generated_files"]
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        for entry in archive:
            name = entry.name.rstrip("/") if entry.isdir() else entry.name
            parts = M.safe_name(name)
            if parts[0] != source:
                raise BuildError(f"Unexpected layout archive root: {name}")
            relative = "/".join(parts[1:])
            if name in seen:
                raise BuildError(f"Duplicate layout source member: {name}")
            seen.add(name)
            if entry.isdir():
                # Colliding names are only allowed for the two omitted files.
                for end in range(1, len(parts) + 1):
                    component = "/".join(parts[:end])
                    if spelling.setdefault(component.casefold(), component) != component:
                        raise BuildError(f"Layout archive paths collide on Windows: {name}")
                continue
            if not entry.isfile() or len(parts) < 2:
                raise BuildError(f"Linked or special layout archive member: {name}")
            total += entry.size
            if total > 70_000_000 or len(seen) > 8000:
                raise BuildError("Layout archive exceeds source size/member limits.")
            stream = archive.extractfile(entry)
            content = stream.read() if stream is not None else b""
            if len(content) != entry.size:
                raise BuildError(f"Incomplete layout member: {name}")
            if relative in omissions:
                if {"bytes": len(content), "sha256": B.sha256(content)} != omissions[relative]:
                    raise BuildError(f"Generated source omission hash differs: {name}")
                omitted.add(relative)
                continue
            for end in range(1, len(parts) + 1):
                component = "/".join(parts[:end])
                if spelling.setdefault(component.casefold(), component) != component:
                    raise BuildError(f"Layout archive paths collide on Windows: {name}")
            owned = "sources/" + name
            files[owned] = content
            if entry.mode & 0o111:
                executables.append(owned)
    if omitted != set(omissions) or not files.get("sources/" + source + "/meson.build"):
        raise BuildError("Layout archive lacks its exact recorded omissions or build entry point.")
    if package["name"] == "fribidi" and not files.get("sources/" + source + "/lib/arabic-misc.tab.i"):
        raise BuildError("FriBidi must retain its hand-authored Arabic table.")
    # Meson opens an empty persistent POSIX lock in existing subprojects dirs.
    if any(name.startswith("sources/" + source + "/subprojects/") for name in files):
        lock = "sources/" + source + "/subprojects/.wraplock"
        if lock in files:
            raise BuildError("Archive must not supply the managed Meson lock.")
        files[lock] = b""
    return files, executables


def host_source_files(data, host):
    # The unchanged GLib extractor verifies its two known license aliases and
    # every source member before selecting only the host script and GPL text.
    files, _ = glib_call(G.archive_files, data)
    stem = "sources/" + host["source_dir"] + "/"
    script = files.get(stem + host["path"], b"")
    if {"bytes": len(script), "sha256": B.sha256(script)} != {key: host[key] for key in ("bytes", "sha256")}:
        raise BuildError("Pinned host glib-mkenums source differs.")
    license_bytes = files.get(stem + host["license"])
    if not license_bytes:
        raise BuildError("Pinned host glib-mkenums license is missing.")
    return {stem + host["path"]: script, stem + host["license"]: license_bytes}


def generated_files(root, repo, ndk_tools, tools, template):
    generated = T.generated_files(root, ndk_tools, tools)
    generated["toolchain/cxx"] = ("#!/usr/bin/env bash\nexec " + shlex.join([str(ndk_tools["clang++"]), "--target=x86_64-linux-android23"]) + ' "$@"\n').encode()
    if T.is_msys():
        for name in ("cc", "cxx"):
            conversion = "export MSYS2_ARG_CONV_EXCL='-DLIBDIR=;-DINSTALLDIR=;-DSYSCONFDIR='\n"
            lines = generated["toolchain/" + name].decode().splitlines(keepends=True)
            lines = [line for line in lines if not line.startswith("export MSYS2_ARG_CONV_EXCL=")]
            generated["toolchain/" + name] = (lines[0] + conversion + "".join(lines[1:])).encode()
    prefixes = [root / "prefix", *(repo / "build" / name / "prefix" for name in STAGES)]
    cross = "[binaries]\n"
    for key, name in (("c", "cc"), ("cpp", "cxx"), ("ar", "ar"), ("strip", "strip")):
        cross += f"{key} = [{str(root / 'toolchain' / name)!r}]\n"
    cross += f"pkg-config = [{str(tools['pkg-config'])!r}]\n"
    # Meson 1.12 find_tool consults HOST binaries even for native generators.
    # Explicitly invoke host Python; this is not an Android target executable.
    cross += f"glib-mkenums = [{sys.executable!r}, {str(root / 'toolchain/glib-mkenums.py')!r}]\n"
    cross += "[host_machine]\nsystem = 'android'\ncpu_family = 'x86_64'\ncpu = 'x86_64'\nendian = 'little'\n"
    cross += "[properties]\nneeds_exe_wrapper = true\n"
    cross += f"pkg_config_libdir = {[str(p / 'lib/pkgconfig') for p in prefixes]!r}\n[built-in options]\n"
    cross += "c_args = ['-O2', '-fPIC']\ncpp_args = ['-O2', '-fPIC']\n"
    generated["toolchain/android.ini"] = cross.encode()
    text = template.decode("utf-8")
    if text.count("@PYTHON@") != 1 or text.count("@VERSION@") != 1:
        raise BuildError("Unexpected host glib-mkenums template substitutions.")
    generated["toolchain/glib-mkenums.py"] = text.replace("@PYTHON@", sys.executable).replace("@VERSION@", "2.82.1").encode()
    native = f"[binaries]\nc = [{str(tools['host-cc'])!r}]\ncpp = [{str(tools['host-cxx'])!r}]\n"
    native += f"glib-mkenums = [{sys.executable!r}, {str(root / 'toolchain/glib-mkenums.py')!r}]\n"
    generated["toolchain/native.ini"] = native.encode()
    return generated


def verify_sources(root, marker):
    if (marker.get("schema_version") != 1 or marker.get("kind") != KIND
            or M.inventory(root, "sources") != marker.get("source_files")
            or M.inventory(root, "toolchain") != marker.get("generated_files")):
        raise BuildError("Managed layout source/toolchain files changed. Nothing overwritten.")


def prepare_sources(root, files, executables, generated, profile, pins, prior):
    B.reject_link(root.parent)
    B.reject_link(root)
    records = lambda data: {name: {"bytes": len(value), "sha256": B.sha256(value)} for name, value in sorted(data.items())}
    marker = {"schema_version": 1, "kind": KIND, "profile": profile, "archives": pins, "prerequisites": prior,
              "source_files": records(files), "generated_files": records(generated)}
    if root.exists():
        path = B.managed_path(root, MARKER)
        if not path.is_file() or B.read_json(path) != marker:
            raise BuildError("Existing layout directory is unmanaged or uses different inputs. Nothing overwritten.")
        verify_sources(root, marker)
        for relative in ("build", "prefix"):
            B.reject_link(root / relative)
            for child in (root / relative).rglob("*"):
                B.reject_link(child)
        return marker
    root.mkdir(parents=True)
    for name, content in {**files, **generated}.items():
        path = B.managed_path(root, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(content)
        path.chmod(0o755 if name in executables or name.startswith("toolchain/") and not name.endswith((".ini", ".py")) else 0o644)
        os.utime(path, (1577836800, 1577836800))
    (root / MARKER).write_bytes(B.json_bytes(marker))
    return marker


def verify_prefix(root):
    files = M.inventory(root)
    path = B.managed_path(root, PREFIX_STATE)
    if path.exists():
        if B.read_json(path) != {"schema_version": 1, "kind": KIND, "prefix_files": files}:
            raise BuildError("Installed layout prefix changed or install interrupted. Nothing overwritten.")
    elif files:
        raise BuildError("Existing layout prefix has no owned file record. Nothing overwritten.")
    return files


def save_prefix_state(root):
    files = M.inventory(root)
    (root / PREFIX_STATE).write_bytes(B.json_bytes({"schema_version": 1, "kind": KIND, "prefix_files": files}))
    return files


def install_development_files(root, package):
    private, source = root / "build" / package["name"], root / "sources" / package["source_dir"]
    mapping = B.read_json(B.require_file(private / "meson-info/intro-installed.json", "Layout install manifest"))
    include = "include/fribidi/" if package["name"] == "fribidi" else "include/pango-1.0/pango/"
    selected = {}
    for name, destination in mapping.items():
        if not isinstance(destination, str):
            raise BuildError("Unexpected layout install manifest entry.")
        relative = destination.removeprefix("/usr/")
        if not (relative.startswith(include) and relative.endswith(".h") or relative in {"lib/pkgconfig/" + n for n in REQUIRED_PC}):
            continue
        M.safe_name(relative)
        path = Path(name)
        if (not destination.startswith("/usr/") or not path.is_absolute()
                or not (path.is_relative_to(source) or path.is_relative_to(private)) or relative in selected):
            raise BuildError("Layout install manifest escapes owned source/build or duplicates output.")
        selected[relative] = B.managed_path(root, path.relative_to(root).as_posix())
    if not selected:
        raise BuildError("Layout install manifest contains no public headers/metadata.")
    for relative, path in selected.items():
        T.copy_file(path, root / "prefix" / relative)
        if relative.endswith(".pc"):
            output = root / "prefix" / relative
            text = output.read_text(encoding="utf-8")
            text = re.sub(r"(?m)^prefix=.*$", "prefix=${pcfiledir}/../..", text)
            output.write_text(text, encoding="utf-8", newline="\n")
    for target in package["targets"]:
        T.copy_file(private / target, root / "prefix/lib" / Path(target).name)


def validate_prefix(root):
    prefix = root / "prefix"
    for name in REQUIRED_HEADERS:
        B.require_file(prefix / "include" / name, "Layout public header")
    for name in REQUIRED_PC:
        B.require_file(prefix / "lib/pkgconfig" / name, "Layout package metadata")
    unicode_header = (prefix / "include/fribidi/fribidi-unicode-version.h").read_text(encoding="utf-8")
    if re.search(r'#define\s+FRIBIDI_UNICODE_VERSION\s+"18\.0\.0"', unicode_header) is None:
        raise BuildError("FriBidi must use its regenerated Unicode 18.0.0 header.")
    static = {path.relative_to(prefix).as_posix(): M.inspect_static_archive(path) for path in sorted(prefix.rglob("*.a"))}
    if set(static) != {"lib/" + name for name in REQUIRED_ARCHIVES}:
        raise BuildError("Layout prefix must contain exactly four selected static libraries.")
    runtime = {name: B.inspect_shared_elf(B.require_file(root / name, "Layout runtime artifact").read_bytes()) for name in ARTIFACTS}
    probe = runtime[ARTIFACTS[0]]
    if (probe["interpreter"] != "/system/bin/linker64" or probe["android_api_notes"] != [23]
            or not {"libc.so", "libc++_shared.so"}.issubset(probe["needed"])
            or not set(probe["needed"]).issubset({"libc.so", "libc++_shared.so", "libm.so", "libdl.so", "libz.so"})):
        raise BuildError("Layout probe must be Android x86_64/API23 with only the verified C++ runtime and NDK system dependencies.")
    files = M.inventory(root)
    for name, record in files.items():
        if record["bytes"] == 0 or name.endswith(".la") or ((name.endswith(".so") or ".so." in name) and name != ARTIFACTS[1]):
            raise BuildError(f"Unexpected empty/shared/libtool layout file: {name}")
        if name.endswith(".pc"):
            text = (root / name).read_text(encoding="utf-8")
            if "prefix=${pcfiledir}/../.." not in text or re.search(r"\b[A-Za-z]:[/\\]|/(?:home|workspace|tmp|opt)/", text):
                raise BuildError(f"Layout metadata retains a build-host path: {name}")
    return {"static_libraries": static, "runtime": runtime, "fribidi_unicode": "18.0.0"}, files


def verify_receipt(repo):
    profile, inputs, _, pins = selected_inputs(repo)
    prior = prior_inputs(repo)
    root = repo / "build/android-text-layout"
    B.reject_link(root.parent)
    B.reject_link(root)
    receipt = B.read_json(B.managed_path(root, "receipt.json"))
    validation, files = validate_prefix(root)
    if B.fingerprint(root / ARTIFACTS[1]) != B.fingerprint(repo / "build/android-fonts" / ARTIFACTS[1]):
        raise BuildError("Layout C++ runtime differs from the verified font runtime.")
    if (receipt.get("schema_version") != 1 or receipt.get("kind") != KIND or receipt.get("profile") != profile
            or receipt.get("inputs") != {"project_files": inputs, "archives": pins, "prerequisites": prior}
            or receipt.get("prefix_files") != files or receipt.get("validation") != validation
            or receipt.get("artifacts") != {name: files[name] for name in ARTIFACTS}):
        raise BuildError("Layout receipt/input/artifact hashes differ. Preserve changes before rebuilding.")
    print("ANDROID TEXT LAYOUT ARTIFACTS VERIFIED", flush=True)
    return receipt


def build(repo, args):
    profile, inputs, plan, pins = selected_inputs(repo)
    prior = prior_inputs(repo)
    files, executables = {}, []
    def cached(archive):
        directory = repo / "build/android-layout-sources" if archive == PACKAGES[0][1] else args.archive_directory
        if directory is None and T.is_msys() and (Path("/tmp/android-dl") / archive).exists():
            directory = Path("/tmp/android-dl")
        return M.archive_data(repo, archive, pins[archive], directory)
    for package in plan["packages"]:
        members, modes = archive_files(cached(package["archive"]), package)
        files.update(members)
        executables.extend(modes)
        for license_file in package["license_files"]:
            if not members.get("sources/" + package["source_dir"] + "/" + license_file):
                raise BuildError("Layout source license or copyright notice missing.")
    host = plan["host_generator"]
    files.update(host_source_files(cached(GLIB_ARCHIVE), host))
    root = repo / "build/android-text-layout"
    if re.search(r"[\s'\"$`#;&<>|?!*]", str(root)):
        raise BuildError("Use the existing workspace without spaces/metacharacters; NDK paths may contain spaces.")
    env = R.isolated_environment()
    ndk_tools, tools, versions = T.tools_and_versions(args.ndk, env)
    for label, name in (("host-cc", "gcc"), ("host-cxx", "g++")):
        tools[label] = B.require_file(Path("/usr/bin") / name if T.is_msys() else Path(shutil.which(name) or "/missing/" + name), "Native layout generator compiler")
        versions[label] = T.run([tools[label], "--version"], env, capture=True).splitlines()[0]
    template = files["sources/" + host["source_dir"] + "/" + host["path"]]
    marker = prepare_sources(root, files, executables, generated_files(root, repo, ndk_tools, tools, template), profile, pins, prior)
    verify_prefix(root)
    receipt_path = B.managed_path(root, "receipt.json")
    if receipt_path.exists():
        receipt_path.unlink()
    (root / "prefix/lib/pkgconfig").mkdir(parents=True, exist_ok=True)
    save_prefix_state(root)
    prefixes = [root / "prefix", *(repo / "build" / name / "prefix" for name in STAGES)]
    env.update(PKG_CONFIG_LIBDIR=os.pathsep.join(str(path / "lib/pkgconfig") for path in prefixes), PKG_CONFIG_PATH="")
    for package in plan["packages"]:
        private = root / "build" / package["name"]
        setup = [tools["meson"], "setup", private, root / "sources" / package["source_dir"],
                 "--cross-file", root / "toolchain/android.ini", "--native-file", root / "toolchain/native.ini", *package["meson_options"]]
        if (private / "meson-private/coredata.dat").exists():
            setup.append("--reconfigure")
        T.run(setup, env)
        T.run([tools["ninja"], "-C", private, "-j", str(args.jobs), *package["targets"]], env)
        install_development_files(root, package)
        for license_file in package["license_files"]:
            T.copy_file(root / "sources" / package["source_dir"] / license_file, root / "prefix/share/licenses/wesnoth-text-layout" / package["name"] / license_file)
        save_prefix_state(root)
    T.copy_file(root / "sources" / host["source_dir"] / host["license"], root / "prefix/share/licenses/wesnoth-text-layout/host-glib-mkenums/GPL-2.0-or-later.txt")
    T.copy_file(repo / "build/android-fonts" / ARTIFACTS[1], root / ARTIFACTS[1])
    save_prefix_state(root)
    probe = root / ARTIFACTS[0]
    probe.parent.mkdir(exist_ok=True)
    flags = shlex.split(T.run([tools["pkg-config"], "--cflags", "--libs", "--static", "pangocairo", "pangoft2", "fribidi"], env, capture=True))
    link_map = root / "build/probe.map"
    T.run([root / "toolchain/cc", "-std=c11", "-O2", "-fPIE", "-pie", repo / "tools/native-text-layout/probe.c",
           *flags, "-lc++_shared", "-Xlinker", "-Map", "-Xlinker", link_map, "-o", probe], env)
    mapping = B.require_file(link_map, "Layout linker map").read_text(encoding="utf-8")
    for name in (*REQUIRED_ARCHIVES, "libharfbuzz.a", "libfreetype.a", "libfontconfig.a", "libcairo.a", "libglib-2.0.a", "libgobject-2.0.a"):
        if name + "(" not in mapping:
            raise BuildError(f"Layout probe linker map contains no members from {name}.")
    save_prefix_state(root)
    validation, records = validate_prefix(root)
    verify_sources(root, marker)
    if any(B.fingerprint(repo / name) != record for name, record in inputs.items()) or prior_inputs(repo) != prior:
        raise BuildError("Layout inputs or completed prerequisites changed during compilation; no receipt written.")
    receipt = {"schema_version": 1, "kind": KIND, "profile": profile,
               "inputs": {"project_files": inputs, "archives": pins, "prerequisites": prior}, "tools": versions,
               "artifacts": {name: records[name] for name in ARTIFACTS}, "prefix_files": records,
               "validation": validation, "device_executed": False}
    with receipt_path.open("xb") as stream:
        stream.write(B.json_bytes(receipt))
    print("ANDROID TEXT LAYOUT BUILD PASS", flush=True)
    print(f"Prefix: {root / 'prefix'}\nReceipt: {receipt_path}", flush=True)
    print("Offline paragraph libraries/probe built; actual Android execution remains untested.", flush=True)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ndk")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--archive-directory")
    parser.add_argument("--verify", "--verify-only", action="store_true")
    args = parser.parse_args()
    try:
        if args.verify:
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
