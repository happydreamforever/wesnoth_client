"""PowerShell control-flow fixtures with synthetic Windows tools on Linux.

The existing-stage guard uses the real source generator and committed engine.
These tests make no network calls and do not validate Windows SDK/Gradle behavior.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PWSH = None
ENVIRONMENT = ("JAVA_HOME", "ANDROID_HOME", "ANDROID_SDK_ROOT", "GRADLE_USER_HOME")

FAKE_TOOL = r'''#!/usr/bin/python3
import json, os, pathlib, subprocess, sys
cfg = json.loads(pathlib.Path(os.environ['WESNOTH_SETUP_FIXTURE']).read_text())
name = pathlib.Path(sys.argv[0]).name
if name == 'sdkmanager.bat':
    action = 'ndk'
elif name == 'python.exe':
    action = 'verify-stage' if sys.argv[2].endswith('prepare_android_source.py') else 'scons'
else:
    action = 'gradle-offline' if '--offline' in sys.argv else 'gradle-online'
with open(cfg['calls'], 'a') as stream:
    stream.write(json.dumps({'action': action, 'args': sys.argv[1:], 'env': {
        key: os.environ.get(key) for key in ('JAVA_HOME', 'ANDROID_HOME', 'ANDROID_SDK_ROOT', 'GRADLE_USER_HOME')
    }}) + '\n')
print('VISIBLE TOOL OUTPUT: ' + action, flush=True)
if action == 'verify-stage':
    raise SystemExit(subprocess.run([sys.executable] + sys.argv[1:]).returncode)
if action == 'ndk':
    if cfg.get('license_prompt'):
        print('FIXTURE SDK LICENSE PROMPT', flush=True)
        print('LICENSE_REPLY: ' + sys.stdin.readline().strip(), flush=True)
    revision = cfg.get('revision', '26.3.11579264')
    if revision is not None:
        path = pathlib.Path(cfg['sdk']) / 'ndk/26.3.11579264/source.properties'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('Pkg.Revision = ' + revision + '\n')
raise SystemExit(cfg.get(action + '_exit', 0))
'''


class PrepareToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="android setup ")
        self.base = Path(self.temp.name)
        self.repo = self.base / "client with spaces"
        shutil.copytree(ROOT / "engine", self.repo / "engine")
        (self.repo / "tools").mkdir()
        for name in ("Prepare-WindowsAndroidTools.ps1", "android-build-profile.json", "prepare_android_source.py",
                     "prepare_scons.py", "android-cache.init.gradle"):
            shutil.copy2(ROOT / "tools" / name, self.repo / "tools" / name)
        result = subprocess.run([sys.executable, "-I", str(self.repo / "tools/prepare_android_source.py")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.stage = self.repo / "build/android-source"
        self.marker = self.stage / ".wesnoth-android-stage.json"
        self.assertEqual(len(json.loads(self.marker.read_text())["files"]), 1592)
        self.sdk = self.base / "Android SDK"
        self.java = self.base / "Java home"
        self.python = self.base / "Python tools/python.exe"
        self.gradle = self.base / "Gradle cache"
        for path in (self.sdk / "cmdline-tools/latest/bin/sdkmanager.bat", self.java / "bin/java.exe", self.python):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(FAKE_TOOL)
            path.chmod(0o755)
        self.config = {"sdk": str(self.sdk), "calls": str(self.base / "calls.jsonl")}
        self.config_path = self.base / "fixture.json"
        (self.repo / ".env").write_text("DO_NOT_REPORT_PRIVATE_SENTINEL")

    def tearDown(self):
        self.temp.cleanup()

    def run_setup(self, download=True, stdin="", sdk_verbose=False):
        self.config_path.write_text(json.dumps(self.config))
        args = {"SdkRoot": str(self.sdk), "JavaHome": str(self.java), "PythonPath": str(self.python), "GradleUserHome": str(self.gradle)}
        if download:
            args["Download"] = True
        if sdk_verbose:
            args["SdkVerbose"] = True
        parameters = self.base / "parameters.json"
        parameters.write_text(json.dumps(args))
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        wrapper = self.base / "invoke-fixture.ps1"
        wrapper.write_text("""
