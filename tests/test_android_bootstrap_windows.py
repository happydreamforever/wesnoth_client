"""Linux PowerShell fixtures for Windows native-build and ADB wrapper control flow.

Tools, receipts and native artifacts are synthetic. No network/device is used;
these tests do not establish Windows compilation or Android runtime success.
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
INPUTS = ("tools/build_android_bootstrap.py", "tools/android-build-profile.json", "tools/android-native-archives.json",
          "tools/native-bootstrap/CMakeLists.txt", "tools/native-bootstrap/probe.cpp")
ARTIFACTS = ("out/bin/wesnoth-native-check", "out/bin/libc++_shared.so", "out/lib/libbz2.a")
SUCCESS = "\n".join(("WESNOTH_NATIVE_BOOTSTRAP version=1.19.26 abi=x86_64 min_api=23", "PASS bzip2 roundtrip",
                     "PASS zlib roundtrip", "PASS C++ STL", "PASS C++ exceptions", "PASS wesnoth-native-bootstrap"))

FAKE_TOOL = r'''#!/usr/bin/python3
import json, os, pathlib, sys
cfg = json.loads(pathlib.Path(os.environ['BOOTSTRAP_FIXTURE']).read_text())
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
    print(cfg.get('connect_output', 'connected to ' + args[5]))
    raise SystemExit(cfg.get('connect_exit', 0))
assert args[4:6] == ['-s', cfg.get('serial', '127.0.0.1:5555')], args
command = args[6:]
if command == ['get-state']:
    print(cfg.get('state', 'device'))
    raise SystemExit(cfg.get('state_exit', 0))
if command[:2] == ['shell', 'getprop']:
    defaults = {'sys.boot_completed': '1', 'ro.build.version.sdk': '28', 'ro.product.cpu.abi': 'x86_64'}
    print(cfg.get('properties', {}).get(command[2], defaults[command[2]]))
elif command[:3] == ['shell', 'mkdir', '-p']:
    pass
elif command[0] == 'push':
    assert pathlib.Path(command[1]).is_file(), command
    print('fixture pushed ' + pathlib.Path(command[1]).name)
    raise SystemExit(cfg.get('push_exit', 0))
elif command[:3] == ['shell', 'chmod', '700']:
    pass
elif command[:2] == ['shell', 'env']:
    print(cfg['runtime_output'])
    print(cfg.get('runtime_stderr', 'native stderr fixture'), file=sys.stderr)
    raise SystemExit(cfg.get('runtime_exit', 0))
else:
    raise AssertionError(command)
'''


class BootstrapWrapperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="native bootstrap ")
        self.base = Path(self.temp.name)
        self.repo = self.base / "client with spaces"
        (self.repo / "tools/native-bootstrap").mkdir(parents=True)
        for name in ("Build-WindowsAndroidBootstrap.ps1", "Test-AndroidBootstrap.ps1"):
            shutil.copy2(ROOT / "tools" / name, self.repo / "tools" / name)
        for relative in INPUTS:
            (self.repo / relative).write_text("synthetic tracked input: " + relative)
        self.build = self.repo / "build/android-native-bootstrap"
        for relative in ARTIFACTS:
            path = self.build / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic native artifact: " + relative.encode())
        metadata = lambda path: {"bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        self.receipt = self.build / "receipt.json"
        self.receipt_data = {
            "schema_version": 1, "kind": "wesnoth-android-native-bootstrap",
            "profile": {"wesnoth_version": "1.19.26", "abi": "x86_64", "min_sdk": 23, "ndk_version": "26.3.11579264"},
            "inputs": {"project_files": {name: metadata(self.repo / name) for name in INPUTS}},
            "artifacts": {name: metadata(self.build / name) for name in ARTIFACTS},
        }
        self.save_receipt()
        self.python = self.base / "Python tools/python.exe"
        self.cmake = self.base / "CMake tools/cmake.exe"
        self.ninja = self.base / "Ninja tools/ninja.exe"
        self.adb = self.base / "SDK tools/adb.exe"
        self.sdk = self.base / "Android SDK"
        for path in (self.python, self.adb, self.cmake, self.ninja):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(FAKE_TOOL)
            path.chmod(0o755)
        self.config = {"calls": str(self.base / "calls.jsonl"), "receipt": str(self.receipt), "runtime_output": SUCCESS}
        self.config_path = self.base / "fixture.json"
        (self.repo / ".env").write_text("PRIVATE_SENTINEL_DO_NOT_PRINT")

    def tearDown(self):
        self.temp.cleanup()

    def save_receipt(self):
        self.receipt.write_text(json.dumps(self.receipt_data))

    def run_wrapper(self, build=False, serial="127.0.0.1:5555", archive=""):
        self.config_path.write_text(json.dumps(self.config))
        Path(self.config["calls"]).unlink(missing_ok=True)
        if build:
            script = "Build-WindowsAndroidBootstrap.ps1"
            args = ["-SdkRoot", str(self.sdk), "-PythonPath", str(self.python), "-CMakePath", str(self.cmake),
                    "-NinjaPath", str(self.ninja), "-Jobs", "3"]
            if archive:
                args += ["-ArchiveDirectory", archive]
        else:
            script = "Test-AndroidBootstrap.ps1"
            args = ["-AdbPath", str(self.adb), "-Serial", serial]
        env = dict(os.environ, OS="Windows_NT", LOCALAPPDATA=str(self.base), BOOTSTRAP_FIXTURE=str(self.config_path))
        result = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-File", str(self.repo / "tools" / script)] + args,
                                capture_output=True, text=True, env=env, timeout=40)
        self.output = result.stdout + result.stderr
        calls = Path(self.config["calls"])
        self.calls = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
        self.assertNotIn("PRIVATE_SENTINEL_DO_NOT_PRINT", self.output)
        return result.returncode

    def test_build_passes_pinned_arguments_without_downloads(self):
        archive = str(self.base / "native archive cache")
        self.assertEqual(self.run_wrapper(build=True, archive=archive), 0, self.output)
        self.assertEqual(self.calls, [{"tool": "python.exe", "args": ["-I", str(self.repo / INPUTS[0]), "--ndk",
            str(self.sdk / "ndk/26.3.11579264"), "--cmake", str(self.cmake), "--ninja", str(self.ninja),
            "--jobs", "3", "--archive-directory", archive]}])
        self.assertIn("VISIBLE BUILDER OUTPUT", self.output)
        self.assertIn("ANDROID NATIVE BUILD COMPLETE:", self.output)

    def test_failed_build_does_not_report_success(self):
        self.config["builder_exit"] = 1
        self.assertEqual(self.run_wrapper(build=True), 2, self.output)
        self.assertNotIn("ANDROID NATIVE BUILD COMPLETE:", self.output)

    def test_silent_build_without_receipt_does_not_report_success(self):
        self.config["builder_receipt"] = False
        self.assertEqual(self.run_wrapper(build=True), 2, self.output)
        self.assertIn("without its artifact receipt", self.output)

    def test_runtime_success_uses_explicit_device_and_isolated_remote_paths(self):
        before = {name: (self.build / name).read_bytes() for name in ARTIFACTS}
        self.assertEqual(self.run_wrapper(), 0, self.output)
        self.assertEqual(self.calls[0]["args"], ["-H", "localhost", "-P", "5037", "connect", "127.0.0.1:5555"])
        commands = [call["args"][6:] for call in self.calls[1:]]
        for call in self.calls[1:]:
            self.assertEqual(call["args"][:6], ["-H", "localhost", "-P", "5037", "-s", "127.0.0.1:5555"])
        self.assertEqual(commands[:4], [["get-state"], ["shell", "getprop", "sys.boot_completed"],
            ["shell", "getprop", "ro.build.version.sdk"], ["shell", "getprop", "ro.product.cpu.abi"]])
        remote = commands[4][3]
        self.assertRegex(remote, r"^/data/local/tmp/wesnoth-native-bootstrap/[0-9a-f]{32}$")
        self.assertEqual(commands[4], ["shell", "mkdir", "-p", remote])
        self.assertEqual(commands[5:7], [["push", str(self.build / "out/bin" / name), remote + "/" + name]
                                       for name in ("wesnoth-native-check", "libc++_shared.so")])
        self.assertEqual(commands[7], ["shell", "chmod", "700", remote + "/wesnoth-native-check"])
        self.assertEqual(commands[8], ["shell", "env", "LD_LIBRARY_PATH=" + remote, remote + "/wesnoth-native-check"])
        self.assertEqual(len(commands), 9)
        self.assertEqual(before, {name: (self.build / name).read_bytes() for name in ARTIFACTS})
        self.assertIn("native stderr fixture", self.output)
        self.assertIn("ANDROID NATIVE BOOTSTRAP TEST PASSED", self.output)
        self.assertIn("did not build an APK", self.output)

    def test_changed_artifact_blocks_before_adb(self):
        artifact = self.build / ARTIFACTS[0]
        data = artifact.read_bytes()
        artifact.write_bytes(bytes([data[0] ^ 1]) + data[1:])
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls, [])
        self.assertIn("differs from its receipt", self.output)

    def test_changed_tracked_input_blocks_before_adb(self):
        (self.repo / INPUTS[-1]).write_text("changed probe source")
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls, [])
        self.assertIn("differs from its receipt", self.output)

    def test_mismatched_profile_blocks_before_adb(self):
        self.receipt_data["profile"]["abi"] = "arm64-v8a"
        self.save_receipt()
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls, [])

    def test_unexpected_receipt_path_blocks_before_adb(self):
        self.receipt_data["artifacts"]["../.env"] = self.receipt_data["artifacts"].pop(ARTIFACTS[0])
        self.save_receipt()
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls, [])

    def test_missing_receipt_blocks_before_adb(self):
        self.receipt.unlink()
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls, [])

    def test_symlinked_artifact_blocks_before_adb(self):
        artifact = self.build / ARTIFACTS[0]
        target = self.base / "redirected artifact"
        artifact.rename(target)
        artifact.symlink_to(target)
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls, [])
        self.assertIn("reparse points", self.output)

    def test_unready_device_blocks_after_connect_even_with_exit_zero(self):
        self.config.update(connect_output="failed to connect", state="offline")
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(len(self.calls), 2)
        self.assertNotIn("ANDROID NATIVE BOOTSTRAP TEST PASSED", self.output)

    def test_incompatible_or_unbooted_devices_are_never_modified(self):
        for properties in ({"ro.build.version.sdk": "22"}, {"ro.product.cpu.abi": "arm64-v8a"}, {"sys.boot_completed": "0"}):
            with self.subTest(properties=properties):
                self.config["properties"] = properties
                self.assertEqual(self.run_wrapper(), 2, self.output)
                self.assertEqual(len(self.calls), 5)
                self.assertNotIn("Native test directory:", self.output)

    def test_failed_push_prevents_chmod_and_execution(self):
        self.config["push_exit"] = 1
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls[-1]["args"][6], "push")
        self.assertNotIn("ANDROID NATIVE BOOTSTRAP TEST PASSED", self.output)

    def test_native_failure_exit_cannot_pass_from_success_text(self):
        self.config["runtime_exit"] = 1
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertNotIn("ANDROID NATIVE BOOTSTRAP TEST PASSED", self.output)

    def test_native_zero_exit_without_all_checks_cannot_pass(self):
        self.config["runtime_output"] = SUCCESS.replace("PASS C++ exceptions", "")
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertIn("did not confirm: PASS C++ exceptions", self.output)

    def test_native_stderr_failure_cannot_pass_with_zero_exit(self):
        self.config["runtime_stderr"] = "FAIL wesnoth-native-bootstrap: injected failure"
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertIn("reported a failure despite its exit code", self.output)

    def test_shell_metacharacters_in_serial_are_rejected_without_adb(self):
        self.assertNotEqual(self.run_wrapper(serial="127.0.0.1:5555;echo unsafe"), 0, self.output)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pwsh", required=True)
    args, rest = parser.parse_known_args()
    PWSH = args.pwsh
    unittest.main(argv=[__file__] + rest)
