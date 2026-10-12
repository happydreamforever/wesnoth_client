#!/usr/bin/env python3
"""Prepare and run pinned project-private SCons without pip or global installation.

prepare is offline unless --download is explicitly supplied. version and run
always verify the retained wheel and private files and never use the network.
Invoke with the selected MSYS2 /usr/bin/python3 -I on Windows.
"""

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import runpy
import stat
import sys
import urllib.parse
import urllib.request
import zipfile


MARKER = ".wesnoth-scons-package.json"


class PackageError(Exception):
    """A missing or changed input requires attention, without overwriting it."""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load_package(path):
    try:
        package = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PackageError(f"Cannot read SCons package declaration: {error}") from error
    if not isinstance(package, dict) or package.get("schema_version") != 1:
        raise PackageError("Unexpected SCons package declaration.")
    version = package.get("version", "")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise PackageError("Invalid SCons package version.")
    if package.get("filename") != f"scons-{version}-py3-none-any.whl":
        raise PackageError("Unexpected SCons wheel filename.")
    if type(package.get("bytes")) is not int or not 0 < package["bytes"] < 20_000_000:
        raise PackageError("Invalid SCons wheel size.")
    if not re.fullmatch(r"[0-9a-f]{64}", str(package.get("sha256", ""))):
        raise PackageError("Invalid SCons wheel checksum.")
    url = urllib.parse.urlsplit(package.get("url", ""))
    if (url.scheme != "https" or url.netloc != "files.pythonhosted.org"
            or url.query or url.fragment or not url.path.endswith("/" + package["filename"])):
        raise PackageError("Unexpected SCons wheel download URL.")
    return package


def reject_link(path):
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise PackageError(f"Symbolic link or junction is not a private tool directory/file: {path}")


def private_path(repo, *parts):
    path = repo
    for part in parts:
        path = path / part
        reject_link(path)
    return path


def check_wheel_bytes(data, package):
    if len(data) != package["bytes"] or digest(data) != package["sha256"]:
        raise PackageError("SCons wheel size/SHA256 differs from the published pin. Preserve the file; no overwrite attempted.")


def get_wheel(repo, package, download=False):
    cache = private_path(repo, "build", "downloads", package["filename"])
    if cache.exists():
        data = cache.read_bytes()
        check_wheel_bytes(data, package)
        return data
    if not download:
        raise PackageError(f"Pinned SCons wheel is missing: {cache}. Use prepare --download while connected, or copy that exact verified wheel here.")
    # Download in memory so a failed transfer leaves no misleading cache file.
    request = urllib.request.Request(package["url"], headers={"User-Agent": "Wesnoth-offline-build-preparation/1"})
    print(f"Downloading pinned SCons {package['version']} ({package['bytes']} bytes).", flush=True)
    with urllib.request.urlopen(request, timeout=60) as response:
        final_url = urllib.parse.urlsplit(response.geturl())
        if final_url.scheme != "https" or final_url.netloc != "files.pythonhosted.org":
            raise PackageError("SCons download redirected outside the pinned HTTPS package host.")
        data = response.read(package["bytes"] + 1)
    check_wheel_bytes(data, package)
    cache.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation preserves a concurrent or unrelated file. Interrupted
    # writes remain visible and fail verification on the next run.
    with cache.open("xb") as stream:
        stream.write(data)
    return data


def wheel_files(data, package):
    """Only unpack the verified purelib layout, rejecting traversal and links."""
    check_wheel_bytes(data, package)
    files = {}
    names = set()
    allowed_roots = {"SCons", f"scons-{package['version']}.dist-info"}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for entry in archive.infolist():
                name = entry.filename
                relative = PurePosixPath(name)
                if (not relative.parts or relative.is_absolute() or ".." in relative.parts
                        or "\\" in name or ":" in name or relative.parts[0] not in allowed_roots
                        or stat.S_ISLNK(entry.external_attr >> 16)):
                    raise PackageError(f"Unsafe or unexpected wheel member: {name}")
                if entry.is_dir():
                    continue
                # Windows paths are case-insensitive; reject both spellings.
                folded = name.casefold()
                if folded in names:
                    raise PackageError(f"Duplicate wheel member: {name}")
                names.add(folded)
                files[name] = archive.read(entry)
    except zipfile.BadZipFile as error:
        raise PackageError(f"Invalid SCons wheel: {error}") from error
    wheel = f"scons-{package['version']}.dist-info/WHEEL"
    if (b"Root-Is-Purelib: true" not in files.get(wheel, b"")
            or b"Tag: py3-none-any" not in files.get(wheel, b"")
            or "SCons/__main__.py" not in files or "SCons/__init__.py" not in files):
        raise PackageError("SCons wheel is not the expected runnable pure Python package.")
    return files


