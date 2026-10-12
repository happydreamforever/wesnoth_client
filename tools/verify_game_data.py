#!/usr/bin/env python3
"""Read-only, offline comparison with the recorded VPS game-data copy."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time


ALGORITHM = "sha256-sorted-json-lines-path-size-sha256-v1"
REFERENCE = Path(__file__).with_name("gamedata-1.19.26.reference.json")
METADATA = {"manifest.txt", "status.properties"}
SUMMARY_FIELDS = ("files", "bytes", "tree_sha256", "directories", "empty_files", "metadata")


class VerificationError(Exception):
    pass


def checked_stat(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise VerificationError(f"Link or reparse point is not allowed: {path}")
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise VerificationError(f"Special file is not allowed: {path}")
    return info


def check_name(name):
    # Apply Windows filename rules on either host before accepting a copy.
    if (name in {".", ".."} or name.endswith((".", " "))
            or any(ord(char) < 32 or char in '<>:"/\\|?*' for char in name)
            or re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9\u00b9\u00b2\u00b3]|LPT[1-9\u00b9\u00b2\u00b3]",
                            name.split(".")[0], re.IGNORECASE)):
        raise VerificationError(f"Windows-incompatible name: {name!r}")


def fingerprint(info):
    return (info.st_mode, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def inventory(root):
    # Do not resolve() first: that would hide a symlink in the selected path.
    for ancestor in reversed((root, *root.parents)):
        if not stat.S_ISDIR(checked_stat(ancestor).st_mode):
            raise VerificationError(f"Expected a directory: {ancestor}")
    files = {}
    folded = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        for entry in directory.iterdir():
            check_name(entry.name)
            relative = entry.relative_to(root).as_posix()
            key = relative.casefold()
            if key in folded:
                raise VerificationError(f"Case-insensitive path collision: {folded[key]!r}, {relative!r}")
            folded[key] = relative
            info = checked_stat(entry)
            if stat.S_ISDIR(info.st_mode):
                pending.append(entry)
            else:
                files[relative] = fingerprint(info)
    return files


def canonical_record(relative, size, digest):
    return (json.dumps([relative, size, digest], ensure_ascii=True,
                       separators=(",", ":")) + "\n").encode("ascii")


def scan(root, progress=None):
    """Hash exact file names and bytes; directory-only structure is ignored."""
    root = Path(os.path.abspath(root))
    files = inventory(root)
    if not files:
        raise VerificationError(f"Data directory contains no files: {root}")
    groups = {}
    tree = hashlib.sha256()
    total_bytes = 0
    empty_files = []
    metadata = {}
    last_progress = time.monotonic()
    if progress:
        progress(f"Found {len(files):,} files. Reading every file for SHA256 comparison...")
    for index, relative in enumerate(sorted(files), 1):
        path = root / relative
        if fingerprint(checked_stat(path)) != files[relative]:
            raise VerificationError(f"File changed during verification: {relative}")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as source:
            if fingerprint(os.fstat(source.fileno())) != files[relative]:
                raise VerificationError(f"File changed while opening: {relative}")
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
            if fingerprint(os.fstat(source.fileno())) != files[relative]:
                raise VerificationError(f"File changed while reading: {relative}")
        if size != files[relative][3]:
            raise VerificationError(f"Incomplete file read: {relative}")
        file_hash = digest.hexdigest()
        record = canonical_record(relative, size, file_hash)
        tree.update(record)
        parent = relative.rpartition("/")[0] or "."
        group = groups.setdefault(parent, {"files": 0, "bytes": 0, "digest": hashlib.sha256()})
        group["files"] += 1
        group["bytes"] += size
        group["digest"].update(record)
        total_bytes += size
        if size == 0:
            empty_files.append(relative)
        if relative in METADATA:
            metadata[relative] = {"bytes": size, "sha256": file_hash}
        now = time.monotonic()
        if progress and (index % 2000 == 0 or now - last_progress >= 5 or index == len(files)):
            progress(f"Checked {index:,}/{len(files):,} files; {total_bytes:,} bytes")
            last_progress = now
    if inventory(root) != files:
        raise VerificationError("Data changed during verification. Retry with copying/editing stopped.")
    return {
        "files": len(files), "bytes": total_bytes, "tree_sha256": tree.hexdigest(),
        "directories": {name: {"files": group["files"], "bytes": group["bytes"],
                               "sha256": group["digest"].hexdigest()}
                        for name, group in sorted(groups.items())},
        "empty_files": empty_files, "metadata": metadata,
    }


def differences(actual, expected):
    errors = []
    for field in ("files", "bytes", "tree_sha256"):
        if actual[field] != expected[field]:
            errors.append(f"{field}: expected {expected[field]}, found {actual[field]}")
    for name in sorted(actual["directories"].keys() | expected["directories"].keys()):
        if actual["directories"].get(name) != expected["directories"].get(name):
            errors.append(f"Directory differs (file names, sizes or contents): {name}")
    for field in ("empty_files", "metadata"):
        if actual[field] != expected[field]:
            errors.append(f"{field} differs from the reference")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path,
                        default=Path(__file__).absolute().parents[2] / "gamedata-1.19.26")
    args = parser.parse_args(argv)
    try:
        expected = json.loads(REFERENCE.read_text(encoding="utf-8"))
        if (not isinstance(expected, dict) or expected.get("schema") != 1
                or expected.get("algorithm") != ALGORITHM
                or any(field not in expected for field in SUMMARY_FIELDS)
                or not isinstance(expected["directories"], dict)
                or not isinstance(expected["metadata"], dict)
                or not isinstance(expected["empty_files"], list)):
            raise VerificationError("Unsupported or incomplete reference manifest. Update the tools.")
        print(f"Game data: {args.data_root.absolute()}", flush=True)
        print("Read-only offline check against the recorded VPS copy; no files are changed.", flush=True)
        actual = scan(args.data_root, lambda message: print(message, flush=True))
        errors = differences(actual, expected)
        if errors:
            for error in errors[:35]:
                print(f"MISMATCH: {error}", flush=True)
            if len(errors) > 35:
                print(f"... {len(errors) - 35} additional mismatches", flush=True)
            raise VerificationError("Copy does not match. Preserve it and send the complete output.")
        print(f"PASS: {actual['files']:,} files; {actual['bytes']:,} bytes; "
              f"{len(actual['empty_files'])} expected empty files")
        print(f"Tree SHA256: {actual['tree_sha256']}")
        print("Core 1.19.26; music 1.19.18; manual_install=true (metadata matches).")
        print("GAME DATA VERIFICATION PASSED")
        print("This verifies the copied folder. Android asset installation, OBB and gameplay remain pending.")
        return 0
    except KeyboardInterrupt:
        print("INCOMPLETE: verification interrupted; no successful result.", file=sys.stderr)
        return 130
    except (OSError, ValueError, KeyError, TypeError, VerificationError) as error:
        print(f"GAME DATA VERIFICATION FAILED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
