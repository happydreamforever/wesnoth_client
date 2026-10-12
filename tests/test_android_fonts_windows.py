"""Linux PowerShell fixtures for the font-build and ADB wrapper control flow.

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
INPUTS = ("tools/build_android_fonts.py", "tools/build_android_media.py", "tools/build_android_bootstrap.py", "tools/android-build-profile.json",
          "tools/android-native-archives.json", "tools/native-fonts/build-plan.json",
          "tools/native-fonts/CMakeLists.txt", "tools/native-fonts/probe.cpp", "tools/native-fonts/assets.json")
ARTIFACTS = ("prefix/bin/wesnoth-fonts-check", "prefix/lib/libc++_shared.so")
FONT_FILES = ("fonts/Lato-Regular.ttf", "fonts/DejaVuSans.ttf", "fonts/COPYING")
PREFIX_EXTRA = ("prefix/include/freetype2/ft2build.h", "prefix/lib/libfreetype.a", "prefix/lib/libharfbuzz.a",
                "prefix/share/licenses/wesnoth-fonts/freetype/FTL.TXT")
SUCCESS = "\n".join(("WESNOTH_ANDROID_FONTS version=1.19.26 abi=x86_64 min_api=23", "PASS FreeType font loading", "PASS HarfBuzz Latin ligatures", "PASS HarfBuzz Arabic shaping",
                     "PASS FreeType glyph rasterization", "PASS wesnoth-android-fonts"))

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


class FontsWrapperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="native fonts ")
        self.base = Path(self.temp.name)
        self.repo = self.base / "client with spaces"
        (self.repo / "tools/native-fonts").mkdir(parents=True)
        for name in ("Build-WindowsAndroidFonts.ps1", "Test-AndroidFonts.ps1"):
            shutil.copy2(ROOT / "tools" / name, self.repo / "tools" / name)
        for relative in INPUTS:
            (self.repo / relative).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / relative).write_text("synthetic tracked input: " + relative)
        self.build = self.repo / "build/android-fonts"
        for relative in ARTIFACTS + PREFIX_EXTRA:
            path = self.build / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic native artifact: " + relative.encode())
        metadata = lambda path: {"bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        self.data = self.base / "game data with spaces"
        for name in FONT_FILES:
            path = self.data / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic selected asset: " + name.encode())
        self.asset_data = {"schema_version": 1, "game_data_version": "1.19.26",
                           "files": {name: metadata(self.data / name) for name in FONT_FILES}}
        (self.repo / INPUTS[-1]).write_text(json.dumps(self.asset_data))
        self.receipt = self.build / "receipt.json"
        self.receipt_data = {
            "schema_version": 1, "kind": "wesnoth-android-fonts",
            "profile": {"wesnoth_version": "1.19.26", "abi": "x86_64", "min_sdk": 23, "ndk_version": "26.3.11579264"},
            "inputs": {"project_files": {name: metadata(self.repo / name) for name in INPUTS}},
            "artifacts": {name: metadata(self.build / name) for name in ARTIFACTS},
        }
        self.receipt_data["prefix_files"] = {name: metadata(self.build / name) for name in ARTIFACTS + PREFIX_EXTRA}
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

    def run_wrapper(self, build=False, serial="127.0.0.1:5555", archive="", data=None, default_data=False):
        self.config_path.write_text(json.dumps(self.config))
        Path(self.config["calls"]).unlink(missing_ok=True)
        if build:
            script = "Build-WindowsAndroidFonts.ps1"
            args = ["-SdkRoot", str(self.sdk), "-PythonPath", str(self.python), "-CMakePath", str(self.cmake),
                    "-NinjaPath", str(self.ninja), "-Jobs", "3"]
            if archive:
                args += ["-ArchiveDirectory", archive]
        else:
            script = "Test-AndroidFonts.ps1"
            args = ["-AdbPath", str(self.adb), "-Serial", serial]
            if not default_data:
                args += ["-DataRoot", str(self.data if data is None else data)]
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
        self.assertIn("ANDROID FONTS BUILD COMPLETE:", self.output)

    def test_failed_build_does_not_report_success(self):
        self.config["builder_exit"] = 1
        self.assertEqual(self.run_wrapper(build=True), 2, self.output)
        self.assertNotIn("ANDROID FONTS BUILD COMPLETE:", self.output)

    def test_silent_build_without_receipt_does_not_report_success(self):
        self.config["builder_receipt"] = False
        self.assertEqual(self.run_wrapper(build=True), 2, self.output)
        self.assertIn("without its font artifact receipt", self.output)

    def test_runtime_success_uses_explicit_device_and_isolated_remote_paths(self):
        before = {name: (self.build / name).read_bytes() for name in ARTIFACTS}
        self.assertEqual(self.run_wrapper(), 0, self.output)
        self.assertEqual(self.calls[0]["args"], ["-H", "localhost", "-P", "5037", "connect", "127.0.0.1:5555"])
        commands = [call["args"][6:] for call in self.calls[1:]]
        for call in self.calls[1:]:
            self.assertEqual(call["args"][:6], ["-H", "localhost", "-P", "5037", "-s", "127.0.0.1:5555"])
        self.assertEqual(commands[:4], [["get-state"], ["shell", "getprop", "sys.boot_completed"],
            ["shell", "getprop", "ro.build.version.sdk"], ["shell", "getprop", "ro.product.cpu.abi"]])
        remote = commands[4][3].removesuffix("/bin")
        self.assertRegex(remote, r"^/data/local/tmp/wesnoth-fonts-check/[0-9a-f]{32}$")
        self.assertEqual(commands[4], ["shell", "mkdir", "-p", remote + "/bin", remote + "/lib", remote + "/fonts"])
        self.assertEqual(commands[5:7], [["push", str(self.build / name), remote + name.removeprefix("prefix")]
                                        for name in ARTIFACTS])
        self.assertEqual(commands[7:10], [["push", str(self.data / name), remote + "/" + name] for name in FONT_FILES])
        self.assertEqual(commands[10], ["shell", "chmod", "700", remote + "/bin/wesnoth-fonts-check"])
        self.assertEqual(commands[11], ["shell", "env", "LD_LIBRARY_PATH=" + remote + "/lib", remote + "/bin/wesnoth-fonts-check", remote + "/fonts"])
        self.assertEqual(len(commands), 12)
        self.assertEqual(before, {name: (self.build / name).read_bytes() for name in ARTIFACTS})
        self.assertIn("native stderr fixture", self.output)
        self.assertIn("ANDROID FONTS TEST PASSED", self.output)
        self.assertIn("No APK change, full game-data installation", self.output)

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

    def test_selected_font_and_license_tampering_blocks_before_adb(self):
        for name in FONT_FILES:
            with self.subTest(name=name):
                path = self.data / name
                original = path.read_bytes()
                path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
                self.assertEqual(self.run_wrapper(), 2, self.output)
                self.assertEqual(self.calls, [])
                self.assertIn("differs from its receipt", self.output)
                path.write_bytes(original)

    def test_missing_font_empty_license_and_missing_data_root_block_before_adb(self):
        font = self.data / FONT_FILES[0]
        original = font.read_bytes()
        font.unlink()
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls, [])
        font.write_bytes(original)
        (self.data / FONT_FILES[2]).write_bytes(b"")
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.run_wrapper(data=self.base / "missing data"), 2, self.output)
        self.assertEqual(self.calls, [])

    def test_redirected_font_and_data_root_block_before_adb(self):
        font = self.data / FONT_FILES[0]
        moved = self.base / "outside font"
        font.rename(moved)
        font.symlink_to(moved)
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls, [])
        self.assertIn("reparse points", self.output)
        font.unlink()
        moved.rename(font)
        link = self.base / "data link"
        link.symlink_to(self.data, target_is_directory=True)
        self.assertEqual(self.run_wrapper(data=link), 2, self.output)
        self.assertEqual(self.calls, [])

    def test_wrong_asset_version_or_unexpected_asset_path_blocks_before_adb(self):
        for scenario in ("version", "path"):
            with self.subTest(scenario=scenario):
                manifest = dict(self.asset_data)
                if scenario == "version":
                    manifest["game_data_version"] = "1.19.25"
                else:
                    manifest["files"] = dict(manifest["files"])
                    manifest["files"]["../.env"] = manifest["files"].pop(FONT_FILES[0])
                path = self.repo / INPUTS[-1]
                path.write_text(json.dumps(manifest))
                self.receipt_data["inputs"]["project_files"][INPUTS[-1]] = {
                    "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                self.save_receipt()
                self.assertEqual(self.run_wrapper(), 2, self.output)
                self.assertEqual(self.calls, [])

    def test_default_sibling_data_root_and_unrelated_data_remain_untouched(self):
        self.data.rename(self.repo.parent / "gamedata-1.19.26")
        self.data = self.repo.parent / "gamedata-1.19.26"
        unrelated = self.data / "unselected asset.bin"
        unrelated.write_bytes(b"unselected game data")
        before = {path.relative_to(self.data).as_posix(): path.read_bytes()
                  for path in self.data.rglob("*") if path.is_file()}
        self.assertEqual(self.run_wrapper(default_data=True), 0, self.output)
        after = {path.relative_to(self.data).as_posix(): path.read_bytes()
                 for path in self.data.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        pushes = [call["args"][7] for call in self.calls if call["args"][6:7] == ["push"]]
        self.assertEqual(len(pushes), 5)
        self.assertNotIn(str(unrelated), pushes)

    def test_build_is_independent_of_game_data(self):
        shutil.rmtree(self.data)
        self.assertEqual(self.run_wrapper(build=True), 0, self.output)
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("--data-root", self.calls[0]["args"])

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

    def test_changed_header_in_complete_prefix_blocks_before_adb(self):
        (self.build / PREFIX_EXTRA[0]).write_text("edited installed header")
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls, [])
        self.assertIn("differs from its receipt", self.output)

    def test_missing_or_unrecorded_prefix_files_block_before_adb(self):
        header = self.build / PREFIX_EXTRA[0]
        data = header.read_bytes()
        header.unlink()
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls, [])
        header.write_bytes(data)
        (self.build / "prefix/include/unrecorded.h").write_text("unrecorded")
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls, [])
        self.assertIn("missing or unrecorded", self.output)

    def test_unsafe_or_empty_prefix_inventory_is_rejected_before_adb(self):
        records = dict(self.receipt_data["prefix_files"])
        for name in ("prefix/../.env", "prefix/include/.env", "C:/outside.h", "prefix/include//header.h"):
            with self.subTest(name=name):
                self.receipt_data["prefix_files"] = dict(records)
                self.receipt_data["prefix_files"][name] = {"bytes": 1, "sha256": "0" * 64}
                self.save_receipt()
                self.assertEqual(self.run_wrapper(), 2, self.output)
                self.assertEqual(self.calls, [])
                self.assertIn("Unsafe installed-prefix", self.output)
        self.receipt_data["prefix_files"] = {}
        self.save_receipt()
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls, [])

    def test_prefix_header_symlink_and_redirected_build_parent_are_rejected(self):
        header = self.build / PREFIX_EXTRA[0]
        target = self.base / "redirected header"
        header.rename(target)
        header.symlink_to(target)
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls, [])
        self.assertIn("Redirected installed-prefix", self.output)
        header.unlink()
        target.rename(header)
        build_parent = self.repo / "build"
        destination = self.base / "redirected build"
        build_parent.rename(destination)
        build_parent.symlink_to(destination, target_is_directory=True)
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls, [])

    def test_unready_device_blocks_after_connect_even_with_exit_zero(self):
        self.config.update(connect_output="failed to connect", state="offline")
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(len(self.calls), 2)
        self.assertNotIn("ANDROID FONTS TEST PASSED", self.output)

    def test_incompatible_or_unbooted_devices_are_never_modified(self):
        for properties in ({"ro.build.version.sdk": "22"}, {"ro.product.cpu.abi": "arm64-v8a"}, {"sys.boot_completed": "0"}):
            with self.subTest(properties=properties):
                self.config["properties"] = properties
                self.assertEqual(self.run_wrapper(), 2, self.output)
                self.assertEqual(len(self.calls), 5)
                self.assertNotIn("Fonts test directory:", self.output)

    def test_failed_push_prevents_chmod_and_execution(self):
        self.config["push_exit"] = 1
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls[-1]["args"][6], "push")
        self.assertNotIn("ANDROID FONTS TEST PASSED", self.output)

    def test_native_failure_exit_cannot_pass_from_success_text(self):
        self.config["runtime_exit"] = 1
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertNotIn("ANDROID FONTS TEST PASSED", self.output)

    def test_native_zero_exit_without_all_checks_cannot_pass(self):
        self.config["runtime_output"] = SUCCESS.replace("PASS HarfBuzz Arabic shaping", "")
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertIn("did not confirm: PASS HarfBuzz Arabic shaping", self.output)

    def test_native_stderr_failure_cannot_pass_with_zero_exit(self):
        self.config["runtime_stderr"] = "FAIL wesnoth-android-fonts: injected failure"
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
