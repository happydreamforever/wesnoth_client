#!/usr/bin/env python3
"""Prepare a verified, separate Android source tree without downloads or builds.

The preserved engine snapshot is never modified. This checkpoint adapts Gradle
configuration and flattened Lua only; Android native compilation remains blocked.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys


SOURCE_MANIFEST_SHA256 = "fa2a240b93b994c756ff96d444c0a93416557c17d57ff60af1bd85b5c88080d1"
UPSTREAM_COMMIT = "75c84d2ed344b954d1fd308e588dfe13aef53b20"
STAGE_MARKER = ".wesnoth-android-stage.json"
ORIGINAL_MANIFEST = ".wesnoth-upstream-source-manifest.json"
NATIVE_BLOCK_MESSAGE = (
    "Android native compilation is not ported to Windows yet. "
    "This generated project prepares source and Gradle configuration only; "
    "it cannot build an APK. Follow docs/NEXT_TEST.md in the client checkout."
)
NATIVE_BLOCKERS = [
    "NDK host paths and the SCons Windows/MSYS compiler selection need a port.",
    "Android dependency archives must be verified and built for x86_64 offline.",
    "Native API selection and the Java compile SDK must use separate settings.",
    "The final APK needs verified native libraries and SDL Java sources.",
]


class PreparationError(Exception):
    """An input or existing destination must be inspected before continuing."""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load_profile(path):
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PreparationError(f"Cannot read Android build profile: {error}") from error
    if not isinstance(profile, dict):
        raise PreparationError("Android build profile must be a JSON object.")
    for key in ("schema_version", "min_sdk", "compile_sdk", "target_sdk", "java_major"):
        if type(profile.get(key)) is not int or profile[key] < 1:
            raise PreparationError(f"Invalid integer in Android build profile: {key}")
    if profile["schema_version"] != 1 or profile.get("abi") != "x86_64":
        raise PreparationError("Only profile schema 1 and the verified x86_64 target are supported.")
    if profile.get("wesnoth_version") != "1.19.26":
        raise PreparationError("The preserved source requires Wesnoth 1.19.26.")
    for key in ("build_tools", "ndk_version", "gradle_version", "agp_version", "scons_version"):
        if not isinstance(profile.get(key), str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", profile[key]):
            raise PreparationError(f"Invalid version in Android build profile: {key}")
    if not re.fullmatch(r"[0-9a-f]{64}", str(profile.get("gradle_sha256", ""))):
        raise PreparationError("Missing or invalid official Gradle distribution checksum.")
    if (not 23 <= profile["min_sdk"] <= 28
            or not profile["min_sdk"] <= profile["target_sdk"] <= profile["compile_sdk"] <= 35):
        raise PreparationError("SDK settings do not match the confirmed Android 9 / API 28 target.")
    return profile


def checked_path(root, relative):
    posix = PurePosixPath(relative)
    if posix.is_absolute() or ".." in posix.parts or "\\" in relative or ":" in relative:
        raise PreparationError(f"Unsafe manifest path: {relative}")
    path = root
    for component in posix.parts:
        path = path / component
        if path.is_symlink():
            raise PreparationError(f"Symbolic link is not a preserved source file: {relative}")
    return path


def verified_source(engine):
    """Read only pinned files. Executable-bit changes do not alter source bytes."""
    if engine.is_symlink():
        raise PreparationError("The engine directory must not be a symbolic link.")
    manifest_path = checked_path(engine, "source-manifest.json")
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError as error:
        raise PreparationError(f"Cannot read preserved source manifest: {error}") from error
    if digest(manifest_bytes) != SOURCE_MANIFEST_SHA256:
        raise PreparationError("Preserved source manifest differs from the pinned restoration. No files copied.")
    manifest = json.loads(manifest_bytes)
    if manifest["upstream"]["commit"] != UPSTREAM_COMMIT:
        raise PreparationError("Unexpected upstream source commit.")
    sources = {}
    mismatches = []
    for entry in manifest["files"]:
        relative = entry["path"]
        try:
            content = checked_path(engine, relative).read_bytes()
            if len(content) != entry["bytes"] or digest(content) != entry["sha256"]:
                mismatches.append(relative)
            sources[relative] = content
        except OSError:
            mismatches.append(relative)
    if mismatches:
        details = "\n  ".join(mismatches[:25])
        remainder = f"\n  ... and {len(mismatches) - 25} more" if len(mismatches) > 25 else ""
        raise PreparationError(
            f"{len(mismatches)} preserved source files differ or are missing:\n  {details}{remainder}\n"
            "Your files were not changed. Inspect the local edits; do not reset or discard them."
        )
    sources[ORIGINAL_MANIFEST] = manifest_bytes
    return sources, {entry["path"]: entry["git_mode"] for entry in manifest["files"]}


def replace_once(content, before, after, name):
    if content.count(before) != 1:
        raise PreparationError(f"Expected upstream text does not match the staged overlay: {name}")
    return content.replace(before, after, 1)


def apply_overlays(sources, profile):
    """Transform verified bytes in memory; never patch the original engine."""
    staged = sources.copy()
    prefix = "packaging/android/"
    wrapper = prefix + "gradle/wrapper/gradle-wrapper.properties"
    text = staged[wrapper].decode("utf-8")
    text = replace_once(
        text, "gradle-8.11.1-bin.zip", f"gradle-{profile['gradle_version']}-bin.zip", wrapper
    )
    if not text.endswith("\n"):
        raise PreparationError("Pinned Gradle wrapper properties must end in a newline.")
    text += f"distributionSha256Sum={profile['gradle_sha256']}\n"
    staged[wrapper] = text.encode("utf-8")

    # Replace the small top-level file instead of retaining download-capable
    # native tasks. Project evaluation/help remain possible; builds stop clearly.
    root_gradle = f"""// Generated by tools/prepare_android_source.py; original engine is preserved.
