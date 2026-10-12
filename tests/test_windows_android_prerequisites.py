"""Linux PowerShell control-flow fixtures with synthetic tools and archive hashes.

Uses committed engine source; needs no ignored dependency downloads. These tests
do not validate native Windows executables, real dependency archives or APK builds.
Report-write exceptions are injected in temporary script copies; they test control
flow and persistence, not Windows file-sharing behavior.
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
        temporary = self.base / "temp"
        temporary.mkdir(exist_ok=True)
        env = dict(os.environ, OS="Windows_NT", USERPROFILE=str(self.base), LOCALAPPDATA=str(self.base),
                   TEMP=str(temporary), TMPDIR=str(temporary))
        result = subprocess.run([
            PWSH, "-NoLogo", "-NoProfile", "-File", str(self.repo / "tools/Test-WindowsAndroidPrerequisites.ps1"),
            "-MsysRoot", str(self.msys), "-SdkRoot", str(self.sdk), "-JavaHome", str(self.java), "-GradleUserHome", str(self.gradle),
        ], text=True, capture_output=True, env=env, timeout=60)
        self.last_output = result.stdout + result.stderr
        saved = [line.removeprefix("Report saved to: ") for line in result.stdout.splitlines() if line.startswith("Report saved to: ")]
        self.assertLessEqual(len(saved), 1, self.last_output)
        self.saved_path = Path(saved[0]) if saved else None
        report = self.saved_path.read_text(encoding="utf-8-sig") if self.saved_path else ""
        self.assertNotIn("DO_NOT_REPORT_PRIVATE_SENTINEL", result.stdout + result.stderr + report)
        self.assertIn("does not produce or validate an APK", self.last_output)
        if self.saved_path:
            self.assertIn("does not produce or validate an APK", report)
        return result.returncode, report

    def inject_save_errors(self, condition, partial=False):
        """Override only the temp fixture's save operation, leaving checks unchanged."""
        path = self.repo / "tools/Test-WindowsAndroidPrerequisites.ps1"
        source = path.read_text()
        source = source.replace("function Write-ReportFile(", "function Write-ReportFileActual(", 1)
        wrapper = '''
$script:fixtureSaveAttempts = 0
function Write-ReportFile([string]$Path, [string[]]$Lines) {
    $script:fixtureSaveAttempts++
    if (CONDITION) {
        PARTIAL
        throw [IO.IOException]::new('Injected report sharing violation')
    }
    Write-ReportFileActual -Path $Path -Lines $Lines
}
'''.replace("CONDITION", condition).replace("PARTIAL", """
        $null = [IO.Directory]::CreateDirectory((Split-Path $Path -Parent))
        [IO.File]::WriteAllText($Path, 'partial fixture output')
""" if partial else "")
        source = source.replace("function Test-Nonempty(", wrapper + "\nfunction Test-Nonempty(", 1)
        path.write_text(source)

    def test_present_inputs_are_not_claimed_to_be_a_build(self):
        script = self.repo / "engine/packaging/android/build-sources"
        script.chmod(script.stat().st_mode ^ 0o111)
        code, report = self.run_probe()
        self.assertEqual(code, 0, report)
        self.assertIn("All 1591 restored source files match", report)
        self.assertIn("matching size/SHA256=23/23", report)
        self.assertIn("transitive dependencies, cache checksums and offline resolution remain unverified", report)
        self.assertIn("BLOCKED entries=0", report)
        self.assertIn("javac: javac 21.0.9", report)
        self.assertIn("2026-10-12.2", report)

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

    def test_transient_save_errors_do_not_affect_prerequisites(self):
        self.inject_save_errors("$script:fixtureSaveAttempts -lt 3")
        code, report = self.run_probe()
        self.assertEqual(code, 0, self.last_output)
        self.assertEqual(self.saved_path.parent, self.repo / "diagnostics")
        self.assertEqual(report.count("Injected report sharing violation"), 2)
        self.assertIn("Check complete=True; BLOCKED entries=0", report)
        self.assertIn("matching size/SHA256=23/23", report)
        self.assertNotIn("[BLOCKED]", report)

    def test_unavailable_diagnostics_directory_uses_fallback(self):
        self.put(self.repo / "diagnostics", "Existing unrelated file; preserve it.")
        code, report = self.run_probe()
        self.assertEqual(code, 0, self.last_output)
        self.assertEqual(self.saved_path.parent, self.base / "temp/wesnoth-diagnostics")
        self.assertEqual((self.repo / "diagnostics").read_text(), "Existing unrelated file; preserve it.")
        self.assertIn("Check complete=True; BLOCKED entries=0", report)
        self.assertIn("matching size/SHA256=23/23", report)
        self.assertEqual(report.count("Report write attempt"), 3)
        self.assertNotIn("[BLOCKED]", report)
        self.assertNotIn("Report saved to: " + str(self.repo / "diagnostics"), self.last_output)

    def test_fallback_preserves_missing_prerequisite_exit(self):
        (self.bin / "clang.exe").unlink()
        self.inject_save_errors("(Split-Path $Path -Parent) -eq (Join-Path $repoRoot 'diagnostics')")
        code, report = self.run_probe()
        self.assertEqual(code, 2, self.last_output)
        self.assertEqual(self.saved_path.parent, self.base / "temp/wesnoth-diagnostics")
        self.assertIn("Check complete=True; BLOCKED entries=1", report)
        self.assertIn("matching size/SHA256=23/23", report)
        self.assertEqual(report.count("Injected report sharing violation"), 3)

    def test_persistent_save_failure_has_distinct_exit_and_no_saved_claim(self):
        self.inject_save_errors("$true", partial=True)
        code, report = self.run_probe()
        self.assertEqual(code, 3, self.last_output)
        self.assertIsNone(self.saved_path)
        self.assertEqual(report, "")
        self.assertIn("No complete report was saved", self.last_output)
        self.assertIn("Check complete=True; BLOCKED entries=0", self.last_output)
        self.assertIn("matching size/SHA256=23/23", self.last_output)
        self.assertEqual(self.last_output.count("Injected report sharing violation"), 6)
        self.assertNotIn("[BLOCKED]", self.last_output)
        partials = list(self.base.rglob("android-prerequisites-*.txt"))
        self.assertEqual(len(partials), 2)
        self.assertTrue(all(path.read_text() == "partial fixture output" for path in partials))

    def test_early_return_saves_partial_report_with_incomplete_exit(self):
        path = self.repo / "tools/Test-WindowsAndroidPrerequisites.ps1"
        source = path.read_text()
        path.write_text(source.replace("    Invoke-Section 'JDK selected for Gradle' {", "    return\n    Invoke-Section 'JDK selected for Gradle' {", 1))
        code, report = self.run_probe()
        self.assertEqual(code, 2, self.last_output)
        self.assertIn("All 1591 restored source files match", report)
        self.assertIn("Check complete=False; BLOCKED entries=0", report)
        self.assertNotIn("Native archive integrity", report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pwsh", required=True)
    args, rest = parser.parse_known_args()
    PWSH = args.pwsh
    unittest.main(argv=[__file__] + rest)
