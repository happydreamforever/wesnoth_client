"""Linux PowerShell fixtures for glib wrappers; Windows tools/ADB are mocked.

The builder's independent tests cover its read-only receipt and ELF validator.
These fixtures verify that the device wrapper requires that validator to pass
before any ADB action, and that native failures cannot produce wrapper success.
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
BUILD = "Build-WindowsAndroidGlib.ps1"
TEST = "Test-AndroidGlib.ps1"
LAUNCH = 'builder=$(/usr/bin/cygpath -u -- "$1") || exit; shift; exec /usr/bin/python3 -I -B -u "$builder" "$@"'
SUCCESS = "\n".join(("WESNOTH_ANDROID_GLIB version=1.19.26 abi=x86_64 min_api=23",
                     "PASS GLib Unicode and regex", "PASS GLib variants",
                     "PASS GObject signals and lifetime", "PASS GIO streams and compression",
                     "PASS GLib threads and main context", "PASS GModule process symbols",
                     "PASS wesnoth-android-glib"))
FAKE_TOOL = r'''#!/usr/bin/python3
import json, os, pathlib, signal, subprocess, sys, time
cfg = json.loads(pathlib.Path(os.environ['GLIB_FIXTURE']).read_text())
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
if name == 'bash.exe':
    print('VISIBLE BUILD OUTPUT', flush=True)
    print('VISIBLE BUILD STDERR', file=sys.stderr, flush=True)
    if cfg.get('hang'):
        os.setsid()
        time.sleep(120)
    if not cfg.get('builder_receipt', True):
        pathlib.Path(cfg['receipt']).unlink(missing_ok=True)
    print(cfg.get('build_output', 'ANDROID GLIB BUILD PASS'), flush=True)
    raise SystemExit(cfg.get('build_exit', 0))
if name == 'python.exe':
    if cfg.get('real_verifier'):
        os.execv(sys.executable, [sys.executable] + args)
    print(cfg.get('verify_output', 'ANDROID GLIB ARTIFACTS VERIFIED'))
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
    pass
elif command[0] == 'push':
    assert pathlib.Path(command[1]).is_file(), command
    print('fixture pushed ' + pathlib.Path(command[1]).name)
    raise SystemExit(cfg.get('push_exit', 0))
elif command[:3] == ['shell', 'chmod', '700']:
    pass
elif len(command) == 2 and command[0] == 'shell' and command[1].endswith('/wesnoth-glib-check'):
    print(cfg['runtime_output'])
    print(cfg.get('runtime_stderr', 'VISIBLE RUNTIME STDERR'), file=sys.stderr)
    raise SystemExit(cfg.get('runtime_exit', 0))
else:
    raise AssertionError(command)
'''


def ps_string(value):
    return "'" + str(value).replace("'", "''") + "'"


class GlibWrapperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="glib ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "client with spaces"
        (self.repo / "tools").mkdir(parents=True)
        for name in (BUILD, TEST):
            shutil.copy2(ROOT / "tools" / name, self.repo / "tools" / name)
        self.builder = self.repo / "tools/build_android_glib.py"
        self.builder.write_text("synthetic builder input")
        self.build = self.repo / "build/android-glib"
        self.artifact = self.build / "prefix/bin/wesnoth-glib-check"
        self.artifact.parent.mkdir(parents=True)
        self.artifact.write_bytes(b"synthetic probe")
        self.receipt = self.build / "receipt.json"
        self.receipt.write_text('{"synthetic_fixture":true}')
        self.msys = self.base / "MSYS with spaces"
        self.sdk = self.base / "Android SDK"
        self.ndk = self.sdk / "ndk/26.3.11579264"
        self.ndk.mkdir(parents=True)
        self.system = self.base / "Windows system"
        self.bash = self.msys / "usr/bin/bash.exe"
        self.msys_python = self.msys / "usr/bin/python3.exe"
        self.cygpath = self.msys / "usr/bin/cygpath.exe"
        self.taskkill = self.system / "System32/taskkill.exe"
        self.python = self.base / "Native Python/python.exe"
        self.adb = self.base / "SDK platform tools/adb.exe"
        for path in (self.bash, self.msys_python, self.cygpath, self.taskkill, self.python, self.adb):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(FAKE_TOOL)
            path.chmod(0o755)
        self.keep = self.repo / "build/android-fonts/prefix/keep.txt"
        self.keep.parent.mkdir(parents=True)
        self.keep.write_bytes(b"preserve completed stage")
        (self.repo / ".env").write_text("PRIVATE_SENTINEL_DO_NOT_PRINT")
        self.config = {"calls": str(self.base / "calls.jsonl"), "receipt": str(self.receipt), "runtime_output": SUCCESS}
        self.config_path = self.base / "fixture.json"

    def run_wrapper(self, build=False, archive=None, serial="127.0.0.1:5555"):
        self.config_path.write_text(json.dumps(self.config))
        Path(self.config["calls"]).unlink(missing_ok=True)
        script = self.repo / "tools" / (BUILD if build else TEST)
        args = ["-MsysRoot", str(self.msys), "-SdkRoot", str(self.sdk), "-Jobs", "3"] if build else [
            "-PythonPath", str(self.python), "-AdbPath", str(self.adb), "-Serial", serial]
        if archive is not None:
            args += ["-ArchiveDirectory", str(archive)]
        before = self.base / "before.json"
        after = self.base / "after.json"
        capture = "@{ PATH=$env:PATH; MSYSTEM=$env:MSYSTEM; BASH_ENV=$env:BASH_ENV; ENV=$env:ENV } | ConvertTo-Json | Set-Content -LiteralPath "
        launcher = self.base / "launcher.ps1"
        launcher.write_text("$ErrorActionPreference='Stop'\n" + capture + ps_string(before)
                            + "\n$result=2\ntry { & " + ps_string(script) + " "
                            + " ".join(args[i] + " " + ps_string(args[i + 1]) for i in range(0, len(args), 2))
                            + "\n$result=$LASTEXITCODE } catch { Write-Host $_.Exception.Message }\n"
                            + capture + ps_string(after) + "\nexit $result\n")
        env = dict(os.environ, OS="Windows_NT", LOCALAPPDATA=str(self.base), SystemRoot=str(self.system),
                   GLIB_FIXTURE=str(self.config_path), MSYSTEM="PARENT_UNCHANGED",
                   BASH_ENV="parent startup hook", ENV="parent other hook")
        result = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-File", str(launcher)],
                                capture_output=True, text=True, env=env, timeout=25)
        self.output = result.stdout + result.stderr
        calls = Path(self.config["calls"])
        self.calls = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
        self.assertNotIn("PRIVATE_SENTINEL_DO_NOT_PRINT", self.output)
        self.assertEqual(self.keep.read_bytes(), b"preserve completed stage")
        self.assertEqual(json.loads(before.read_text()), json.loads(after.read_text()))
        return result.returncode

    def use_real_verifier(self):
        # Exercise the actual verifier on deterministic ELF/archive bytes. No
        # source downloads, compiler, MSYS installation or real device is used.
        from test_android_glib import create_fixture
        create_fixture(self.repo)
        self.upstream_builds = [self.repo / "build" / name for name in ("android-text-base", "android-glib-deps")]
        self.config["real_verifier"] = True

    def test_build_forwards_fixed_launcher_pinned_args_and_isolated_child_environment(self):
        archive = self.base / "native archive cache & literal $"
        archive.mkdir()
        self.assertEqual(self.run_wrapper(build=True, archive=archive), 0, self.output)
        self.assertEqual(len(self.calls), 1)
        call = self.calls[0]
        self.assertEqual(call["tool"], "bash.exe")
        self.assertEqual(call["args"], ["--noprofile", "--norc", "-c", LAUNCH, "wesnoth-glib-build",
            str(self.builder), "--ndk", str(self.ndk), "--jobs", "3", "--archive-directory", str(archive)])
        self.assertEqual(call["cwd"], str(self.repo))
        self.assertEqual(call["path"], "/usr/bin:/bin")
        self.assertEqual(call["msystem"], "MSYS")
        self.assertEqual([call[key] for key in ("bash_env", "env", "pythonhome", "pythonpath")], [None] * 4)
        self.assertEqual([call[key] for key in ("nobytecode", "nouser")], ["1", "1"])
        self.assertIn("VISIBLE BUILD OUTPUT", self.output)
        self.assertIn("VISIBLE BUILD STDERR", self.output)
        self.assertIn("ANDROID GLIB BUILD COMPLETE:", self.output)

    def test_build_nonzero_exit_cannot_pass_with_success_marker(self):
        self.config["build_exit"] = 2
        self.assertEqual(self.run_wrapper(build=True), 2, self.output)
        self.assertNotIn("ANDROID GLIB BUILD COMPLETE:", self.output)

    def test_build_zero_without_marker_or_receipt_cannot_pass(self):
        self.config["build_output"] = "no final marker"
        self.assertEqual(self.run_wrapper(build=True), 2, self.output)
        self.assertNotIn("ANDROID GLIB BUILD COMPLETE:", self.output)
        self.config.pop("build_output")
        self.config["builder_receipt"] = False
        self.assertEqual(self.run_wrapper(build=True), 2, self.output)
        self.assertIn("without its glib artifact receipt", self.output)

    def test_missing_build_tools_and_ndk_stop_before_bash(self):
        for tool in (self.bash, self.msys_python, self.cygpath, self.taskkill, self.builder):
            with self.subTest(tool=tool.name):
                data = tool.read_bytes()
                tool.unlink()
                self.assertEqual(self.run_wrapper(build=True), 2, self.output)
                self.assertEqual(self.calls, [])
                tool.write_bytes(data)
                tool.chmod(0o755)
        self.ndk.rmdir()
        self.assertEqual(self.run_wrapper(build=True), 2, self.output)
        self.assertEqual(self.calls, [])

    def test_missing_selected_archive_folder_stops_before_bash(self):
        self.assertEqual(self.run_wrapper(build=True, archive=self.base / "absent cache"), 2, self.output)
        self.assertEqual(self.calls, [])

    def test_build_timeout_stops_owned_pid_and_preserves_outputs(self):
        wrapper = self.repo / "tools" / BUILD
        wrapper.write_text(wrapper.read_text().replace("$timeoutMilliseconds = 7200000", "$timeoutMilliseconds = 400"))
        self.config["hang"] = True
        self.assertEqual(self.run_wrapper(build=True), 2, self.output)
        self.assertEqual([c["tool"] for c in self.calls], ["bash.exe", "taskkill.exe"])
        self.assertEqual(self.calls[1]["args"], ["/PID", str(self.calls[0]["pid"]), "/T", "/F"])
        self.assertTrue(self.artifact.is_file())
        self.assertNotIn("ANDROID GLIB BUILD COMPLETE:", self.output)

    def test_runtime_verifies_before_adb_and_pushes_only_probe(self):
        self.bash.unlink()
        self.ndk.rmdir()  # Device verification must not depend on the build tools.
        self.assertEqual(self.run_wrapper(), 0, self.output)
        self.assertEqual(self.calls[0]["tool"], "python.exe")
        self.assertEqual(self.calls[0]["args"], ["-I", "-B", "-u", str(self.builder), "--verify-only"])
        self.assertEqual(self.calls[1]["args"], ["-H", "localhost", "-P", "5037", "connect", "127.0.0.1:5555"])
        commands = [call["args"][6:] for call in self.calls[2:]]
        for call in self.calls[2:]:
            self.assertEqual(call["args"][:6], ["-H", "localhost", "-P", "5037", "-s", "127.0.0.1:5555"])
        self.assertEqual(commands[:4], [["get-state"], ["shell", "getprop", "sys.boot_completed"],
            ["shell", "getprop", "ro.build.version.sdk"], ["shell", "getprop", "ro.product.cpu.abi"]])
        remote = commands[4][-1]
        self.assertRegex(remote, r"^/data/local/tmp/wesnoth-glib-check/[0-9a-f]{32}$")
        self.assertEqual(commands[4:], [["shell", "mkdir", "-p", remote],
            ["push", str(self.artifact), remote + "/wesnoth-glib-check"],
            ["shell", "chmod", "700", remote + "/wesnoth-glib-check"],
            ["shell", remote + "/wesnoth-glib-check"]])
        self.assertEqual(len(self.calls), 10)
        self.assertIn("ANDROID GLIB TEST PASSED", self.output)
        self.assertIn("VISIBLE VERIFIER STDERR", self.output)
        self.assertIn("VISIBLE RUNTIME STDERR", self.output)

    def test_verifier_failure_blocks_all_adb(self):
        self.config["verify_exit"] = 2
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual([c["tool"] for c in self.calls], ["python.exe"])
        self.assertNotIn("ANDROID GLIB TEST PASSED", self.output)

    def test_verifier_zero_without_exact_marker_blocks_all_adb(self):
        self.config["verify_output"] = "almost ANDROID GLIB ARTIFACTS VERIFIED"
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual([c["tool"] for c in self.calls], ["python.exe"])

    def test_real_receipt_verifier_passes_without_ndk_or_msys(self):
        self.use_real_verifier()
        shutil.rmtree(self.msys)
        shutil.rmtree(self.ndk)
        stages = [self.build] + self.upstream_builds
        before = {p.relative_to(self.repo).as_posix(): p.read_bytes()
                  for root in stages for p in root.rglob("*") if p.is_file()}
        self.assertEqual(self.run_wrapper(), 0, self.output)
        self.assertEqual(len(self.calls), 10)
        self.assertIn("ANDROID GLIB ARTIFACTS VERIFIED", self.output)
        self.assertEqual(before, {p.relative_to(self.repo).as_posix(): p.read_bytes()
                                  for root in stages for p in root.rglob("*") if p.is_file()})

    def test_real_verifier_changed_prerequisites_or_missing_receipts_prevent_adb(self):
        self.use_real_verifier()
        for root, name in zip(self.upstream_builds, ("iconv.h", "libintl.h")):
            with self.subTest(stage=root.name):
                header = root / "prefix/include" / name
                original = header.read_bytes()
                header.write_bytes(b"changed prerequisite header")
                self.assertEqual(self.run_wrapper(), 2, self.output)
                self.assertEqual([c["tool"] for c in self.calls], ["python.exe"])
                self.assertIn("hashes differ", self.output)
                header.write_bytes(original)
                receipt = root / "receipt.json"
                original = receipt.read_bytes()
                receipt.unlink()
                self.assertEqual(self.run_wrapper(), 2, self.output)
                self.assertEqual([c["tool"] for c in self.calls], ["python.exe"])
                receipt.write_bytes(original)

    def test_real_verifier_changed_prefix_and_missing_receipt_prevent_adb(self):
        self.use_real_verifier()
        header = self.build / "prefix/include/glib-2.0/glib.h"
        original = header.read_bytes()
        header.write_bytes(b"changed header")
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual([c["tool"] for c in self.calls], ["python.exe"])
        self.assertIn("hashes differ", self.output)
        header.write_bytes(original)
        self.receipt.unlink()
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual([c["tool"] for c in self.calls], ["python.exe"])

    def test_real_verifier_changed_source_and_wrong_android_elf_prevent_adb(self):
        from test_android_bootstrap import elf_program
        self.use_real_verifier()
        source = self.repo / "tools/native-glib/probe.c"
        original = source.read_bytes()
        source.write_bytes(original + b"\n/* local edit */\n")
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual([c["tool"] for c in self.calls], ["python.exe"])
        self.assertIn("hashes differ", self.output)
        source.write_bytes(original)
        self.artifact.write_bytes(elf_program(needed=("libc.so",), api=28))
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual([c["tool"] for c in self.calls], ["python.exe"])
        self.assertIn("Android x86_64/API23", self.output)

    def test_missing_native_verification_tools_stop_before_adb(self):
        for tool in (self.python, self.adb, self.builder):
            with self.subTest(tool=tool.name):
                data = tool.read_bytes()
                tool.unlink()
                self.assertEqual(self.run_wrapper(), 2, self.output)
                self.assertEqual(self.calls, [])
                tool.write_bytes(data)
                tool.chmod(0o755)

    def test_offline_device_cannot_pass_from_connect_exit_zero(self):
        self.config.update(connect_output="failed to connect", state="offline")
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(len(self.calls), 3)

    def test_incompatible_devices_are_never_modified(self):
        for props in ({"sys.boot_completed": "0"}, {"ro.build.version.sdk": "22"}, {"ro.product.cpu.abi": "arm64-v8a"}):
            with self.subTest(props=props):
                self.config["properties"] = props
                self.assertEqual(self.run_wrapper(), 2, self.output)
                self.assertEqual(len(self.calls), 6)
                self.assertNotIn("GLib test directory:", self.output)

    def test_push_failure_stops_before_execution(self):
        self.config["push_exit"] = 1
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls[-1]["args"][6], "push")
        self.assertNotIn("ANDROID GLIB TEST PASSED", self.output)

    def test_runtime_failure_exit_cannot_pass_from_success_text(self):
        self.config["runtime_exit"] = 1
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertNotIn("ANDROID GLIB TEST PASSED", self.output)

    def test_incomplete_runtime_markers_cannot_pass(self):
        self.config["runtime_output"] = SUCCESS.replace("PASS GObject signals and lifetime", "")
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertIn("did not confirm: PASS GObject signals and lifetime", self.output)

    def test_runtime_stderr_failure_cannot_pass_with_zero_exit(self):
        self.config["runtime_stderr"] = "FAIL wesnoth-android-glib: injected failure"
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