plugins {{
    id 'com.android.application' version '{profile['agp_version']}' apply false
    id 'com.android.library' version '{profile['agp_version']}' apply false
}}

['buildCppDepends', 'buildCppSource'].each {{ nativeTask ->
    tasks.register(nativeTask) {{
        doLast {{ throw new GradleException('{NATIVE_BLOCK_MESSAGE}') }}
    }}
}}

subprojects {{
    tasks.matching {{ it.name == 'preBuild' }}.configureEach {{
        doFirst {{ throw new GradleException('{NATIVE_BLOCK_MESSAGE}') }}
    }}
}}

tasks.register('clean', Delete) {{ delete rootProject.layout.buildDirectory }}
"""
    staged[prefix + "build.gradle"] = root_gradle.encode("utf-8")
    app = prefix + "app/build.gradle"
    text = staged[app].decode("utf-8")
    for before, after in (
        ("    compileSdk 35\n", f"    compileSdk {profile['compile_sdk']}\n    buildToolsVersion '{profile['build_tools']}'\n"),
        ("    ndkVersion '26.3.11579264'", f"    ndkVersion '{profile['ndk_version']}'"),
        ("        minSdk 23", f"        minSdk {profile['min_sdk']}"),
        ("        targetSdk 35", f"        targetSdk {profile['target_sdk']}"),
        ('            include "x86_64", "armeabi-v7a", "arm64-v8a", "x86"', '            include "x86_64"'),
    ):
        text = replace_once(text, before, after, app)
    staged[app] = text.encode("utf-8")

    # The source restoration deliberately flattened the exact Lua submodule.
    # Validate real required source files instead of inventing .git metadata.
    text = staged["SConstruct"].decode("utf-8")
    text = replace_once(
        text,
        'if not File("#/src/modules/lua/.git").rfile().exists():',
        'if not all(File("#/src/modules/lua/" + name).rfile().exists() for name in ("lua.h", "lapi.c")):',
        "SConstruct Lua source check",
    )
    text = replace_once(
        text,
        'Warning("Lua submodule does not exist. You must run \'git submodule update --init --recursive\' to initialize it.")',
        'Warning("Pinned Lua source is missing. Verify engine/source-manifest.json before preparing Android source.")',
        "SConstruct Lua warning",
    )
    staged["SConstruct"] = text.encode("utf-8")
    return staged


def prepare(engine, destination, profile):
    engine = engine.absolute()
    destination = destination.absolute()
    resolved_engine = engine.resolve()
    resolved_destination = destination.resolve()
    if (resolved_engine == resolved_destination or resolved_engine in resolved_destination.parents
            or resolved_destination in resolved_engine.parents):
        raise PreparationError("The staged destination must be separate from the preserved engine.")
    sources, modes = verified_source(engine)
    staged = apply_overlays(sources, profile)
    file_hashes = {name: digest(data) for name, data in sorted(staged.items())}
    marker = {
        "schema_version": 1,
        "generator": "tools/prepare_android_source.py",
        "generator_sha256": digest(Path(__file__).read_bytes()),
        "upstream_commit": UPSTREAM_COMMIT,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "profile": profile,
        "windows_android_build_ready": False,
        "native_build_blockers": NATIVE_BLOCKERS,
        "overlay_files": sorted(name for name in sources if sources[name] != staged[name]),
        "files": file_hashes,
    }
    marker_bytes = json_bytes(marker)
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not destination.is_dir():
            raise PreparationError("Existing destination is not a managed stage directory. Nothing overwritten.")
        try:
            if checked_path(destination, STAGE_MARKER).read_bytes() != marker_bytes:
                raise PreparationError("Existing stage has different provenance or settings. Nothing overwritten.")
            expected = set(staged) | {STAGE_MARKER}
            actual = set()
            for path in destination.rglob("*"):
                if path.is_symlink():
                    raise PreparationError("Existing stage contains symbolic links. Nothing overwritten.")
                if path.is_file():
                    actual.add(path.relative_to(destination).as_posix())
            if actual != expected:
                raise PreparationError("Existing stage contains additional or missing files. Nothing overwritten.")
            for name, expected_hash in file_hashes.items():
                if digest(checked_path(destination, name).read_bytes()) != expected_hash:
                    raise PreparationError(f"Existing staged file was edited: {name}. Nothing overwritten.")
        except OSError as error:
            raise PreparationError("Existing destination is incomplete or unmanaged. Nothing overwritten.") from error
        return False, len(sources) - 1

    # All source bytes and overlays are verified before any destination writes.
    # mkdir fails if a concurrent process creates this exact destination first.
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()
    try:
        for name, content in staged.items():
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(content)
            if os.name != "nt":
                path.chmod(0o755 if modes.get(name) == "100755" else 0o644)
        for name, expected_hash in file_hashes.items():
            if digest((destination / name).read_bytes()) != expected_hash:
                raise PreparationError(f"Staged bytes failed verification: {name}")
        # A complete marker is written last. Interrupted partial stages are never
        # reused or automatically removed, and no force/overwrite option exists.
        with (destination / STAGE_MARKER).open("xb") as stream:
            stream.write(marker_bytes)
    except (OSError, PreparationError) as error:
        raise PreparationError(f"Incomplete stage retained at {destination}: {error}") from error
    return True, len(sources) - 1


def main(argv=None):
    repo = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=repo / "build" / "android-source")
    args = parser.parse_args(argv)
    try:
        output = args.output.absolute()
        build = (repo / "build").resolve()
        if build not in output.resolve().parents:
            raise PreparationError("Use a new subdirectory under this checkout's ignored build directory.")
        created, count = prepare(repo / "engine", output, load_profile(repo / "tools" / "android-build-profile.json"))
    except (OSError, PreparationError) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2
    print(f"Verified {count} preserved source files; executable-bit differences are ignored.")
    print(f"{'PREPARED' if created else 'ALREADY PREPARED'}: {output}")
    print("Source preparation completed. APK build remains BLOCKED:")
    for blocker in NATIVE_BLOCKERS:
        print(f"  - {blocker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
