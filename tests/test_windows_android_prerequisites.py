"""Linux PowerShell control-flow fixtures with synthetic tools and archive hashes.

Uses committed engine source; needs no ignored dependency downloads. These tests
do not validate native Windows executables, real dependency archives or APK builds.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PWSH = None


class PrerequisitesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="android prereq ")
        self.base = Path(self.temp.name)
        self.repo = self.base / "client"
        shutil.copytree(ROOT / "engine", self.repo / "engine")
        (self.repo / "tools").mkdir()
        for name in ("Test-WindowsAndroidPrerequisites.ps1", "android-build-profile.json", "android-native-archives.json"):
            shutil.copy2(ROOT / "tools" / name, self.repo / "tools" / name)
        self.profile = json.loads((ROOT / "tools/android-build-profile.json").read_text())
        self.sdk = self.base / "sdk"
        self.java = self.base / "jdk"
        self.msys = self.base / "msys"
        self.gradle = self.base / "gradle cache"
        for path in ("platforms/android-35/android.jar", "build-tools/34.0.0/aapt2.exe",
                     "build-tools/34.0.0/zipalign.exe", "build-tools/34.0.0/apksigner.bat"):
            self.put(self.sdk / path)
        self.ndk = self.sdk / ("ndk/" + self.profile["ndk_version"])
        self.put(self.ndk / "source.properties", "Pkg.Revision = " + self.profile["ndk_version"])
        self.bin = self.ndk / "toolchains/llvm/prebuilt/windows-x86_64/bin"
        for name in ("clang++.exe", "llvm-ar.exe", "llvm-strip.exe", "x86_64-linux-android23-clang.cmd", "x86_64-linux-android23-clang++.cmd"):
            self.put(self.bin / name)
        self.exe(self.bin / "clang.exe", "print('Android clang version 17.0.2')")
        self.put(self.ndk / "toolchains/llvm/prebuilt/windows-x86_64/sysroot/usr/lib/x86_64-linux-android/libc++_shared.so")
        self.exe(self.java / "bin/java.exe", "import sys; print('openjdk version \"21.0.9\"', file=sys.stderr)")
        self.exe(self.java / "bin/javac.exe", "print('javac 21.0.9')")
        self.bash = self.msys / "usr/bin/bash.exe"
        self.exe(self.bash, '''import sys
assert sys.argv[1:4] == ['--noprofile', '--norc', '-c']
assert '"$tool"' in sys.argv[4] and 'print("SCONS_VERSION "' in sys.argv[4]
for name in 'make patch tar gzip bzip2 xz autoconf automake libtoolize pkg-config perl meson ninja gcc g++'.split():
    print('TOOL_PRESENT ' + name + ' /usr/bin/' + name)
print('MSYS_PYTHON 3.12.13')
print('SCONS_VERSION 4.11.0')
''')
        version = self.profile["gradle_version"]
        self.put(self.gradle / f"wrapper/dists/gradle-{version}-bin/fixture/gradle-{version}/lib/gradle-launcher-{version}.jar")
        for group, name, version, extension in (
            ("com.android.tools.build", "gradle", self.profile["agp_version"], "jar"),
            ("androidx.core", "core", "1.16.0", "aar"),
            ("androidx.documentfile", "documentfile", "1.1.0", "aar"),
        ):
            self.put(self.gradle / f"caches/modules-2/files-2.1/{group}/{name}/{version}/fixture/{name}-{version}.{extension}")
        self.archive_root = self.repo / "packaging/android/download"
        self.archive_root.mkdir(parents=True)
        archive_manifest = json.loads((ROOT / "tools/android-native-archives.json").read_text())
        for entry in archive_manifest["archives"]:
            fixture_bytes = ("Synthetic archive fixture: " + entry["name"]).encode()
            (self.archive_root / entry["name"]).write_bytes(fixture_bytes)
            entry["bytes"] = len(fixture_bytes)
            entry["sha256"] = hashlib.sha256(fixture_bytes).hexdigest()
        (self.repo / "tools/android-native-archives.json").write_text(json.dumps(archive_manifest))
        self.put(self.repo / ".env", "DO_NOT_REPORT_PRIVATE_SENTINEL")

    def tearDown(self):
        self.temp.cleanup()

    def put(self, path, content="nonempty fixture"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def exe(self, path, code):
        self.put(path, "#!/usr/bin/python3\n" + code + "\n")
        path.chmod(0o755)

    def run_probe(self):
        env = dict(os.environ, OS="Windows_NT", USERPROFILE=str(self.base), LOCALAPPDATA=str(self.base))
        result = subprocess.run([
            PWSH, "-NoLogo", "-NoProfile", "-File", str(self.repo / "tools/Test-WindowsAndroidPrerequisites.ps1"),
            "-MsysRoot", str(self.msys), "-SdkRoot", str(self.sdk), "-JavaHome", str(self.java), "-GradleUserHome", str(self.gradle),
        ], text=True, capture_output=True, env=env, timeout=60)
        report = next((self.repo / "diagnostics").glob("android-prerequisites-*.txt")).read_text(encoding="utf-8-sig")
        self.assertNotIn("DO_NOT_REPORT_PRIVATE_SENTINEL", result.stdout + result.stderr + report)
        self.assertIn("does not produce or validate an APK", report)
        return result.returncode, report

    def test_present_inputs_are_not_claimed_to_be_a_build(self):
        script = self.repo / "engine/packaging/android/build-sources"
        script.chmod(script.stat().st_mode ^ 0o111)
        code, report = self.run_probe()
        self.assertEqual(code, 0, report)
        self.assertIn("All 1591 restored source files match", report)
        self.assertIn("matching size/SHA256=23/23", report)
        self.assertIn("transitive dependencies, cache checksums and offline resolution remain unverified", report)
        self.assertIn("BLOCKED entries=0", report)

    def test_local_source_change_blocks_without_modifying_it(self):
        source = self.repo / "engine/SConstruct"
        original = source.read_bytes()
        changed = bytes([original[0] ^ 1]) + original[1:]
        source.write_bytes(changed)
        code, report = self.run_probe()
        self.assertEqual(code, 2, report)
        self.assertIn("SOURCE DIFFERENCE: SConstruct (SHA256 differs)", report)
        self.assertEqual(source.read_bytes(), changed)

    def test_missing_and_corrupt_prerequisites_are_actionable(self):
        (self.bin / "clang.exe").unlink()
        (self.sdk / "build-tools/34.0.0/aapt2.exe").write_bytes(b"")
        self.exe(self.java / "bin/java.exe", "print('openjdk version \"17.0.9\"')")
        self.exe(self.bash, "print('wrapper silently failed')")
        self.put(self.archive_root / "bzip2-1.0.8.tar.gz", "corrupt fixture")
        code, report = self.run_probe()
        self.assertEqual(code, 2, report)
        self.assertIn("clang.exe", report)
        self.assertIn("aapt2.exe", report)
        self.assertIn("did not report usable JDK21", report)
        self.assertIn("MSYS2 probe output is incomplete", report)
        self.assertIn("ARCHIVE SIZE/SHA256 MISMATCH: bzip2-1.0.8.tar.gz", report)

    def test_manifest_change_is_rejected_before_scanning(self):
        manifest = self.repo / "engine/source-manifest.json"
        manifest.write_text('{"files":[{"path":"../.env"}]}')
        code, report = self.run_probe()
        self.assertEqual(code, 2, report)
        self.assertIn("Source manifest SHA256 differs", report)
        self.assertNotIn("SOURCE DIFFERENCE:", report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pwsh", required=True)
    args, rest = parser.parse_known_args()
    PWSH = args.pwsh
    unittest.main(argv=[__file__] + rest)
