"""Host semantic tests of the real standalone Client/loopback fixture.

Run inside the existing compiler container with ALLIANCE_ANDROID_CHECK_PATH,
ALLIANCE_ANDROID_CHECK_LIBRARY (liballiance_transport.a), and
ALLIANCE_ANDROID_CHECK_BOOST (the existing include directory). These execute
host code only; they do not establish Android runtime acceptance.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools/native-alliance/android_check.cpp"
CASES = (
    "before-connect-guards", "wrong-ip", "wrong-pin", "hostname-refused",
    "broadcast-refused", "expired", "TLS1.2", "TLS1.3", "server-push",
    "untrusted", "downgrade", "plaintext", "preamble-eof", "preamble-reset",
    "preamble-stall", "tls-stall", "oversized", "empty-frame",
    "partial-header", "partial-body", "receive-stall", "cancel",
)
FINAL = "ALLIANCE ANDROID TRANSPORT CHECK PASSED"
ENVIRONMENT = (
    "ALLIANCE_ANDROID_CHECK_PATH", "ALLIANCE_ANDROID_CHECK_LIBRARY",
    "ALLIANCE_ANDROID_CHECK_BOOST",
)


@unittest.skipUnless(all(os.environ.get(key) for key in ENVIRONMENT),
                     "Set the explicit existing host fixture/build paths.")
class AndroidProbeHostSemantics(unittest.TestCase):
    def execute(self, binary: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(binary), *args], text=True, capture_output=True,
                              timeout=20, check=False)

    def test_real_component_all_cases_and_repeat(self) -> None:
        binary = Path(os.environ[ENVIRONMENT[0]])
        for _ in range(3):
            result = self.execute(binary)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual([line for line in result.stdout.splitlines()
                              if line.startswith("PASS ")],
                             ["PASS " + name for name in CASES])
            self.assertEqual(result.stdout.splitlines().count(FINAL), 1)
            self.assertIn("WESNOTH_ANDROID_ALLIANCE abi=host min_api=0 ", result.stdout)
            self.assertEqual(result.stderr, "")

    def test_arguments_rejected_before_any_case(self) -> None:
        result = self.execute(Path(os.environ[ENVIRONMENT[0]]), "--anything")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("no command-line arguments are accepted", result.stderr)

    def mutation(self, old: str, new: str, expected_message: str) -> None:
        text = SOURCE.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1)
        with tempfile.TemporaryDirectory(prefix="alliance-probe-mutation-") as folder:
            source = Path(folder) / "variant.cpp"
            binary = Path(folder) / "variant"
            source.write_text(text.replace(old, new), encoding="utf-8")
            command = [
                os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra",
                "-Werror", "-DBOOST_ALL_NO_LIB", "-DBOOST_ERROR_CODE_HEADER_ONLY",
                "-I" + str(ROOT / "alliance_native"),
                "-isystem", os.environ[ENVIRONMENT[2]], str(source),
                os.environ[ENVIRONMENT[1]], "-lssl", "-lcrypto", "-pthread",
                "-o", str(binary),
            ]
            compile_result = subprocess.run(command, text=True, capture_output=True,
                                            timeout=90, check=False)
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            result = self.execute(binary)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertNotIn(FINAL, result.stdout)
            self.assertIn(expected_message, result.stderr)

    def test_changed_echo_payload_cannot_pass(self) -> None:
        old = "reply = header; reply.insert(reply.end(), body.begin(), body.end());"
        self.mutation(old, old + " reply.back() ^= 0xff;",
                      "client exact frame response")

    def test_wrong_negative_expectation_cannot_pass(self) -> None:
        self.mutation(
            'guard_case("wrong-pin", wrong, alliance::Error::pin_mismatch);',
            'guard_case("wrong-pin", wrong, alliance::Error::invalid_profile);',
            "unexpected client result",
        )

    def test_early_failure_joins_peer_waiting_for_connection(self) -> None:
        old = "Peer peer(identity, mode, version);\n    alliance::Client client;"
        self.mutation(old, "Peer peer(identity, mode, version);\n"
                      '    throw std::runtime_error("injected early failure");\n'
                      "    alliance::Client client;", "injected early failure")

    def test_broken_client_timer_cannot_pass_via_peer_emergency_deadline(self) -> None:
        transport = (ROOT / "alliance_native/transport.cpp").read_text(encoding="utf-8")
        old = ("forced_error = cancellation.requested() ? Error::cancelled : Error::timeout;\n"
               "                    close();")
        self.assertEqual(transport.count(old), 1)
        # Deliberately leave I/O open at the deadline. The peer eventually
        # closes, and the production result mapping still reports timeout.
        # The fixture must fail its independent elapsed-time assertion.
        new = ("forced_error = cancellation.requested() ? Error::cancelled : Error::timeout;\n"
               "                    arm();")
        with tempfile.TemporaryDirectory(prefix="alliance-probe-timer-mutation-") as folder:
            source = Path(folder) / "transport.cpp"
            binary = Path(folder) / "variant"
            source.write_text(transport.replace(old, new), encoding="utf-8")
            command = [
                os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra",
                "-Werror", "-DBOOST_ALL_NO_LIB", "-DBOOST_ERROR_CODE_HEADER_ONLY",
                "-I" + str(ROOT / "alliance_native"),
                "-isystem", os.environ[ENVIRONMENT[2]], str(SOURCE), str(source),
                "-lssl", "-lcrypto", "-pthread", "-o", str(binary),
            ]
            compiled = subprocess.run(command, text=True, capture_output=True,
                                      timeout=120, check=False)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = self.execute(binary)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertNotIn(FINAL, result.stdout)
            self.assertIn("timeout exceeded client-operation bound", result.stderr)


if __name__ == "__main__":
    unittest.main()