def marker_bytes(files, package):
    marker = {"schema_version": 1, "version": package["version"],
              "wheel_sha256": package["sha256"],
              "files": {name: digest(data) for name, data in sorted(files.items())}}
    return (json.dumps(marker, sort_keys=True, indent=2) + "\n").encode("utf-8")


def installed_path(repo, package):
    return private_path(repo, "build", "tools", f"scons-{package['version']}")


def verify_installed(repo, package, files):
    destination = installed_path(repo, package)
    if not destination.is_dir():
        raise PackageError(f"Private SCons is not prepared: {destination}. Run prepare first.")
    expected = set(files) | {MARKER}
    actual = set()
    for path in destination.rglob("*"):
        reject_link(path)
        if path.is_file():
            actual.add(path.relative_to(destination).as_posix())
    if actual != expected:
        raise PackageError(f"Private SCons contains additional or missing files: {destination}. Nothing overwritten.")
    if (destination / MARKER).read_bytes() != marker_bytes(files, package):
        raise PackageError("Private SCons provenance differs. Nothing overwritten.")
    for name, content in files.items():
        if (destination / name).read_bytes() != content:
            raise PackageError(f"Private SCons file was changed: {name}. Nothing overwritten.")
    return destination


def prepare(repo, package, download=False):
    files = wheel_files(get_wheel(repo, package, download), package)
    destination = installed_path(repo, package)
    if destination.exists():
        verify_installed(repo, package, files)
        return False, len(files)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()
    try:
        for name, content in files.items():
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(content)
        # Marker last: incomplete/interrupted installs are retained, not reused.
        with (destination / MARKER).open("xb") as stream:
            stream.write(marker_bytes(files, package))
        verify_installed(repo, package, files)
    except (OSError, PackageError) as error:
        raise PackageError(f"Incomplete private SCons retained at {destination}: {error}") from error
    return True, len(files)


def run_scons(repo, package, arguments, version_only=False):
    files = wheel_files(get_wheel(repo, package), package)
    destination = verify_installed(repo, package, files)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(destination))
    import SCons
    if SCons.__version__ != package["version"]:
        raise PackageError("Loaded SCons does not match the pinned version.")
    sys.argv = ["scons"] + (["--version"] if version_only else arguments)
    if version_only:
        print(f"SCONS_VERSION {SCons.__version__}", flush=True)
    runpy.run_module("SCons", run_name="__main__")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("prepare", help="Prepare only this project's private SCons.")
    setup.add_argument("--download", action="store_true", help="Allow downloading the pinned wheel when missing.")
    commands.add_parser("version", help="Verify private files and execute the selected SCons version.")
    run = commands.add_parser("run", help="Run verified SCons with this Python interpreter.")
    run.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parent.parent
    try:
        package = load_package(repo / "tools" / "android-scons-package.json")
        if args.command == "prepare":
            created, count = prepare(repo, package, args.download)
            print(f"{'PREPARED' if created else 'ALREADY PREPARED'}: SCons {package['version']}; {count} verified files.")
            print(f"Private tool: {installed_path(repo, package)}")
            print("No global Python/MSYS2 packages were installed or changed.")
        else:
            arguments = [] if args.command == "version" else args.arguments
            if arguments[:1] == ["--"]:
                arguments = arguments[1:]
            run_scons(repo, package, arguments, version_only=args.command == "version")
    except (OSError, ValueError, PackageError) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
