"""Real native public-profile reader and certificate verification diagnostics.

Builds the unchanged production reader, codec and transport with pinned Boost
headers and host OpenSSL in a temporary directory. All identities/files are
disposable. The sole injected failure is private crypto-context allocation.
No socket, production profile, credentials, Android runtime or Windows service
is used; these results do not identify the user's pre-menu failure cause.
"""
from pathlib import Path
import hashlib
import json
import os
import shutil
import ssl
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MESSAGES = {
    "file": "Alliance could not access its saved connection profile. [profile-file]",
    "read": "Alliance could not read its saved connection profile. [profile-read]",
    "format": "Alliance could not validate its saved connection profile format. [profile-format]",
    "identity": "Alliance could not validate its saved server identity. [profile-identity]",
    "pin": "Alliance could not match its saved server certificate fingerprint. [profile-pin]",
    "certificate": "Alliance could not verify its saved server certificate. [profile-certificate]",
    "crypto": "Alliance could not complete its local certificate checks. [profile-crypto]",
    "internal": "Alliance could not check its saved connection profile. [profile-internal]",
}


class ProfileReadDiagnostics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which(os.environ.get("CXX", "c++"))
        cls.openssl = shutil.which("openssl")
        if not compiler or not cls.openssl:
            raise unittest.SkipTest("C++17 compiler and OpenSSL are required")
        cls.temporary = tempfile.TemporaryDirectory(prefix="alliance-profile-reader-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.audit = Path(os.environ["ALLIANCE_PROFILE_READ_AUDIT"]) if os.environ.get("ALLIANCE_PROFILE_READ_AUDIT") else None
        output = cls.audit or cls.root
        output.mkdir(parents=True, exist_ok=True)
        cls.binary = output / "account_profile_read_probe"
        paths = ["tests/account_profile_read_probe.cpp", "alliance_native/account_client.cpp",
                 "alliance_native/account_protocol.cpp", "alliance_native/transport.cpp"]
        command = [compiler, "-std=c++17", "-O0", "-g", "-Wall", "-Wextra", "-Werror",
                   "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                   "-DBOOST_ALL_NO_LIB", "-DBOOST_ERROR_CODE_HEADER_ONLY",
                   "-I" + str(ROOT / "build/android-boost/prefix/include"),
                   "-I" + str(ROOT / "alliance_native"), *[str(ROOT / name) for name in paths],
                   "-Wl,--wrap=OSSL_LIB_CTX_new", "-lssl", "-lcrypto", "-lz", "-pthread",
                   "-o", str(cls.binary)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=240)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        cls.command = command
        cls.paths = paths + ["alliance_native/account_client.hpp", "tests/test_account_profile_read.py"]
        cls.observations = []
        cls.key = cls.root / "disposable-key.pem"
        cls.certificate = cls.root / "disposable-cert.pem"
        cls.invoke_openssl("req", "-new", "-x509", "-newkey", "ec",
            "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-days", "2",
            "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
            "-addext", "basicConstraints=critical,CA:FALSE",
            "-addext", "keyUsage=critical,digitalSignature",
            "-addext", "extendedKeyUsage=serverAuth", "-keyout", cls.key, "-out", cls.certificate)
        cls.pem = cls.certificate.read_text()
        cls.original_identity = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in (cls.key, cls.certificate)}

    @classmethod
    def invoke_openssl(cls, *arguments):
        environment = os.environ.copy()
        environment["OPENSSL_CONF"] = os.devnull
        result = subprocess.run([cls.openssl, *map(str, arguments)], env=environment,
                                capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise RuntimeError("Disposable certificate fixture creation failed: " + result.stderr)
        return result.stdout

    @classmethod
    def tearDownClass(cls):
        current = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in (cls.key, cls.certificate)}
        if current != cls.original_identity:
            raise AssertionError("Original disposable identity changed during read checks")
        if cls.audit:
            (cls.audit / "result.json").write_text(json.dumps({
                "command": cls.command, "observations": cls.observations,
                "identity_preserved": True,
                "sources": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                            for path in cls.paths}, "limitations": __doc__,
            }, indent=2) + "\n")

    def setUp(self):
        self.path = self.root / (self.id().split(".")[-1] + "-PRIVATE_PATH_SENTINEL.json")

    def profile(self, pem=None):
        pem = pem or self.pem
        return {"format": 1, "protocol": "wesnoth-alliance-tls", "server_address": "127.0.0.1",
                "port": 15001, "minimum_tls": "1.2", "certificate_pem": pem,
                "certificate_sha256": hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()}

    def write(self, value):
        self.path.write_text(json.dumps(value))
        return self.path

    def run_probe(self, path=None, *, crypto_failure=False, expected=None, as_unprivileged=False):
        environment = os.environ.copy()
        environment.pop("ALLIANCE_PROFILE_TEST_CRYPTO_FAILURE", None)
        if crypto_failure:
            environment["ALLIANCE_PROFILE_TEST_CRYPTO_FAILURE"] = "1"
        options = {}
        if as_unprivileged and os.geteuid() == 0:
            # chmod alone cannot deny root a read. Only the disposable child is
            # demoted, with fixture ancestor execute access, for this one case.
            self.root.chmod(0o755)
            options.update(user=65534, group=65534, extra_groups=[])
        before = self.path.read_bytes() if self.path.is_file() and os.access(self.path, os.R_OK) else None
        result = subprocess.run([str(self.binary), str(path if path is not None else self.path)],
                                env=environment, capture_output=True, text=True, timeout=10, **options)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertNotIn("PRIVATE_PATH_SENTINEL", result.stdout)
        self.assertNotIn("PRIVATE_BODY_SENTINEL", result.stdout)
        self.assertNotIn("PRIVATE_KEY_SENTINEL", result.stdout)
        self.assertNotIn("BEGIN CERTIFICATE", result.stdout)
        self.assertNotIn("BEGIN PRIVATE KEY", result.stdout)
        value = json.loads(result.stdout)
        if expected:
            self.assertEqual(value, {"ok": False, "message": MESSAGES[expected]})
        if before is not None:
            self.assertEqual(self.path.read_bytes(), before)
        self.observations.append({"test": self.id().split(".")[-1], "result": value})
        return value

    def test_valid_profile_keeps_native_verification_and_values(self):
        self.write(self.profile())
        self.assertEqual(self.run_probe(), {"ok": True, "address_matches": True,
                         "port_matches": True, "pin_length": 64, "has_certificate": True})

    def test_missing_directory_symlink_and_size_limits(self):
        self.run_probe(expected="file")
        self.path.mkdir()
        self.run_probe(expected="file")
        self.path.rmdir()
        self.path.symlink_to(self.certificate)
        self.run_probe(expected="file")
        self.path.unlink()
        for contents in (b"", b"x" * 32769):
            self.path.write_bytes(contents)
            self.run_probe(expected="file")

    def test_unreadable_regular_file_has_separate_read_code(self):
        self.write(self.profile())
        self.path.chmod(0)
        try:
            self.run_probe(expected="read", as_unprivileged=True)
        finally:
            self.path.chmod(0o600)

    def test_malformed_json_and_duplicate_keys_remain_rejected(self):
        for contents in ('PRIVATE_BODY_SENTINEL{', '{"format":1,"format":1}', '[]',
                         json.dumps(self.profile()) + "PRIVATE_BODY_SENTINEL"):
            self.path.write_text(contents)
            self.run_probe(expected="format")

    def test_private_material_never_appears_in_diagnostic(self):
        self.path.write_text("-----BEGIN PRIVATE KEY-----\nPRIVATE_KEY_SENTINEL\n-----END PRIVATE KEY-----\n")
        self.run_probe(expected="format")
        private = self.profile()
        private["private_key"] = "PRIVATE_KEY_SENTINEL"
        self.write(private)
        self.run_probe(expected="format")

    def test_schema_and_types_remain_rejected(self):
        for key, replacement in (("format", 2), ("port", "15001"), ("port", 0),
                                 ("minimum_tls", "1.0"), ("protocol", "PRIVATE_BODY_SENTINEL"),
                                 ("server_address", ""), ("certificate_sha256", "x" * 65)):
            with self.subTest(key=key, replacement=replacement):
                value = self.profile()
                value[key] = replacement
                self.write(value)
                self.run_probe(expected="format")

    def test_invalid_native_identity_has_own_code(self):
        for key, replacement in (("server_address", "127.000.0.1"),
                                 ("certificate_pem", "PRIVATE_BODY_SENTINEL"),
                                 ("certificate_sha256", "g" * 64)):
            value = self.profile()
            value[key] = replacement
            self.write(value)
            self.run_probe(expected="identity")

    def test_wrong_fingerprint_retains_pin_rejection(self):
        value = self.profile()
        value["certificate_sha256"] = "0" * 64
        self.write(value)
        self.run_probe(expected="pin")

    def test_wrong_certificate_ip_retains_certificate_rejection(self):
        value = self.profile()
        value["server_address"] = "127.0.0.2"
        self.write(value)
        self.run_probe(expected="certificate")

    def test_wrong_certificate_purpose_retains_certificate_rejection(self):
        certificate = self.root / "client-purpose.pem"
        self.invoke_openssl("req", "-new", "-x509", "-key", self.key, "-days", "2",
            "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
            "-addext", "basicConstraints=critical,CA:FALSE",
            "-addext", "keyUsage=critical,digitalSignature",
            "-addext", "extendedKeyUsage=clientAuth", "-out", certificate)
        self.write(self.profile(certificate.read_text()))
        self.run_probe(expected="certificate")

    def test_expired_certificate_retains_certificate_rejection(self):
        help_result = subprocess.run([self.openssl, "x509", "-help"], capture_output=True, timeout=10)
        if b"-not_before" not in help_result.stdout + help_result.stderr:
            self.skipTest("Pinned verification OpenSSL 3.5+ explicit date options required")
        request, config = self.root / "expired-request.pem", self.root / "expired.cnf"
        config.write_text("[server]\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n"
                          "extendedKeyUsage=serverAuth\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always\n"
                          "subjectAltName=IP:127.0.0.1\n")
        self.invoke_openssl("req", "-new", "-key", self.key, "-subj", "/CN=127.0.0.1", "-out", request)
        pem = self.invoke_openssl("x509", "-req", "-in", request, "-signkey", self.key,
            "-sha256", "-set_serial", "1", "-extfile", config, "-extensions", "server",
            "-not_before", "20000101000000Z", "-not_after", "20010101000000Z")
        self.write(self.profile(pem))
        self.run_probe(expected="certificate")

    def test_native_crypto_failure_is_fixed_and_separate(self):
        self.write(self.profile())
        self.run_probe(crypto_failure=True, expected="crypto")

    def test_unknown_error_category_uses_fixed_fallback(self):
        self.run_probe("--unknown-category", expected="internal")


if __name__ == "__main__":
    unittest.main()