$parameters = @{}
$inputParameters = Get-Content -LiteralPath PARAMETERS -Raw | ConvertFrom-Json
foreach ($property in $inputParameters.PSObject.Properties) { $parameters[$property.Name] = $property.Value }
& HELPER @parameters
$setupCode = $LASTEXITCODE
$restored = @{}
foreach ($name in @('JAVA_HOME', 'ANDROID_HOME', 'ANDROID_SDK_ROOT', 'GRADLE_USER_HOME')) {
    $restored[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
Write-Host ('FIXTURE_ENV_AFTER: ' + ($restored | ConvertTo-Json -Compress))
exit $setupCode
""".replace("PARAMETERS", quote(parameters)).replace("HELPER", quote(self.repo / "tools/Prepare-WindowsAndroidTools.ps1")))
        env = dict(os.environ, OS="Windows_NT", LOCALAPPDATA=str(self.base), USERPROFILE=str(self.base),
                   WESNOTH_SETUP_FIXTURE=str(self.config_path), **{key: "original " + key for key in ENVIRONMENT})
        result = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-File", str(wrapper)], text=True,
                                input=stdin, capture_output=True, env=env, timeout=45)
        self.output = result.stdout + result.stderr
        calls = Path(self.config["calls"])
        self.calls = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
        restored = next(line.removeprefix("FIXTURE_ENV_AFTER: ") for line in self.output.splitlines()
                        if line.startswith("FIXTURE_ENV_AFTER: "))
        self.assertEqual(json.loads(restored), {key: "original " + key for key in ENVIRONMENT}, self.output)
        self.assertNotIn("DO_NOT_REPORT_PRIVATE_SENTINEL", self.output)
        return result.returncode

    def actions(self):
        return [call["action"] for call in self.calls]

    def assert_stopped(self, actions):
        self.assertEqual(self.run_setup(), 2, self.output)
        self.assertEqual(self.actions(), actions, self.output)
        self.assertNotIn("SETUP COMPLETE:", self.output)

    def test_refuses_without_explicit_download_and_makes_no_calls(self):
        self.assertEqual(self.run_setup(download=False), 2, self.output)
        self.assertEqual(self.calls, [])
        self.assertIn("requires -Download", self.output)

    def test_missing_existing_stage_marker_makes_no_calls(self):
        self.marker.unlink()
        self.assert_stopped([])
        self.assertFalse(self.marker.exists())

    def test_modified_stage_is_preserved_and_prevents_downloads(self):
        source = self.stage / "SConstruct"
        changed = source.read_bytes() + b"\n# local edit\n"
        source.write_bytes(changed)
        self.assert_stopped(["verify-stage"])
        self.assertEqual(source.read_bytes(), changed)
        self.assertIn("Existing staged file was edited", self.output)

    def test_changed_stage_marker_hash_cannot_bypass_source_guard(self):
        source = self.stage / "SConstruct"
        source.write_bytes(source.read_bytes() + b"\n# local edit\n")
        marker = json.loads(self.marker.read_text())
        marker["files"]["SConstruct"] = hashlib.sha256(source.read_bytes()).hexdigest()
        changed = json.dumps(marker).encode()
        self.marker.write_bytes(changed)
        self.assert_stopped(["verify-stage"])
        self.assertEqual(self.marker.read_bytes(), changed)

    def test_extra_stage_file_is_preserved_and_prevents_downloads(self):
        extra = self.stage / "local-note.txt"
        extra.write_text("preserve")
        self.assert_stopped(["verify-stage"])
        self.assertEqual(extra.read_text(), "preserve")

    def test_ndk_install_failure_stops_following_tools(self):
        self.config["ndk_exit"] = 9
        self.assert_stopped(["verify-stage", "ndk"])

    def test_zero_ndk_exit_without_metadata_stops_following_tools(self):
        self.config["revision"] = None
        self.assert_stopped(["verify-stage", "ndk"])

    def test_wrong_ndk_revision_stops_following_tools(self):
        self.config["revision"] = "30.0.14904198"
        self.assert_stopped(["verify-stage", "ndk"])

    def test_scons_failure_stops_gradle(self):
        self.config["scons_exit"] = 2
        self.assert_stopped(["verify-stage", "ndk", "scons"])

    def test_gradle_online_failure_stops_offline_check(self):
        self.config["gradle-online_exit"] = 7
        self.assert_stopped(["verify-stage", "ndk", "scons", "gradle-online"])

    def test_gradle_offline_failure_prevents_complete(self):
        self.config["gradle-offline_exit"] = 8
        self.assert_stopped(["verify-stage", "ndk", "scons", "gradle-online", "gradle-offline"])

    def test_sdk_verbose_changes_only_the_single_sdk_invocation(self):
        self.assertEqual(self.run_setup(sdk_verbose=True), 0, self.output)
        self.assertEqual(self.actions(), ["verify-stage", "ndk", "scons", "gradle-online", "gradle-offline"])
        self.assertEqual(self.calls[1]["args"], ["--sdk_root=" + str(self.sdk), "--install", "ndk;26.3.11579264", "--verbose"])
        for call in self.calls[:1] + self.calls[2:]:
            self.assertNotIn("--verbose", call["args"])
        self.assertIn("Android tools setup 2026-10-12.2", self.output)
        self.assertIn("SETUP COMPLETE:", self.output)

    def test_success_uses_selected_arguments_and_preserves_stage(self):
        before = {path.relative_to(self.stage): path.read_bytes() for path in self.stage.rglob("*") if path.is_file()}
        self.config["license_prompt"] = True
        self.assertEqual(self.run_setup(stdin="yes\n"), 0, self.output)
        self.assertEqual(self.actions(), ["verify-stage", "ndk", "scons", "gradle-online", "gradle-offline"])
        self.assertEqual(self.calls[1]["args"], ["--sdk_root=" + str(self.sdk), "--install", "ndk;26.3.11579264"])
        self.assertEqual(self.calls[2]["args"], ["-I", str(self.repo / "tools/prepare_scons.py"), "prepare", "--download"])
        project = self.stage / "packaging/android"
        expected = ["-classpath", str(project / "gradle/wrapper/gradle-wrapper.jar"), "org.gradle.wrapper.GradleWrapperMain",
                    "--no-daemon", "--console=plain", "--project-dir", str(project), "--project-cache-dir",
                    str(self.repo / "build/android-gradle-cache"), "--init-script", str(self.repo / "tools/android-cache.init.gradle"),
                    ":wesnothResolveAndroidDependencies"]
        self.assertEqual(self.calls[3]["args"], expected)
        self.assertEqual(self.calls[4]["args"], expected + ["--offline"])
        self.assertNotIn(self.stage, Path(expected[expected.index("--project-cache-dir") + 1]).parents)
        expected_env = {"JAVA_HOME": str(self.java), "ANDROID_HOME": str(self.sdk),
                        "ANDROID_SDK_ROOT": str(self.sdk), "GRADLE_USER_HOME": str(self.gradle)}
        for call in self.calls[1:]:
            self.assertEqual(call["env"], expected_env)
        after = {path.relative_to(self.stage): path.read_bytes() for path in self.stage.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertIn("ALREADY PREPARED:", self.output)
        self.assertIn("LICENSE_REPLY: yes", self.output)
        self.assertIn("VISIBLE TOOL OUTPUT: gradle-offline", self.output)
        self.assertIn("SETUP COMPLETE:", self.output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pwsh", required=True)
    args, rest = parser.parse_known_args()
    PWSH = args.pwsh
    unittest.main(argv=[__file__] + rest)
