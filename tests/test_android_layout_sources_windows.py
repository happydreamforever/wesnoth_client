"""PowerShell source-cache fixtures; no network, SDK or existing builds needed.

Download tests replace only the HTTP response factory, by its parsed AST extent.
All stream copying, hashes, exclusive creation, finalization and cleanup run the
real helper. Separate root integration checks the actual public release asset.
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
HELPER = "Prepare-WindowsAndroidLayoutSources.ps1"
ARCHIVE = "fribidi-1.0.17.tar.xz"
DATA = b"Fixture source archive, not a real release.\n" * 2500

MOCK_RESPONSE = r'''function New-LayoutSourceResponse([string]$Uri) {
    $cfg = Get-Content -LiteralPath $env:WESNOTH_LAYOUT_FIXTURE -Raw | ConvertFrom-Json
    Add-Content -LiteralPath $cfg.calls -Value ($Uri + ' TLS=' + [Net.ServicePointManager]::SecurityProtocol)
    if ($cfg.failure) { throw 'Fixture network interruption.' }
    if ($cfg.collision) { [IO.File]::WriteAllText($cfg.archive, 'Concurrent file must survive.') }
    $stream = New-Object IO.MemoryStream -ArgumentList (,[IO.File]::ReadAllBytes($cfg.payload))
    $response = [pscustomobject]@{
        ResponseUri = [Uri]$cfg.redirect
        StatusCode = $cfg.status
        ContentLength = [long]$cfg.content_length
        FixtureStream = $stream
        BreakStream = $cfg.break_stream
    }
    $response | Add-Member -MemberType ScriptMethod -Name GetResponseStream -Value {
        if ($this.BreakStream) { throw 'Fixture stream interrupted.' }
        return $this.FixtureStream
    }
    $response | Add-Member -MemberType ScriptMethod -Name Close -Value { $this.FixtureStream.Dispose() }
    return $response
}'''


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


class LayoutSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wesnoth layout sources ")
        self.base = Path(self.temp.name)
        self.repo = self.base / "client with spaces"
        self.tools = self.repo / "tools"
        (self.tools / "native-text-layout").mkdir(parents=True)
        shutil.copy2(ROOT / "tools" / HELPER, self.tools / HELPER)
        self.manifest = json.loads((ROOT / "tools/native-text-layout/sources.json").read_text())
        self.pin = self.manifest["archives"][0]
        self.pin.update(bytes=len(DATA), sha256=hashlib.sha256(DATA).hexdigest())
        self.manifest_path = self.tools / "native-text-layout/sources.json"
        self.save_manifest()
        self.cache = self.repo / "build/android-layout-sources"
        self.archive = self.cache / ARCHIVE
        self.payload = self.base / "response.bin"
        self.payload.write_bytes(DATA)
        self.calls = self.base / "requests.txt"
        self.config = {
            "payload": str(self.payload), "calls": str(self.calls), "archive": str(self.archive),
            "redirect": "https://release-assets.githubusercontent.com/github-production-release-asset/fixture?token=fixture",
            "status": 200, "content_length": len(DATA), "failure": False, "collision": False,
            "break_stream": False,
        }
        self.sentinels = {}
        for relative in (".env", "tools/android-native-archives.json", "build/android-fonts/prefix/lib/libfreetype.a",
                         "build/android-fonts/build-receipt.json", "build/android-text-render/prefix/lib/libcairo.a",
                         "build/android-text-render/build-receipt.json"):
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            self.sentinels[path] = b"PRESERVE_PRIVATE_OR_PRIOR_INPUT_" + relative.encode()
            path.write_bytes(self.sentinels[path])

    def tearDown(self):
        for path, expected in self.sentinels.items():
            self.assertEqual(path.read_bytes(), expected)
        self.temp.cleanup()

    def save_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest))

    def put_archive(self, data=DATA):
        self.cache.mkdir(parents=True, exist_ok=True)
        self.archive.write_bytes(data)

    def run_helper(self, download=False, mock=True):
        helper = self.tools / HELPER
        if mock:
            config_path = self.base / "response.json"
            config_path.write_text(json.dumps(self.config))
            replacement = self.base / "response-factory.ps1"
            replacement.write_text(MOCK_RESPONSE)
            instrumented = self.tools / "fixture-prepare-layout.ps1"
            wrapper = self.base / "run-fixture.ps1"
            wrapper.write_text(r'''
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(HELPER, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Helper parse failed.' }
$matches = @($ast.FindAll({ param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'New-LayoutSourceResponse'
}, $false))
if ($matches.Count -ne 1) { throw 'Expected exactly one HTTP response factory.' }
$extent = $matches[0].Extent
$source = Get-Content -LiteralPath HELPER -Raw
$source = $source.Remove($extent.StartOffset, $extent.EndOffset - $extent.StartOffset).Insert(
    $extent.StartOffset, (Get-Content -LiteralPath REPLACEMENT -Raw))
[IO.File]::WriteAllText(INSTRUMENTED, $source)
$before = [Net.ServicePointManager]::SecurityProtocol
& INSTRUMENTED DOWNLOAD
$code = $LASTEXITCODE
if ([Net.ServicePointManager]::SecurityProtocol -ne $before) { throw 'TLS state was not restored.' }
Write-Host 'FIXTURE TLS RESTORED'
exit $code
'''.replace("HELPER", quote(helper)).replace("REPLACEMENT", quote(replacement))
                .replace("INSTRUMENTED", quote(instrumented)).replace("DOWNLOAD", "-Download" if download else ""))
            helper = wrapper
        env = dict(os.environ, WESNOTH_LAYOUT_FIXTURE=str(self.base / "response.json"))
        result = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-File", str(helper)] +
                                (["-Download"] if download and not mock else []),
                                cwd=self.base, env=env, capture_output=True, text=True, timeout=30)
        self.output = result.stdout + result.stderr
        self.request_lines = self.calls.read_text().splitlines() if self.calls.exists() else []
        self.assertNotIn("PRESERVE_PRIVATE_OR_PRIOR_INPUT", self.output)
        if mock:
            self.assertIn("FIXTURE TLS RESTORED", self.output)
        return result.returncode

    def assert_failed(self, download=False, requests=0):
        self.assertEqual(self.run_helper(download=download), 2, self.output)
        self.assertEqual(len(self.request_lines), requests, self.output)
        self.assertNotIn("ANDROID LAYOUT SOURCES VERIFIED", self.output)
        if self.cache.is_dir() and not self.cache.is_symlink():
            self.assertEqual(list(self.cache.glob(".fribidi-download-*.tmp")), [])

    def test_direct_file_offline_success_from_unrelated_directory(self):
        self.put_archive()
        self.assertEqual(self.run_helper(mock=False), 0, self.output)
        self.assertIn("ANDROID LAYOUT SOURCES VERIFIED", self.output)
        self.assertEqual(self.archive.read_bytes(), DATA)
        self.assertEqual(self.request_lines, [])

    def test_direct_file_missing_offline_does_not_create_cache(self):
        self.assertEqual(self.run_helper(mock=False), 2, self.output)
        self.assertFalse(self.cache.exists())
        self.assertIn("with -Download", self.output)

    def test_offline_valid_archive_never_requests_network(self):
        self.put_archive()
        self.assertEqual(self.run_helper(), 0, self.output)
        self.assertEqual(self.request_lines, [])

    def test_download_reuses_valid_archive_without_network(self):
        self.put_archive()
        self.assertEqual(self.run_helper(download=True), 0, self.output)
        self.assertEqual(self.request_lines, [])

    def test_offline_missing_never_requests_or_creates_cache(self):
        self.assert_failed()
        self.assertFalse(self.cache.exists())

    def test_existing_same_size_tamper_preserved_even_with_download(self):
        changed = b"X" + DATA[1:]
        self.put_archive(changed)
        self.assert_failed(download=True)
        self.assertEqual(self.archive.read_bytes(), changed)

    def test_existing_empty_file_is_preserved(self):
        self.put_archive(b"")
        self.assert_failed(download=True)
        self.assertEqual(self.archive.read_bytes(), b"")

    def test_download_finalizes_verified_bytes_and_offline_reuses(self):
        self.assertEqual(self.run_helper(download=True), 0, self.output)
        self.assertEqual(len(self.request_lines), 1)
        self.assertIn("Tls12", self.request_lines[0])
        self.assertEqual(self.archive.read_bytes(), DATA)
        self.assertEqual(list(self.cache.iterdir()), [self.archive])
        self.calls.unlink()
        self.assertEqual(self.run_helper(mock=False), 0, self.output)

    def test_concurrent_destination_survives_failed_finalization(self):
        self.config["collision"] = True
        self.assert_failed(download=True, requests=1)
        self.assertEqual(self.archive.read_text(), "Concurrent file must survive.")

    def test_network_interruption_removes_only_owned_temporary_file(self):
        self.cache.mkdir(parents=True)
        old_temporary = self.cache / ".fribidi-download-user-kept.tmp"
        old_temporary.write_bytes(b"Keep prior temporary data.")
        self.config["failure"] = True
        self.assertEqual(self.run_helper(download=True), 2, self.output)
        self.assertEqual(old_temporary.read_bytes(), b"Keep prior temporary data.")
        self.assertEqual(list(self.cache.iterdir()), [old_temporary])
        self.assertEqual(len(self.request_lines), 1)

    def test_stream_interruption_is_not_finalized(self):
        self.config["break_stream"] = True
        self.assert_failed(download=True, requests=1)
        self.assertFalse(self.archive.exists())

    def test_wrong_download_hash_is_not_finalized(self):
        self.payload.write_bytes(b"X" + DATA[1:])
        self.assert_failed(download=True, requests=1)
        self.assertFalse(self.archive.exists())

    def test_truncated_download_without_length_is_not_finalized(self):
        self.config["content_length"] = -1
        self.payload.write_bytes(DATA[:-1])
        self.assert_failed(download=True, requests=1)
        self.assertFalse(self.archive.exists())

    def test_oversized_download_without_length_is_bounded(self):
        self.config["content_length"] = -1
        self.payload.write_bytes(DATA + b"extra bytes")
        self.assert_failed(download=True, requests=1)
        self.assertIn("exceeded its pinned size", self.output)
        self.assertFalse(self.archive.exists())

    def test_wrong_content_length_stops_before_stream(self):
        self.config["content_length"] = len(DATA) + 1
        self.assert_failed(download=True, requests=1)
        self.assertIn("Content-Length", self.output)

    def test_non_success_http_is_not_finalized(self):
        self.config["status"] = 206
        self.assert_failed(download=True, requests=1)

    def test_non_https_or_unexpected_redirect_is_refused(self):
        for url in ("http://github.com/fixture", "https://unrelated.example/fixture",
                    "https://name:password@github.com/fixture", "https://github.com/fixture#fragment"):
            with self.subTest(url=url):
                self.config["redirect"] = url
                self.assert_failed(download=True, requests=1)
                self.calls.unlink()

    def test_archive_symlink_is_preserved_and_refused(self):
        self.cache.mkdir(parents=True)
        self.archive.symlink_to(self.payload)
        self.assert_failed(download=True)
        self.assertTrue(self.archive.is_symlink())
        self.assertEqual(self.payload.read_bytes(), DATA)

    def test_missing_target_symlink_is_preserved_and_refused(self):
        self.cache.mkdir(parents=True)
        self.archive.symlink_to(self.base / "absent source")
        self.assert_failed(download=True)
        self.assertTrue(self.archive.is_symlink())

    def test_redirected_cache_is_refused(self):
        external = self.base / "external cache"
        external.mkdir()
        self.cache.symlink_to(external, target_is_directory=True)
        self.assert_failed(download=True)
        self.assertEqual(list(external.iterdir()), [])

    def test_archive_directory_collision_is_preserved(self):
        self.archive.mkdir(parents=True)
        self.assert_failed(download=True)
        self.assertTrue(self.archive.is_dir())

    def test_changed_manifest_path_url_hash_or_size_is_refused(self):
        original = dict(self.pin)
        for key, value in (("name", "../escape.tar.xz"), ("source_dir", "../escape"),
                           ("url", "http://github.com/unsafe"), ("version", "master"),
                           ("sha256", "not-a-sha256"), ("bytes", -1), ("bytes", 1.5),
                           ("bytes", "1203284"), ("bytes", 20000001)):
            with self.subTest(key=key, value=value):
                self.pin.clear()
                self.pin.update(original)
                self.pin[key] = value
                self.save_manifest()
                self.assert_failed(download=True)
                self.assertFalse(self.cache.exists())

    def test_multiple_manifest_archives_are_refused(self):
        self.manifest["archives"].append(dict(self.pin))
        self.save_manifest()
        self.assert_failed(download=True)

    def test_manifest_symlink_is_refused(self):
        moved = self.base / "redirected manifest.json"
        self.manifest_path.rename(moved)
        self.manifest_path.symlink_to(moved)
        self.assert_failed(download=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pwsh", required=True)
    args, rest = parser.parse_known_args()
    PWSH = args.pwsh
    unittest.main(argv=[__file__] + rest)
