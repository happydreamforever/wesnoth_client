"""Linux PowerShell fixtures; Windows executables and ADB are simulated.

Real archive/receipt/ELF validation belongs to test_android_boost.py. These tests
check the native process boundary, selected-device guards and failure handoff.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PWSH = None
BUILD = "Build-WindowsAndroidBoost.ps1"
TEST = "Test-AndroidBoost.ps1"
SUCCESS = "\n".join(("WESNOTH_ANDROID_BOOST version=1.19.26 abi=x86_64 min_api=23",
    "PASS Boost.Locale ICU backend and embedded data",
    "PASS Boost.Filesystem and Process scoped file operations",
    "PASS Boost.ProgramOptions Random Charconv and Regex",
    "PASS Boost.Iostreams gzip and bzip2 round trips",
    "PASS Boost.Thread Context Coroutine and Asio",
    "PASS wesnoth-android-boost"))
FAKE_TOOL = r'''#!/usr/bin/python3
import json, os, pathlib, signal, sys, time
cfg = json.loads(pathlib.Path(os.environ['BOOST_FIXTURE']).read_text())
args = sys.argv[1:]
name = pathlib.Path(sys.argv[0]).name
with open(cfg['calls'], 'a') as stream:
    stream.write(json.dumps({'tool': name, 'args': args, 'pid': os.getpid(), 'cwd': os.getcwd(),
        'path': os.environ.get('PATH'), 'msystem': os.environ.get('MSYSTEM'),
        'bash_env': os.environ.get('BASH_ENV'), 'env': os.environ.get('ENV'),
        'pythonhome': os.environ.get('PYTHONHOME'), 'pythonpath': os.environ.get('PYTHONPATH'),
        'nobytecode': os.environ.get('PYTHONDONTWRITEBYTECODE'),
        'nouser': os.environ.get('PYTHONNOUSERSITE')}) + '\n')
if name == 'taskkill.exe':
    assert args[::2] == ['/PID', '/T'] and args[-1] == '/F', args
    os.killpg(int(args[1]), signal.SIGKILL)
    raise SystemExit(0)
if name == 'python.exe' and '--ndk' in args:
    assert sys.stdin.read() == '', 'Builder standard input must be valid and closed.'
    print('VISIBLE BUILD OUTPUT', flush=True)
    print(cfg.get('build_stderr', 'VISIBLE BUILD STDERR'), file=sys.stderr, flush=True)
    for index in range(cfg.get('bulk_lines', 0)):
        print('BULK STDOUT ' + str(index) + ' x' * 40, flush=True)
    for index in range(cfg.get('bulk_lines', 0)):
        print('BULK STDERR ' + str(index) + ' x' * 40, file=sys.stderr, flush=True)
    if cfg.get('hang'):
        os.setsid()
        time.sleep(120)
    if not cfg.get('builder_receipt', True):
        pathlib.Path(cfg['receipt']).unlink(missing_ok=True)
    print(cfg.get('build_output', 'ANDROID BOOST BUILD PASS'), flush=True)
    raise SystemExit(cfg.get('build_exit', 0))
if name == 'python.exe':
    if cfg.get('verify_hang'):
        time.sleep(120)
    print(cfg.get('verify_output', 'ANDROID BOOST ARTIFACTS VERIFIED'))
    print(cfg.get('verify_stderr', 'VISIBLE VERIFIER STDERR'), file=sys.stderr)
    raise SystemExit(cfg.get('verify_exit', 0))
assert name == 'adb.exe'
assert args[:4] == ['-H', 'localhost', '-P', '5037'], args
if args[4] == 'connect':
    print(cfg.get('connect_output', 'connected to ' + args[5]))
    raise SystemExit(cfg.get('connect_exit', 0))
assert args[4:6] == ['-s', '127.0.0.1:5555'], args
command = args[6:]
if command == ['get-state']:
    print(cfg.get('state', 'device'))
elif command[:2] == ['shell', 'getprop']:
    defaults = {'sys.boot_completed': '1', 'ro.build.version.sdk': '28', 'ro.product.cpu.abi': 'x86_64'}
    print(cfg.get('properties', {}).get(command[2], defaults[command[2]]))
elif command[:3] == ['shell', 'mkdir', '-p']:
    raise SystemExit(cfg.get('mkdir_exit', 0))
elif command[0] == 'push':
    assert pathlib.Path(command[1]).is_file(), command
    print('fixture pushed ' + pathlib.Path(command[1]).name)
    raise SystemExit(cfg.get('push_exits', {}).get(pathlib.Path(command[1]).name, 0))
elif command[:3] == ['shell', 'chmod', '700']:
    raise SystemExit(cfg.get('chmod_exit', 0))
elif len(command) == 2 and command[0] == 'shell' and command[1].endswith('/wesnoth-boost-check'):
    parts = command[1].split()
    assert len(parts) == 5 and parts[:1] == ['cd'] and parts[2] == '&&', command
    parent = pathlib.PurePosixPath(parts[4]).parent
    assert parts[1] == str(parent) and parts[3] == 'LD_LIBRARY_PATH=' + str(parent), command
    if cfg.get('runtime_hang'):
        time.sleep(120)
    print(cfg['runtime_output'])
    print(cfg.get('runtime_stderr', 'VISIBLE RUNTIME STDERR'), file=sys.stderr)
    raise SystemExit(cfg.get('runtime_exit', 0))
else:
    raise AssertionError(command)
'''


def ps_string(value):
    return "'" + str(value).replace("'", "''") + "'"


class BoostWrapperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="boost wrappers ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "client with spaces"
        (self.repo / "tools").mkdir(parents=True)
        for name in (BUILD, TEST):
            shutil.copy2(ROOT / "tools" / name, self.repo / "tools" / name)
        self.builder = self.repo / "tools/build_android_boost.py"
        self.builder.write_text("synthetic builder input")
        self.build = self.repo / "build/android-boost"
        self.artifact = self.build / "prefix/bin/wesnoth-boost-check"
        self.artifact.parent.mkdir(parents=True)
        self.artifact.write_bytes(b"synthetic probe")
        self.runtime = self.build / "prefix/lib/libc++_shared.so"
        self.runtime.parent.mkdir(parents=True)
        self.runtime.write_bytes(b"synthetic runtime")
        self.receipt = self.build / "receipt.json"
        self.receipt.write_text('{"synthetic_fixture":true}')
        self.sdk = self.base / "Android SDK & literal $"
        self.ndk = self.sdk / "ndk/26.3.11579264"
        self.ndk.mkdir(parents=True)
        self.system = self.base / "Windows system"
        self.taskkill = self.system / "System32/taskkill.exe"
        self.python = self.base / "Native Python/python.exe"
        self.adb = self.base / "SDK platform tools/adb.exe"
        self.cmake = self.base / "CMake with spaces/cmake.exe"
        self.ninja = self.base / "Relocated MSYS/ucrt64/bin/ninja.exe"
        for path in (self.taskkill, self.python, self.adb, self.cmake, self.ninja):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(FAKE_TOOL)
            path.chmod(0o755)
        self.keep = self.repo / "build/android-media/prefix/keep.txt"
        self.keep.parent.mkdir(parents=True)
        self.keep.write_bytes(b"preserve completed stage")
        (self.repo / ".env").write_text("PRIVATE_SENTINEL_DO_NOT_PRINT")
        self.config = {"calls": str(self.base / "calls.jsonl"), "receipt": str(self.receipt), "runtime_output": SUCCESS}
        self.config_path = self.base / "fixture.json"

    def run_wrapper(self, build=False, serial="127.0.0.1:5555", direct=False):
        self.config_path.write_text(json.dumps(self.config))
        Path(self.config["calls"]).unlink(missing_ok=True)
        script = self.repo / "tools" / (BUILD if build else TEST)
        args = ["-PythonPath", str(self.python)]
        args += ["-SdkRoot", str(self.sdk), "-CMakePath", str(self.cmake), "-NinjaPath", str(self.ninja), "-Jobs", "3"] if build else [
            "-AdbPath", str(self.adb), "-Serial", serial]
        before, after = self.base / "before.json", self.base / "after.json"
        capture = "@{ PATH=$env:PATH; MSYSTEM=$env:MSYSTEM; BASH_ENV=$env:BASH_ENV; ENV=$env:ENV } | ConvertTo-Json | Set-Content -LiteralPath "
        invocation = ps_string(script)
        if direct:
            invocation = ps_string(PWSH) + " -NoLogo -NoProfile -File " + invocation
        launcher = self.base / "launcher.ps1"
        launcher.write_text("$ErrorActionPreference='Stop'\n" + capture + ps_string(before)
            + "\n$result=2\ntry { & " + invocation + " "
            + " ".join(args[i] + " " + ps_string(args[i + 1]) for i in range(0, len(args), 2))
            + "\n$result=$LASTEXITCODE } catch { Write-Host $_.Exception.Message }\n"
            + capture + ps_string(after) + "\nexit $result\n")
        env = dict(os.environ, OS="Windows_NT", LOCALAPPDATA=str(self.base), SystemRoot=str(self.system),
            BOOST_FIXTURE=str(self.config_path), MSYSTEM="PARENT_UNCHANGED", BASH_ENV="parent startup hook", ENV="parent other hook")
        result = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-File", str(launcher)],
            capture_output=True, text=True, env=env, cwd=self.base, timeout=25)
        self.output = result.stdout + result.stderr
        calls = Path(self.config["calls"])
        self.calls = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
        self.assertNotIn("PRIVATE_SENTINEL_DO_NOT_PRINT", self.output)
        self.assertEqual(self.keep.read_bytes(), b"preserve completed stage")
        self.assertEqual(json.loads(before.read_text()), json.loads(after.read_text()))
        return result.returncode

    def test_native_build_forwards_pinned_args_isolates_child_and_streams_both_outputs(self):
        self.assertEqual(self.run_wrapper(build=True, direct=True), 0, self.output)
        self.assertEqual(len(self.calls), 1)
        call = self.calls[0]
        self.assertEqual(call["tool"], "python.exe")
        self.assertEqual(call["args"], ["-I", "-B", "-u", str(self.builder), "--ndk", str(self.ndk),
            "--cmake", str(self.cmake), "--ninja", str(self.ninja), "--jobs", "3"])
        self.assertEqual(call["cwd"], str(self.repo))
        self.assertEqual(call["path"], json.loads((self.base / "before.json").read_text())["PATH"])
        self.assertEqual(call["msystem"], "PARENT_UNCHANGED")
        self.assertEqual([call[key] for key in ("bash_env", "env", "pythonhome", "pythonpath")], [None] * 4)
        self.assertEqual([call[key] for key in ("nobytecode", "nouser")], ["1", "1"])
        self.assertIn("VISIBLE BUILD OUTPUT", self.output)
        self.assertIn("VISIBLE BUILD STDERR", self.output)
        self.assertIn("ANDROID BOOST BUILD COMPLETE:", self.output)

    def test_large_install_output_is_drained_without_a_delay_for_each_line(self):
        wrapper = self.repo / "tools" / BUILD
        wrapper.write_text(wrapper.read_text().replace("$timeoutMilliseconds = 7200000", "$timeoutMilliseconds = 5000"))
        self.config["bulk_lines"] = 1000
        self.assertEqual(self.run_wrapper(build=True), 0, self.output[-2000:])
        for channel in ("STDOUT", "STDERR"):
            for index in range(1000):
                self.assertIn("BULK " + channel + " " + str(index) + " x" * 40, self.output)
        self.assertIn("ANDROID BOOST BUILD COMPLETE:", self.output)

    def test_default_ninja_uses_verified_msys_location(self):
        launcher = self.base / "inspect build default.ps1"
        launcher.write_text("$tokens=$null; $errors=$null\n"
            "$ast=[System.Management.Automation.Language.Parser]::ParseFile("
            + ps_string(self.repo / "tools" / BUILD) + ", [ref]$tokens, [ref]$errors)\n"
            "if ($errors.Count) { throw 'Helper failed to parse.' }\n"
            "$binding=[scriptblock]::Create($ast.ParamBlock.Extent.Text + \"`nWrite-Output `$NinjaPath\")\n"
            "& $binding -SdkRoot 'unused sdk' -PythonPath 'unused python'\n")
        result = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-File", str(launcher)],
            capture_output=True, text=True, cwd=self.base, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), r"D:\Test\wesnoth-setup\msys64\ucrt64\bin\ninja.exe")

    def test_build_failure_cannot_pass_from_success_marker(self):
        self.config["build_exit"] = 2
        self.assertEqual(self.run_wrapper(build=True), 2, self.output)
        self.assertNotIn("ANDROID BOOST BUILD COMPLETE:", self.output)

    def test_build_requires_stdout_marker_and_receipt(self):
        self.config.update(build_output="not the marker", build_stderr="ANDROID BOOST BUILD PASS")
        self.assertEqual(self.run_wrapper(build=True), 2, self.output)
        self.assertNotIn("ANDROID BOOST BUILD COMPLETE:", self.output)
        self.config.pop("build_output")
        self.config["builder_receipt"] = False
        self.assertEqual(self.run_wrapper(build=True), 2, self.output)
        self.assertIn("without its Boost artifact receipt", self.output)

    def test_missing_empty_tools_and_ndk_prevent_build_process(self):
        for tool in (self.python, self.cmake, self.ninja, self.taskkill, self.builder):
            for change in ("missing", "empty"):
                with self.subTest(tool=tool.name, change=change):
                    data = tool.read_bytes()
                    tool.unlink()
                    if change == "empty":
                        tool.write_bytes(b"")
                    self.assertEqual(self.run_wrapper(build=True), 2, self.output)
                    self.assertEqual(self.calls, [])
                    tool.write_bytes(data)
                    tool.chmod(0o755)
        self.ndk.rmdir()
        self.assertEqual(self.run_wrapper(build=True), 2, self.output)
        self.assertEqual(self.calls, [])

    def test_build_timeout_stops_owned_pid_and_preserves_outputs(self):
        wrapper = self.repo / "tools" / BUILD
        wrapper.write_text(wrapper.read_text().replace("$timeoutMilliseconds = 7200000", "$timeoutMilliseconds = 400"))
        self.config["hang"] = True
        self.assertEqual(self.run_wrapper(build=True), 2, self.output)
        self.assertEqual([c["tool"] for c in self.calls], ["python.exe", "taskkill.exe"])
        self.assertEqual(self.calls[1]["args"], ["/PID", str(self.calls[0]["pid"]), "/T", "/F"])
        self.assertTrue(self.artifact.is_file())
        self.assertNotIn("ANDROID BOOST BUILD COMPLETE:", self.output)

    def test_device_verifies_first_and_pushes_exactly_probe_and_runtime_without_args(self):
        self.cmake.unlink()
        self.ninja.unlink()
        self.ndk.rmdir()
        self.assertEqual(self.run_wrapper(direct=True), 0, self.output)
        self.assertEqual(self.calls[0]["tool"], "python.exe")
        self.assertEqual(self.calls[0]["args"], ["-I", "-B", "-u", str(self.builder), "--verify"])
        self.assertEqual(self.calls[1]["args"], ["-H", "localhost", "-P", "5037", "connect", "127.0.0.1:5555"])
        commands = [call["args"][6:] for call in self.calls[2:]]
        for call in self.calls[2:]:
            self.assertEqual(call["args"][:6], ["-H", "localhost", "-P", "5037", "-s", "127.0.0.1:5555"])
        self.assertEqual(commands[:4], [["get-state"], ["shell", "getprop", "sys.boot_completed"],
            ["shell", "getprop", "ro.build.version.sdk"], ["shell", "getprop", "ro.product.cpu.abi"]])
        remote = commands[4][-1]
        self.assertRegex(remote, r"^/data/local/tmp/wesnoth-boost-check/[0-9a-f]{32}$")
        self.assertEqual(commands[4:], [["shell", "mkdir", "-p", remote],
            ["push", str(self.artifact), remote + "/wesnoth-boost-check"],
            ["push", str(self.runtime), remote + "/libc++_shared.so"],
            ["shell", "chmod", "700", remote + "/wesnoth-boost-check"],
            ["shell", "cd " + remote + " && LD_LIBRARY_PATH=" + remote + " " + remote + "/wesnoth-boost-check"]])
        self.assertEqual(len(self.calls), 11)
        self.assertTrue(all(call["cwd"] == str(self.repo) for call in self.calls))
        self.assertIn("ANDROID BOOST TEST PASSED", self.output)
        self.assertIn("VISIBLE VERIFIER STDERR", self.output)
        self.assertIn("VISIBLE RUNTIME STDERR", self.output)

    def test_missing_empty_and_redirected_artifacts_stop_before_adb(self):
        for path in (self.artifact, self.runtime):
            for change in ("missing", "empty", "symlink"):
                with self.subTest(file=path.name, change=change):
                    original = path.read_bytes()
                    path.unlink()
                    if change == "empty":
                        path.write_bytes(b"")
                    elif change == "symlink":
                        target = self.base / "redirected runtime"
                        target.write_bytes(original)
                        path.symlink_to(target)
                    self.assertEqual(self.run_wrapper(), 2, self.output)
                    self.assertEqual([call["tool"] for call in self.calls], ["python.exe"])
                    path.unlink(missing_ok=True)
                    path.write_bytes(original)

    def test_redirected_prefix_stops_before_adb(self):
        prefix = self.build / "prefix"
        outside = self.base / "redirected prefix"
        prefix.rename(outside)
        prefix.symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual([call["tool"] for call in self.calls], ["python.exe"])
        self.assertIn("reparse points", self.output)

    def test_verifier_failure_missing_marker_or_stderr_only_marker_blocks_adb(self):
        for config in ({"verify_exit": 2}, {"verify_output": "almost ANDROID BOOST ARTIFACTS VERIFIED"},
            {"verify_output": "", "verify_stderr": "ANDROID BOOST ARTIFACTS VERIFIED"}):
            with self.subTest(config=config):
                self.config.update(config)
                self.assertEqual(self.run_wrapper(), 2, self.output)
                self.assertEqual([c["tool"] for c in self.calls], ["python.exe"])
                self.assertNotIn("ANDROID BOOST TEST PASSED", self.output)
                for key in config:
                    self.config.pop(key)

    def test_verifier_timeout_blocks_adb(self):
        wrapper = self.repo / "tools" / TEST
        wrapper.write_text(wrapper.read_text().replace("--verify') 600000", "--verify') 400"))
        self.config["verify_hang"] = True
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual([call["tool"] for call in self.calls], ["python.exe"])
        self.assertIn("timed out", self.output)

    def test_missing_native_verification_tools_stop_before_adb(self):
        for tool in (self.python, self.adb, self.builder):
            with self.subTest(tool=tool.name):
                data = tool.read_bytes()
                tool.unlink()
                self.assertEqual(self.run_wrapper(), 2, self.output)
                self.assertEqual(self.calls, [])
                tool.write_bytes(data)
                tool.chmod(0o755)

    def test_connection_failure_or_offline_state_prevents_device_mutation(self):
        self.config["connect_exit"] = 1
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(len(self.calls), 2)
        self.config.update(connect_exit=0, connect_output="failed to connect", state="offline")
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(len(self.calls), 3)

    def test_incompatible_or_unbooted_devices_are_never_modified(self):
        for props in ({"sys.boot_completed": "0"}, {"ro.build.version.sdk": "22"},
            {"ro.build.version.sdk": "not a number"}, {"ro.product.cpu.abi": "arm64-v8a"}):
            with self.subTest(props=props):
                self.config["properties"] = props
                self.assertEqual(self.run_wrapper(), 2, self.output)
                self.assertEqual(len(self.calls), 6)
                self.assertNotIn("Boost test directory:", self.output)

    def test_each_push_failure_prevents_execution(self):
        for name in (self.artifact.name, self.runtime.name):
            with self.subTest(name=name):
                self.config["push_exits"] = {name: 1}
                self.assertEqual(self.run_wrapper(), 2, self.output)
                self.assertEqual(self.calls[-1]["args"][6], "push")
                self.assertEqual(Path(self.calls[-1]["args"][7]).name, name)
                self.assertNotIn("ANDROID BOOST TEST PASSED", self.output)

    def test_mkdir_or_chmod_failure_prevents_execution(self):
        for key in ("mkdir_exit", "chmod_exit"):
            with self.subTest(key=key):
                self.config[key] = 1
                self.assertEqual(self.run_wrapper(), 2, self.output)
                self.assertNotIn("ANDROID BOOST TEST PASSED", self.output)
                self.assertEqual(self.calls[-1]["args"][7], key.split("_")[0])
                self.config.pop(key)

    def test_adb_timeout_remains_separate_from_long_receipt_verification(self):
        wrapper = self.repo / "tools" / TEST
        wrapper.write_text(wrapper.read_text().replace("[int]$TimeoutMilliseconds = 60000", "[int]$TimeoutMilliseconds = 400"))
        self.config["runtime_hang"] = True
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(len(self.calls), 11)
        self.assertIn("timed out after 0.4 seconds", self.output)
        self.assertNotIn("ANDROID BOOST TEST PASSED", self.output)

    def test_runtime_failure_exit_cannot_pass_from_success_text(self):
        self.config["runtime_exit"] = 1
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertNotIn("ANDROID BOOST TEST PASSED", self.output)

    def test_every_runtime_marker_is_required_on_stdout(self):
        for marker in SUCCESS.splitlines():
            with self.subTest(marker=marker):
                self.config.update(runtime_output=SUCCESS.replace(marker, ""), runtime_stderr=marker)
                self.assertEqual(self.run_wrapper(), 2, self.output)
                self.assertIn("did not confirm: " + marker, self.output)
                self.assertNotIn("ANDROID BOOST TEST PASSED", self.output)

    def test_runtime_stderr_failure_cannot_pass_with_zero_exit(self):
        self.config["runtime_stderr"] = "FAIL wesnoth-android-boost: injected failure"
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertIn("reported a failure despite its exit code", self.output)

    def test_serial_shell_metacharacters_are_rejected_before_any_tool(self):
        self.assertNotEqual(self.run_wrapper(serial="127.0.0.1:5555;echo unsafe"), 0, self.output)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pwsh", required=True)
    args, rest = parser.parse_known_args()
    PWSH = args.pwsh
    unittest.main(argv=[__file__] + rest)
