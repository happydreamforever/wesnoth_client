"""Linux PowerShell control-flow fixtures; no Windows/MSYS tools are executed."""
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
MARKER = "ANDROID TEXT TOOLCHAIN CHECK PASSED"
FAKE_TOOL = r'''#!/usr/bin/python3
import json, os, pathlib, signal, subprocess, sys, time
cfg = json.loads(pathlib.Path(os.environ['TEXT_FIXTURE']).read_text())
name = pathlib.Path(sys.argv[0]).name
with open(cfg['calls'], 'a') as stream:
    stream.write(json.dumps({'tool': name, 'args': sys.argv[1:], 'cwd': os.getcwd(),
        'pid': os.getpid(), 'path': os.environ.get('PATH'), 'msystem': os.environ.get('MSYSTEM'),
        'bash_env': os.environ.get('BASH_ENV'), 'env': os.environ.get('ENV')}) + '\n')
if name == 'taskkill.exe':
    assert sys.argv[1::2] == ['/PID', '/T'], sys.argv
    assert sys.argv[-1] == '/F'
    pid = int(sys.argv[2])
    assert pid != os.getpid() and pid != os.getppid()
    os.killpg(pid, signal.SIGKILL)
    print('FIXTURE stopped owned tree')
    raise SystemExit(0)
assert name == 'bash.exe'
assert len(sys.argv) == 6 and sys.argv[1:3] == ['--noprofile', '--norc'], sys.argv
scratch = pathlib.Path(sys.argv[5])
assert scratch.is_dir() and not list(scratch.iterdir())
assert ' ' in str(scratch)
owned = scratch / 'owned probe'
owned.mkdir()
(owned / 'probe.o').write_bytes(b'fixture output')
print('VISIBLE FIRST PROBE', flush=True)
print('VISIBLE STDERR DIAGNOSTIC', file=sys.stderr, flush=True)
if cfg.get('hang'):
    os.setsid()
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
    (owned / 'child.pid').write_text(str(child.pid))
    time.sleep(120)
time.sleep(cfg.get('delay', 0))
if cfg.get('marker_stderr'):
    print(cfg['marker_stderr'], file=sys.stderr, flush=True)
print(cfg.get('output', 'ANDROID TEXT TOOLCHAIN CHECK PASSED'), flush=True)
raise SystemExit(cfg.get('exit', 0))
'''


def ps_string(text):
    return "'" + str(text).replace("'", "''") + "'"


class TextToolchainWrapperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="text toolchain ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "client with spaces & 'literal' $"
        self.tools = self.repo / "tools"
        self.tools.mkdir(parents=True)
        self.wrapper = self.tools / "Test-WindowsAndroidTextToolchain.ps1"
        shutil.copy2(ROOT / "tools" / self.wrapper.name, self.wrapper)
        self.helper = self.tools / "check_android_text_toolchain.sh"
        self.helper.write_text("synthetic shell helper\n")
        self.msys = self.base / "MSYS root with spaces"
        self.sdk = self.base / "Android SDK with spaces"
        self.ndk = self.sdk / "ndk/26.3.11579264"
        self.ndk.mkdir(parents=True)
        self.system = self.base / "Windows system root"
        self.bash = self.msys / "usr/bin/bash.exe"
        self.taskkill = self.system / "System32/taskkill.exe"
        for path in (self.bash, self.taskkill):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(FAKE_TOOL)
            path.chmod(0o755)
        self.temp_root = self.base / "temporary root with spaces"
        self.temp_root.mkdir()
        self.other = self.temp_root / "unrelated user's folder"
        self.other.mkdir()
        (self.other / "keep.txt").write_bytes(b"preserve unrelated")
        self.stage = self.repo / "build/android-fonts/prefix/keep.txt"
        self.stage.parent.mkdir(parents=True)
        self.stage.write_bytes(b"preserve completed fonts")
        (self.repo / ".env").write_text("PRIVATE_SENTINEL_DO_NOT_PRINT")
        self.config = {"calls": str(self.base / "calls.jsonl")}
        self.config_path = self.base / "fixture.json"
        self.parent_env = self.base / "parent-after.json"
        self.parent_before = self.base / "parent-before.json"
        self.launcher = self.base / "fixture-launcher.ps1"
        self.launcher.write_text(
            "@{ PATH=$env:PATH; MSYSTEM=$env:MSYSTEM; BASH_ENV=$env:BASH_ENV; ENV=$env:ENV } | "
            "ConvertTo-Json | Set-Content -LiteralPath " + ps_string(self.parent_before) + "\n"
            "& " + ps_string(self.wrapper) + " -MsysRoot " + ps_string(self.msys)
            + " -SdkRoot " + ps_string(self.sdk) + "\n"
            "$result = $LASTEXITCODE\n"
            "@{ PATH=$env:PATH; MSYSTEM=$env:MSYSTEM; BASH_ENV=$env:BASH_ENV; ENV=$env:ENV } | "
            "ConvertTo-Json | Set-Content -LiteralPath " + ps_string(self.parent_env) + "\nexit $result\n")

    def command(self, windows=True):
        self.config_path.write_text(json.dumps(self.config))
        env = dict(os.environ, OS="Windows_NT" if windows else "fixture-linux", SystemRoot=str(self.system),
                   LOCALAPPDATA=str(self.base), TMPDIR=str(self.temp_root), TEXT_FIXTURE=str(self.config_path),
                   MSYSTEM="PARENT_UNCHANGED", BASH_ENV="parent startup hook", ENV="parent other hook")
        return [PWSH, "-NoLogo", "-NoProfile", "-File", str(self.launcher)], env

    def inspect(self, output):
        self.output = output
        calls = Path(self.config["calls"])
        self.calls = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
        self.assertEqual(self.stage.read_bytes(), b"preserve completed fonts")
        self.assertEqual((self.other / "keep.txt").read_bytes(), b"preserve unrelated")
        self.assertNotIn("PRIVATE_SENTINEL_DO_NOT_PRINT", output)
        parent = json.loads(self.parent_env.read_text())
        self.assertEqual(parent, json.loads(self.parent_before.read_text()))
        self.assertEqual(parent["MSYSTEM"], "PARENT_UNCHANGED")
        self.assertEqual(parent["BASH_ENV"], "parent startup hook")
        self.assertEqual(parent["ENV"], "parent other hook")

    def run_wrapper(self, windows=True):
        args, env = self.command(windows)
        result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=15)
        self.inspect(result.stdout + result.stderr)
        return result.returncode

    def scratch(self):
        return Path(self.calls[0]["args"][-1])

    def test_success_forwards_exact_safe_arguments_and_child_environment(self):
        self.assertEqual(self.run_wrapper(), 0, self.output)
        self.assertEqual(len(self.calls), 1)
        call = self.calls[0]
        self.assertEqual(call["args"], ["--noprofile", "--norc", str(self.helper), str(self.ndk), str(self.scratch())])
        self.assertEqual(call["cwd"], str(self.scratch()))
        self.assertEqual(call["path"], "/usr/bin:/bin")
        self.assertEqual(call["msystem"], "MSYS")
        self.assertIsNone(call["bash_env"])
        self.assertIsNone(call["env"])
        self.assertEqual(self.scratch().parent, self.temp_root)
        self.assertRegex(self.scratch().name, r"^wesnoth text toolchain [0-9a-f]{32}$")
        self.assertFalse(self.scratch().exists())
        self.assertIn("VISIBLE FIRST PROBE", self.output)
        self.assertIn("VISIBLE STDERR DIAGNOSTIC", self.output)
        self.assertIn("Diagnostic scratch removed.", self.output)

    def test_nonzero_child_preserves_scratch_even_if_it_prints_success(self):
        self.config["exit"] = 2
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue((self.scratch() / "owned probe/probe.o").is_file())
        self.assertIn("failed with exit 2", self.output)
        self.assertIn("Diagnostic scratch preserved:", self.output)
        self.assertNotIn("Diagnostic scratch removed.", self.output)

    def test_zero_exit_without_exact_stdout_marker_preserves_scratch(self):
        self.config.update(output="almost " + MARKER, marker_stderr=MARKER)
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertTrue(self.scratch().is_dir())
        self.assertIn("without its required completion marker", self.output)

    def test_missing_bash_stops_before_scratch_or_process(self):
        self.bash.unlink()
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.temp_root.iterdir()), [self.other])

    def test_missing_helper_stops_before_scratch_or_process(self):
        self.helper.unlink()
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.temp_root.iterdir()), [self.other])

    def test_missing_ndk_stops_before_scratch_or_process(self):
        self.ndk.rmdir()
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.temp_root.iterdir()), [self.other])

    def test_missing_process_tree_stop_tool_prevents_start(self):
        self.taskkill.unlink()
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual(self.calls, [])

    def test_non_windows_refuses_without_running_tools(self):
        self.assertEqual(self.run_wrapper(windows=False), 2, self.output)
        self.assertEqual(self.calls, [])

    def test_output_is_visible_while_diagnostic_is_still_running(self):
        self.config["delay"] = 1.5
        args, env = self.command()
        process = subprocess.Popen(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        lines = []
        try:
            while True:
                line = process.stdout.readline()
                self.assertTrue(line, "diagnostic exited without streaming its probe line")
                lines.append(line)
                if "VISIBLE FIRST PROBE" in line:
                    self.assertIsNone(process.poll(), "probe line was buffered until process exit")
                    break
            remainder, _ = process.communicate(timeout=10)
            self.inspect("".join(lines) + remainder)
            self.assertEqual(process.returncode, 0, self.output)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

    def test_timeout_stops_only_owned_process_tree_and_preserves_scratch(self):
        # Accelerate only the copied fixture's deadline; taskkill is a Linux mock.
        self.wrapper.write_text(self.wrapper.read_text().replace("$timeoutMilliseconds = 600000", "$timeoutMilliseconds = 400"))
        self.config["hang"] = True
        self.assertEqual(self.run_wrapper(), 2, self.output)
        self.assertEqual([call["tool"] for call in self.calls], ["bash.exe", "taskkill.exe"])
        self.assertEqual(self.calls[1]["args"], ["/PID", str(self.calls[0]["pid"]), "/T", "/F"])
        self.assertTrue((self.scratch() / "owned probe/probe.o").exists())
        self.assertIn("exceeded its 600-second limit", self.output)
        self.assertIn("Diagnostic scratch preserved:", self.output)
        self.assertNotIn("Diagnostic scratch removed.", self.output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pwsh", required=True)
    args, rest = parser.parse_known_args()
    PWSH = args.pwsh
    unittest.main(argv=[__file__] + rest)
