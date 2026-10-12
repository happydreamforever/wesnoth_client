"""Linux PowerShell control-flow fixtures; synthetic APKs, tools and ADB only.

These checks do not establish a Windows APK build or an Android rendering result.
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
INPUTS = ("tools/build_android_sdl_check.py", "tools/build_android_bootstrap.py",
          "tools/android-build-profile.json", "tools/android-native-archives.json",
          "tools/sdl-check/settings.gradle", "tools/sdl-check/build.gradle", "tools/sdl-check/app.build.gradle",
          "tools/sdl-check/gradle.properties", "tools/sdl-check/AndroidManifest.xml", "tools/sdl-check/CheckActivity.java",
          "tools/sdl-check/native/CMakeLists.txt", "tools/sdl-check/native/main.cpp")
ARTIFACTS = ("app/build/outputs/apk/debug/app-debug.apk", "app/src/main/jniLibs/x86_64/libSDL3.so",
             "app/src/main/jniLibs/x86_64/libmain.so", "app/src/main/jniLibs/x86_64/libc++_shared.so")
PACKAGE = "org.wesnoth.offlinecheck"
FAKE_TOOL = r'''#!/usr/bin/python3
import json, os, pathlib, sys
cfg = json.loads(pathlib.Path(os.environ['SDL_FIXTURE']).read_text())
args = sys.argv[1:]
name = pathlib.Path(sys.argv[0]).name
with open(cfg['calls'], 'a') as stream:
    stream.write(json.dumps({'tool': name, 'args': args}) + '\n')
if name == 'python.exe':
    if not cfg.get('builder_receipt', True):
        pathlib.Path(cfg['receipt']).unlink(missing_ok=True)
    print('VISIBLE BUILDER OUTPUT')
    raise SystemExit(cfg.get('builder_exit', 0))
assert name == 'adb.exe'
assert args[:4] == ['-H', 'localhost', '-P', '5037'], args
if args[4] == 'connect':
    print('already connected to ' + args[5])
    raise SystemExit(0)
assert args[4:6] == ['-s', '127.0.0.1:5555'], args
command = args[6:]
state_path = pathlib.Path(cfg['state'])
state = json.loads(state_path.read_text()) if state_path.exists() else {'log_reads': 0}
if command == ['get-state']:
    print(cfg.get('device_state', 'device'))
elif command[:2] == ['shell', 'getprop']:
    defaults = {'sys.boot_completed': '1', 'ro.build.version.sdk': '28', 'ro.product.cpu.abi': 'x86_64'}
    print(cfg.get('properties', {}).get(command[2], defaults[command[2]]))
elif command[:2] == ['install', '-r']:
    assert pathlib.Path(command[2]).is_file()
    print(cfg.get('install_output', 'Success'))
    raise SystemExit(cfg.get('install_exit', 0))
elif command == ['shell', 'am', 'force-stop', 'org.wesnoth.offlinecheck']:
    pass
elif command[:4] == ['shell', 'am', 'start', '-W']:
    assert command[4:8] == ['-n', 'org.wesnoth.offlinecheck/.CheckActivity', '--es', 'wesnoth_run_id']
    state['run_id'] = command[8]
    print(cfg.get('launch_output', 'Status: ok\nActivity: org.wesnoth.offlinecheck/.CheckActivity'))
    state_path.write_text(json.dumps(state))
    raise SystemExit(cfg.get('launch_exit', 0))
elif command == ['shell', 'pidof', 'org.wesnoth.offlinecheck']:
    print(cfg.get('pid', '4242'))
elif command == ['logcat', '-d', '--pid=4242', '-v', 'brief']:
    state['log_reads'] += 1
    token = state['run_id']
    mode = cfg.get('logs_mode', 'success')
    print('SDL: WESNOTH_SDL_CHECK FAIL run_id=' + '0' * 32 + ' stage=old error=stale')
    if mode == 'stale':
        token = '0' * 32
    if mode in ('native_fail', 'input_fail') and (mode == 'native_fail' or state.get('tapped')):
        print('SDL: WESNOTH_SDL_CHECK FAIL run_id=' + token + ' stage=render error=fixture')
    if mode != 'no_ready':
        dims = cfg.get('dimensions', '1920 1080').split()
        print('SDL: WESNOTH_SDL_CHECK READY run_id=' + token + ' width=' + dims[0] + ' height=' + dims[1] +
              ' sdl=3.4.2 renderer=opengles2 abi=x86_64 min_api=23')
    if state.get('tapped') and mode != 'no_input':
        print('SDL: WESNOTH_SDL_CHECK INPUT run_id=' + token + ' count=1')
    state_path.write_text(json.dumps(state))
elif command[:3] == ['shell', 'input', 'tap']:
    state['tapped'] = True
    state_path.write_text(json.dumps(state))
    raise SystemExit(cfg.get('tap_exit', 0))
else:
    raise AssertionError(command)
'''


class SdlWrapperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="SDL APK fixture ")
        self.base = Path(self.temp.name)
        self.repo = self.base / "client with spaces"
        (self.repo / "tools").mkdir(parents=True)
        for name in ("Build-WindowsSdlCheck.ps1", "Test-AndroidSdlCheck.ps1"):
            shutil.copy2(ROOT / "tools" / name, self.repo / "tools" / name)
        for relative in INPUTS:
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic source: " + relative)
        self.build = self.repo / "build/android-sdl-apk"
        for relative in ARTIFACTS:
            path = self.build / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic APK/native payload: " + relative.encode())
        metadata = lambda path: {"bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        self.receipt = self.build / "receipt.json"
        self.receipt_data = {
            "schema_version": 1, "kind": "wesnoth-android-sdl-check", "package_name": PACKAGE,
            "activity": PACKAGE + ".CheckActivity", "sdl_version": "3.4.2",
            "profile": {"wesnoth_version": "1.19.26", "abi": "x86_64", "min_sdk": 23, "ndk_version": "26.3.11579264"},
            "inputs": {"project_files": {name: metadata(self.repo / name) for name in INPUTS}},
            "artifacts": {name: metadata(self.build / name) for name in ARTIFACTS},
        }
        self.save_receipt()
        self.python = self.base / "Python tools/python.exe"
        self.java_home = self.base / "Java home"
        self.java = self.java_home / "bin/java.exe"
        self.cmake = self.base / "CMake tools/cmake.exe"
        self.ninja = self.base / "Ninja tools/ninja.exe"
        self.adb = self.base / "SDK tools/adb.exe"
        self.sdk = self.base / "Android SDK"
        self.gradle = self.base / "Gradle cache"
        for path in (self.python, self.java, self.adb, self.cmake, self.ninja):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(FAKE_TOOL)
            path.chmod(0o755)
        for relative, revision in (("platforms/android-35", "2"), ("build-tools/34.0.0", "34.0.0")):
            folder = self.sdk / relative
            folder.mkdir(parents=True)
            (folder / "source.properties").write_text("Pkg.Revision=" + revision)
        self.config = {"calls": str(self.base / "calls.jsonl"), "receipt": str(self.receipt),
                       "state": str(self.base / "device-state.json")}
        self.config_path = self.base / "fixture.json"
        (self.repo / ".env").write_text("PRIVATE_SENTINEL_DO_NOT_PRINT")

    def tearDown(self):
        self.temp.cleanup()

    def save_receipt(self):
        self.receipt.write_text(json.dumps(self.receipt_data))

    def run_wrapper(self, build=False, serial="127.0.0.1:5555", archive="", fast_timeout=False):
        self.config_path.write_text(json.dumps(self.config))
        Path(self.config["calls"]).unlink(missing_ok=True)
        Path(self.config["state"]).unlink(missing_ok=True)
        if build:
            script = "Build-WindowsSdlCheck.ps1"
            args = ["-SdkRoot", str(self.sdk), "-JavaHome", str(self.java_home), "-PythonPath", str(self.python),
                    "-CMakePath", str(self.cmake), "-NinjaPath", str(self.ninja), "-GradleUserHome", str(self.gradle), "-Jobs", "3"]
            if archive:
                args += ["-ArchiveDirectory", archive]
        else:
            script = "Test-AndroidSdlCheck.ps1"
            args = ["-AdbPath", str(self.adb), "-Serial", serial]
        script_path = self.repo / "tools" / script
        if fast_timeout:
            text = script_path.read_text()
            self.assertIn("$timer.Elapsed.TotalSeconds -ge 30", text)
            script_path.write_text(text.replace("$timer.Elapsed.TotalSeconds -ge 30", "$timer.Elapsed.TotalSeconds -ge 0"))
        env = dict(os.environ, OS="Windows_NT", LOCALAPPDATA=str(self.base), USERPROFILE=str(self.base),
                   SDL_FIXTURE=str(self.config_path))
        result = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-File", str(script_path)] + args,
                                capture_output=True, text=True, env=env, timeout=45)
        self.output = result.stdout + result.stderr
        calls = Path(self.config["calls"])
        self.calls = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
        self.assertNotIn("PRIVATE_SENTINEL_DO_NOT_PRINT", self.output)
        return result.returncode

    def test_build_passes_selected_offline_arguments_and_reports_sdk_revisions(self):
        archive = str(self.base / "native archive cache")
        self.assertEqual(self.run_wrapper(build=True, archive=archive), 0, self.output)
        self.assertEqual(self.calls, [{"tool": "python.exe", "args": ["-I", str(self.repo / INPUTS[0]), "--ndk",
            str(self.sdk / "ndk/26.3.11579264"), "--cmake", str(self.cmake), "--ninja", str(self.ninja),
            "--java", str(self.java), "--sdk", str(self.sdk), "--gradle-user-home", str(self.gradle),
            "--jobs", "3", "--archive-directory", archive]}])
        self.assertIn("Pkg.Revision=2", self.output)
        self.assertIn("Pkg.Revision=34.0.0", self.output)
        self.assertIn("VISIBLE BUILDER OUTPUT", self.output)
        self.assertIn("SDL CHECK APK BUILD COMPLETE:", self.output)

    def test_build_failure_and_silent_missing_receipt_cannot_pass(self):
        for change in ({"builder_exit": 1}, {"builder_receipt": False}):
            with self.subTest(change=change):
                self.config.update(change)
                self.assertEqual(self.run_wrapper(build=True), 2, self.output)
                self.assertNotIn("SDL CHECK APK BUILD COMPLETE:", self.output)
                self.config.pop("builder_exit", None)

    def test_missing_sdk_metadata_stops_before_build(self):
        (self.sdk / "platforms/android-35/source.properties").unlink()
        self.assertEqual(self.run_wrapper(build=True), 2, self.output)
        self.assertEqual(self.calls, [])

    def test_success_install_launch_token_pid_logs_and_drawable_center_tap(self):
        before = {name: (self.build / name).read_bytes() for name in ARTIFACTS}
        self.assertEqual(self.run_wrapper(), 0, self.output)
        commands = [call["args"][6:] for call in self.calls[1:]]
        self.assertEqual(self.calls[0]["args"], ["-H", "localhost", "-P", "5037", "connect", "127.0.0.1:5555"])
        self.assertEqual(commands[4], ["install", "-r", str(self.build / ARTIFACTS[0])])
        self.assertEqual(commands[5], ["shell", "am", "force-stop", PACKAGE])
        self.assertEqual(commands[6][:8], ["shell", "am", "start", "-W", "-n", PACKAGE + "/.CheckActivity", "--es", "wesnoth_run_id"])
        self.assertRegex(commands[6][8], r"^[0-9a-f]{32}$")
        self.assertIn(["shell", "input", "tap", "960", "540"], commands)
        logs = [cmd for cmd in commands if cmd[0] == "logcat"]
        self.assertEqual(logs, [["logcat", "-d", "--pid=4242", "-v", "brief"]] * 2)
        self.assertEqual(len(self.calls), 12)
        self.assertNotIn("uninstall", str(commands))
        self.assertNotIn("'-c'", str(commands))
        self.assertIn("ANDROID SDL CHECK AUTOMATED TEST PASSED", self.output)
        self.assertIn("Click the colored target", self.output)
        self.assertEqual(before, {name: (self.build / name).read_bytes() for name in ARTIFACTS})

    def test_changed_source_or_artifact_blocks_before_adb(self):
        for path in (self.repo / INPUTS[0], self.build / ARTIFACTS[0]):
            with self.subTest(path=path):
                old = path.read_bytes()
                path.write_bytes(old + b"edited")
                self.assertEqual(self.run_wrapper(), 2, self.output)
                self.assertEqual(self.calls, [])
                path.write_bytes(old)

    def test_wrong_app_profile_or_unexpected_file_entries_block_before_adb(self):
        self.receipt_data["package_name"] = "org.wesnoth.Wesnoth"
        self.save_receipt()
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls, [])
        self.receipt_data["package_name"] = PACKAGE
        self.receipt_data["artifacts"]["../unrelated"] = {"bytes": 1, "sha256": "0" * 64}
        self.save_receipt()
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls, [])

    def test_symlink_artifact_blocks_before_adb(self):
        artifact = self.build / ARTIFACTS[0]
        target = self.base / "redirected.apk"
        artifact.rename(target)
        artifact.symlink_to(target)
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls, [])

    def test_redirected_build_parent_blocks_before_adb(self):
        build_parent = self.repo / "build"
        target = self.base / "redirected build"
        build_parent.rename(target)
        build_parent.symlink_to(target, target_is_directory=True)
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls, [])

    def test_unready_or_incompatible_device_is_not_modified(self):
        for change in ({"device_state": "offline"}, {"properties": {"ro.build.version.sdk": "22"}},
                       {"properties": {"ro.product.cpu.abi": "arm64-v8a"}}, {"properties": {"sys.boot_completed": "0"}}):
            with self.subTest(change=change):
                self.config.update(change)
                self.assertEqual(self.run_wrapper(), 2, self.output)
                self.assertFalse(any(call["args"][6:7] == ["install"] for call in self.calls))
                self.config.pop("device_state", None)
                self.config.pop("properties", None)

    def test_install_exit_or_missing_success_prevents_launch(self):
        for change in ({"install_exit": 1}, {"install_output": "Failure [INSTALL_FAILED_TEST]"}):
            with self.subTest(change=change):
                self.config.update(change)
                self.assertEqual(self.run_wrapper(), 2, self.output)
                self.assertEqual(self.calls[-1]["args"][6], "install")
                self.config.pop("install_exit", None)

    def test_launch_silent_error_prevents_pid_and_input(self):
        self.config["launch_output"] = "Error: Activity not found"
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls[-1]["args"][6:9], ["shell", "am", "start"])

    def test_invalid_pid_prevents_logcat_and_input(self):
        self.config["pid"] = "4242 5151"
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls[-1]["args"][6:8], ["shell", "pidof"])

    def test_native_failure_prevents_automated_input(self):
        self.config["logs_mode"] = "native_fail"
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertIn("native failure", self.output)
        self.assertFalse(any(call["args"][6:8] == ["shell", "input"] for call in self.calls))

    def test_stale_run_markers_and_missing_ready_timeout_without_tap(self):
        for mode in ("stale", "no_ready"):
            with self.subTest(mode=mode):
                self.config["logs_mode"] = mode
                shutil.copy2(ROOT / "tools/Test-AndroidSdlCheck.ps1", self.repo / "tools/Test-AndroidSdlCheck.ps1")
                self.assertEqual(self.run_wrapper(fast_timeout=True), 2, self.output)
                self.assertIn("Timed out", self.output)
                self.assertFalse(any(call["args"][6:8] == ["shell", "input"] for call in self.calls))

    def test_invalid_ready_dimensions_cannot_generate_input(self):
        self.config["dimensions"] = "0 1080"
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertIn("invalid drawable dimensions", self.output)

    def test_tap_failure_missing_input_or_native_input_failure_cannot_pass(self):
        for change in ({"tap_exit": 1}, {"logs_mode": "no_input"}, {"logs_mode": "input_fail"}):
            with self.subTest(change=change):
                self.config.update(change)
                shutil.copy2(ROOT / "tools/Test-AndroidSdlCheck.ps1", self.repo / "tools/Test-AndroidSdlCheck.ps1")
                self.assertEqual(self.run_wrapper(fast_timeout=True), 2, self.output)
                self.assertNotIn("ANDROID SDL CHECK AUTOMATED TEST PASSED", self.output)
                self.config.pop("tap_exit", None)

    def test_shell_metacharacters_in_serial_are_rejected_before_adb(self):
        self.assertNotEqual(self.run_wrapper(serial="127.0.0.1:5555;echo bad"), 0, self.output)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pwsh", required=True)
    args, rest = parser.parse_known_args()
    PWSH = args.pwsh
    unittest.main(argv=[__file__] + rest)
