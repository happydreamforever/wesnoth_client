"""Offline, real C++ account client tests against disposable loopback TLS peers.

Set ALLIANCE_ACCOUNT_RESPONSE_PROBE to the host response-probe executable.
These verify the production codec/client, not Android UI or Windows execution.
No production runtime, server identity, credentials, or database is used.
"""
from __future__ import annotations

from contextlib import contextmanager
import gzip
import hashlib
import json
import os
from pathlib import Path
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
import time
import unittest


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def exact(stream, count):
    result = bytearray()
    while len(result) < count:
        part = stream.recv(count - len(result))
        if not part:
            raise EOFError
        result.extend(part)
    return bytes(result)


@unittest.skipUnless(os.environ.get("ALLIANCE_ACCOUNT_RESPONSE_PROBE"),
                     "Set the existing host account response-probe path.")
class AccountClientResponseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="alliance-account-responses-")
        cls.root = Path(cls.directory.name)
        cls.binary = Path(os.environ["ALLIANCE_ACCOUNT_RESPONSE_PROBE"])
        cls.key = cls.root / "disposable-key.pem"
        cls.cert = cls.root / "disposable-cert.pem"
        result = subprocess.run([
            "openssl", "req", "-new", "-x509", "-newkey", "ec",
            "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-days", "2",
            "-subj", "/CN=127.0.0.1",
            "-addext", "subjectAltName=IP:127.0.0.1",
            "-addext", "basicConstraints=critical,CA:FALSE",
            "-addext", "keyUsage=critical,digitalSignature",
            "-addext", "extendedKeyUsage=serverAuth",
            "-keyout", str(cls.key), "-out", str(cls.cert),
        ], capture_output=True, timeout=20, check=False)
        if result.returncode != 0:
            cls.directory.cleanup()
            raise RuntimeError("Disposable TLS identity creation failed.")
        cls.pem = cls.cert.read_text(encoding="ascii")
        cls.pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cls.pem)).hexdigest()
        cls.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.context.minimum_version = ssl.TLSVersion.TLSv1_2
        cls.context.load_cert_chain(cls.cert, cls.key)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def profile(self, port):
        return {
            "format": 1, "protocol": "wesnoth-alliance-tls",
            "server_address": "127.0.0.1", "port": port, "minimum_tls": "1.2",
            "certificate_pem": self.pem, "certificate_sha256": self.pin,
        }

    @staticmethod
    def account():
        return {
            "id": "12345678-1234-4321-8765-0123456789ab",
            "username": "ResponseUser",
            "leader": {"starter_id": "spearman", "unit_type": "Spearman",
                       "level": 1, "experience": 0, "total_experience": 0},
        }

    @classmethod
    def success(cls, request):
        return {
            "v": 1, "id": request["id"], "ok": True, "status": 200,
            "data": {"token": "a" * 43, "account": cls.account()},
        }

    @contextmanager
    def peer(self, responder=None, *, stall=False, preamble_delay=0):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(2)
        listener.settimeout(0.05)
        stop = threading.Event()
        state = {"connections": 0, "request": None, "error": None}
        port = listener.getsockname()[1]

        def run():
            try:
                while not stop.is_set():
                    try:
                        raw, _ = listener.accept()
                        break
                    except socket.timeout:
                        continue
                else:
                    return
                state["connections"] += 1
                with raw:
                    raw.settimeout(13)
                    if exact(raw, 4) != struct.pack("!I", 1):
                        raise RuntimeError("preamble")
                    if stop.wait(preamble_delay):
                        return
                    raw.sendall(struct.pack("!I", 0))
                    with self.context.wrap_socket(raw, server_side=True) as secure:
                        size = struct.unpack("!I", exact(secure, 4))[0]
                        if not 1 <= size <= 65536:
                            raise RuntimeError("request-bound")
                        wire = gzip.decompress(exact(secure, size))
                        if len(wire) > 32768:
                            raise RuntimeError("request-expansion")
                        state["request"] = json.loads(wire)
                        if stall:
                            stop.wait(13)
                        else:
                            response = (responder or self.success)(state["request"])
                            payload = response if isinstance(response, bytes) else compact(response)
                            frame = gzip.compress(payload, mtime=0)
                            secure.sendall(struct.pack("!I", len(frame)) + frame)
                # Detect automatic retry: a failed account operation must not
                # silently repeat a potentially completed registration/login.
                while not stop.is_set():
                    try:
                        extra, _ = listener.accept()
                        state["connections"] += 1
                        extra.close()
                    except socket.timeout:
                        continue
            except Exception as error:
                state["error"] = type(error).__name__

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        try:
            yield port, state
        finally:
            stop.set()
            thread.join(3)
            listener.close()
            if thread.is_alive():
                raise AssertionError("Disposable TLS peer exceeded its shutdown bound.")

    def invoke(self, profile, request, *, profile_text=None):
        path = self.root / "public-profile.json"
        path.write_text(profile_text if profile_text is not None
                        else json.dumps(profile, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        # Only public profile path appears in argv; no credential-bearing
        # process command line or assertion output is generated.
        result = subprocess.run([str(self.binary), str(path)], input=compact(request),
                                capture_output=True, timeout=16, check=False)
        self.assertTrue(result.returncode == 0, "Response probe did not finish successfully.")
        self.assertTrue(result.stderr == b"", "Response probe unexpectedly wrote stderr.")
        output = json.loads(result.stdout)
        self.assertEqual(set(output), {"ok", "error_code", "has_token",
                                      "has_profile", "level", "elapsed_ms", "area_count", "team_count",
                                      "has_next", "has_team", "member_count", "team_level",
                                      "applicant_count", "has_team_id", "has_account_id", "status"})
        for secret in (request.get("password"), request.get("token")):
            if secret:
                self.assertTrue(secret.encode("utf-8") not in result.stdout + result.stderr,
                                "Response probe disclosed a credential.")
        return output

    @staticmethod
    def login_request():
        return {"operation": "login", "username": "ResponseUser",
                "password": "fixture-password-only"}

    def exercise(self, responder=None, request=None, **peer_options):
        request = request or self.login_request()
        with self.peer(responder, **peer_options) as (port, state):
            output = self.invoke(self.profile(port), request)
        self.assertEqual(state["connections"], 1, "Client must perform one connection only.")
        self.assertIsNone(state["error"], "Disposable peer failed.")
        self.assertIsNotNone(state["request"], "Peer did not receive a verified account request.")
        self.assertEqual(set(state["request"]), {"v", "id", "op", "body"}
                         | ({"token"} if request["operation"] not in ("register", "login") else set()))
        return output, state["request"]

    def rejects(self, change):
        def responder(request):
            response = self.success(request)
            change(response)
            return response
        output, _ = self.exercise(responder)
        self.assertFalse(output["ok"])
        self.assertEqual(output["error_code"], "protocol")
        self.assertFalse(output["has_token"])
        self.assertFalse(output["has_profile"])

    @staticmethod
    def team_request(operation, **fields):
        return {"operation": operation, "token": "b" * 43, **fields}

    @classmethod
    def team_detail(cls):
        return {"id": "abcdef01-1234-4321-8765-0123456789ab", "name": "Test Team",
                "area_id": "area-1", "area_name": "Area One",
                "captain_id": cls.account()["id"], "level": 1, "experience": 0,
                "rating": 1000, "capacity": 8,
                "members": [{"id": cls.account()["id"], "username": "ResponseUser",
                             "unit_type": "Spearman", "level": 1}]}

    @staticmethod
    def team_reply(data, status=200):
        return lambda request: {"v": 1, "id": request["id"], "ok": True,
                                "status": status, "data": data}

    def team_rejects(self, operation, data, **fields):
        output, _ = self.exercise(self.team_reply(data, 201 if operation == "create_team" else 200),
                                  self.team_request(operation, **fields))
        self.assertFalse(output["ok"])
        self.assertEqual(output["error_code"], "protocol")
        self.assertFalse(output["has_team"])
        self.assertFalse(output["has_token"])

    def test_team_operations_shape_and_exact_request_bodies(self):
        team = self.team_detail()
        summary = {key: value for key, value in team.items()
                   if key not in ("capacity", "members", "area_name")}
        summary["member_count"] = 1
        cases = [
            ("areas", {}, {"after": ""}, {"areas": [{"id": "area-1", "name": "Area One"}], "next": ""}),
            ("teams", {"area_id": "area-1"}, {"area_id": "area-1", "after": ""}, {"teams": [summary], "next": ""}),
            ("my_team", {}, {}, {"team": None}),
            ("my_team", {}, {}, {"team": team}),
            ("create_team", {"name": "Test Team", "area_id": "area-1"},
             {"name": "Test Team", "area_id": "area-1"}, {"team": team}),
            ("team_detail", {"team_id": team["id"]}, {"team_id": team["id"]}, {"team": team}),
        ]
        for operation, fields, body, data in cases:
            with self.subTest(operation=operation, no_team=data.get("team", True) is None):
                output, received = self.exercise(
                    self.team_reply(data, 201 if operation == "create_team" else 200),
                    self.team_request(operation, **fields))
                self.assertTrue(output["ok"])
                self.assertEqual(received["body"], body)
                self.assertTrue(received["token"] == "b" * 43)
                self.assertFalse(output["has_token"])
                self.assertFalse(output["has_profile"])
                self.assertEqual(output["has_team"], data.get("team") is not None)
                self.assertEqual(output["member_count"], 1 if data.get("team") else 0)

    def test_team_pagination_maximum_next_and_empty_final_page(self):
        page = [{"id": "area-" + str(i).zfill(3), "name": "Area " + str(i)} for i in range(20)]
        output, _ = self.exercise(self.team_reply({"areas": page, "next": page[-1]["id"]}),
                                  self.team_request("areas"))
        self.assertTrue(output["ok"])
        self.assertEqual(output["area_count"], 20)
        self.assertTrue(output["has_next"])
        output, received = self.exercise(self.team_reply({"areas": [], "next": ""}),
                                        self.team_request("areas", after=page[-1]["id"]))
        self.assertTrue(output["ok"])
        self.assertEqual(received["body"]["after"], page[-1]["id"])
        self.assertFalse(output["has_next"])

    def test_invalid_pages_rejected_including_duplicated_reordered_and_stale_cursor(self):
        page = [{"id": "area-" + str(i).zfill(3), "name": "Area " + str(i)} for i in range(21)]
        for data, after in [
            ({"areas": page, "next": ""}, ""),
            ({"areas": page[:1] * 2, "next": ""}, ""),
            ({"areas": list(reversed(page[:2])), "next": ""}, ""),
            ({"areas": page[:1], "next": "area-000"}, ""),
            ({"areas": page[:20], "next": "wrong"}, ""),
            ({"areas": page[:20], "next": "x" * 65}, ""),
            ({"areas": [], "next": "bad/"}, ""),
            ({"areas": page[:1], "next": ""}, "area-000"),
            ({"areas": page[:1], "next": "", "extra": 1}, ""),
            ({"areas": [{"id": "area-1", "name": "<b>Area</b>"}], "next": ""}, ""),
            ({"areas": [{"id": "area-1", "name": "Area\nOther"}], "next": ""}, ""),
            ({"areas": [{"id": "area-1", "name": "Area ", "extra": 1}], "next": ""}, ""),
        ]:
            self.team_rejects("areas", data, after=after)

    def test_team_detail_rejects_malformed_rosters_and_unbounded_numbers(self):
        import copy
        changes = [
            lambda team: team.update(capacity=9),
            lambda team: team.update(capacity=True),
            lambda team: team.update(members=[]),
            lambda team: team.update(members=team["members"] * 9),
            lambda team: team.update(members=team["members"] * 2),
            lambda team: team.update(captain_id="abcdef01-1234-4321-8765-0123456789ab"),
            lambda team: team.update(level=0),
            lambda team: team.update(experience=-1),
            lambda team: team.update(experience=2147483648),
            lambda team: team.update(rating=-2147483649),
            lambda team: team.update(id="invalid"),
            lambda team: team.update(area_id="area/1"),
            lambda team: team.update(area_name="Area <1>"),
            lambda team: team.update(name="Test Team\nFake"),
            lambda team: team.update(private_requests=["secret"]),
            lambda team: team["members"][0].update(username="<b>Bad</b>"),
            lambda team: team["members"][0].update(unit_type="Spearman\nOther"),
            lambda team: team["members"][0].update(level=4),
            lambda team: team["members"][0].update(id="bad"),
            lambda team: team["members"][0].update(password_hash="hidden"),
        ]
        for change in changes:
            team = copy.deepcopy(self.team_detail())
            change(team)
            self.team_rejects("my_team", {"team": team})
        self.team_rejects("team_detail", {"team": None}, team_id=self.team_detail()["id"])
        self.team_rejects("team_detail", {"team": self.team_detail()},
                          team_id="eeeeeeee-1234-4321-8765-0123456789ab")
        self.team_rejects("create_team", {"team": self.team_detail()},
                          name="Wrong Team", area_id="area-1")

    def test_team_list_page_shape_area_scope_and_bounds(self):
        import copy
        team = self.team_detail()
        base = {key: value for key, value in team.items()
                if key not in ("capacity", "members", "area_name")}
        base["member_count"] = 1
        page = []
        for i in range(20):
            entry = copy.deepcopy(base)
            entry["id"] = f"{i:08x}-1234-4321-8765-0123456789ab"
            page.append(entry)
        output, _ = self.exercise(self.team_reply({"teams": page, "next": page[-1]["id"]}),
                                  self.team_request("teams", area_id="area-1"))
        self.assertTrue(output["ok"])
        self.assertEqual(output["team_count"], 20)
        self.assertTrue(output["has_next"])
        changes = [lambda item: item.update(area_id="other-area"),
                   lambda item: item.update(member_count=0),
                   lambda item: item.update(member_count=9),
                   lambda item: item.update(member_count=True),
                   lambda item: item.update(level=-1),
                   lambda item: item.update(rating=2147483648),
                   lambda item: item.update(captain_id="bad"),
                   lambda item: item.update(name="Bad <markup>"),
                   lambda item: item.update(members=[])]
        for change in changes:
            entry = copy.deepcopy(base)
            change(entry)
            self.team_rejects("teams", {"teams": [entry], "next": ""}, area_id="area-1")
        for entries in (page + [base], [base, base], list(reversed(page))):
            self.team_rejects("teams", {"teams": entries, "next": ""}, area_id="area-1")
        self.team_rejects("teams", {"teams": [base], "next": ""},
                          area_id="area-1", after=base["id"])

    def test_full_roster_and_numeric_boundaries_accepted(self):
        team = self.team_detail()
        team.update(level=2147483647, experience=2147483647, rating=-2147483648)
        for i in range(7):
            team["members"].append({"id": f"{i:08x}-1234-4321-8765-0123456789ab",
                                    "username": "Other" + str(i), "unit_type": "Spearman", "level": 3})
        output, _ = self.exercise(self.team_reply({"team": team}), self.team_request("my_team"))
        self.assertTrue(output["ok"])
        self.assertEqual(output["member_count"], 8)
        self.assertEqual(output["team_level"], 2147483647)

    def test_team_error_does_not_mean_expired_login_except_401(self):
        for status, code, expected in [(403, "forbidden", "forbidden"),
                                       (403, "suspended", "forbidden"),
                                       (401, "forbidden", "invalid_session"),
                                       (409, "already_member", "already_member"),
                                       (409, "conflict", "conflict"),
                                       (404, "not_found", "not_found"),
                                       (400, "busy", "busy"),
                                       (503, "unrecognized", "team_failed"),
                                       (503, "invalid_session", "team_failed")]:
            def responder(request):
                return {"v": 1, "id": request["id"], "ok": False, "status": status,
                        "data": {"code": code, "message": "server-secret-must-not-be-reflected"}}
            output, _ = self.exercise(responder, self.team_request("my_team"))
            self.assertFalse(output["ok"])
            self.assertEqual(output["error_code"], expected)

    def test_invalid_team_requests_rejected_before_connect(self):
        requests = [
            self.team_request("areas", after="bad/"),
            self.team_request("areas", after="a" * 65),
            self.team_request("areas", name="Unused"),
            self.team_request("areas", username="Unused"),
            self.team_request("my_team", team_id=self.team_detail()["id"]),
            self.team_request("my_team", after="area-1"),
            self.team_request("teams"),
            self.team_request("teams", area_id="../area"),
            self.team_request("team_detail", team_id="not-a-uuid"),
            self.team_request("create_team", area_id="area-1", name="x"),
            self.team_request("create_team", area_id="area-1", name="A" * 41),
            self.team_request("create_team", area_id="area-1", name=" Bad"),
            self.team_request("create_team", area_id="area-1", name="Bad "),
            self.team_request("create_team", area_id="area-1", name="<b>Bad</b>"),
            {**self.team_request("my_team"), "token": "bad"},
            {**self.login_request(), "name": "Unused"},
        ]
        for request in requests:
            with self.peer() as (port, state):
                output = self.invoke(self.profile(port), request)
            self.assertFalse(output["ok"])
            self.assertEqual(output["error_code"], "invalid_input")
            self.assertEqual(state["connections"], 0)
            self.assertIsNone(state["error"])

    def test_team_creation_cancel_and_malformed_ack_never_retry(self):
        request = self.team_request("create_team", area_id="area-1", name="Test Team")
        request["cancel_after_ms"] = 700
        output, _ = self.exercise(request=request, stall=True)
        self.assertFalse(output["ok"])
        self.assertEqual(output["error_code"], "cancelled")
        self.assertLess(output["elapsed_ms"], 3000)
        self.team_rejects("create_team", {"team": {}}, area_id="area-1", name="Test Team")

    @classmethod
    def team_summary(cls):
        value = {key: data for key, data in cls.team_detail().items()
                 if key not in ("capacity", "members", "area_name")}
        value["member_count"] = 1
        return value

    def test_membership_operations_exact_shapes_and_acknowledgements(self):
        team_id, account_id = self.team_detail()["id"], self.account()["id"]
        for operation, status in [("apply_team", "applied"), ("withdraw_application", "withdrawn"),
                                  ("approve_application", "approved"), ("reject_application", "rejected")]:
            fields = {"team_id": team_id}
            if operation in ("approve_application", "reject_application"):
                fields["account_id"] = account_id
            output, received = self.exercise(self.team_reply(
                {"team_id": team_id, "account_id": account_id, "status": status}),
                self.team_request(operation, **fields))
            self.assertTrue(output["ok"])
            self.assertEqual(received["body"], fields)
            self.assertEqual(output["status"], status)
            self.assertTrue(output["has_team_id"])
            self.assertTrue(output["has_account_id"])
            self.assertFalse(output["has_token"])
        data = {"teams": [self.team_summary()], "next": ""}
        output, received = self.exercise(self.team_reply(data), self.team_request("my_applications"))
        self.assertTrue(output["ok"])
        self.assertEqual(output["team_count"], 1)
        self.assertEqual(received["body"], {"after": ""})
        data = {"team_id": team_id, "applicants": self.team_detail()["members"], "next": ""}
        output, received = self.exercise(self.team_reply(data), self.team_request("team_applicants", team_id=team_id))
        self.assertTrue(output["ok"])
        self.assertEqual(output["applicant_count"], 1)
        self.assertEqual(received["body"], {"team_id": team_id, "after": ""})

    def test_membership_pages_full_sorted_and_empty_final_page(self):
        team_id = self.team_detail()["id"]
        members = [{"id": f"{i:08x}-1234-4321-8765-0123456789ab", "username": f"User_{i}",
                    "unit_type": "Elvish Fighter", "level": 1} for i in range(20)]
        data = {"team_id": team_id, "applicants": members, "next": members[-1]["id"]}
        output, _ = self.exercise(self.team_reply(data), self.team_request("team_applicants", team_id=team_id))
        self.assertTrue(output["ok"])
        self.assertTrue(output["has_next"])
        self.assertEqual(output["applicant_count"], 20)
        data = {"team_id": team_id, "applicants": [], "next": ""}
        output, received = self.exercise(self.team_reply(data), self.team_request(
            "team_applicants", team_id=team_id, after=members[-1]["id"]))
        self.assertTrue(output["ok"])
        self.assertEqual(received["body"]["after"], members[-1]["id"])
        # Applications may belong to several areas; there is no caller area filter.
        teams = [{**self.team_summary(), "id": entry["id"], "area_id": f"area-{i}"}
                 for i, entry in enumerate(members)]
        output, _ = self.exercise(self.team_reply({"teams": teams, "next": teams[-1]["id"]}),
                                  self.team_request("my_applications"))
        self.assertTrue(output["ok"])
        self.assertTrue(output["has_next"])
        self.assertEqual(output["team_count"], 20)

    def test_membership_ack_rejects_wrong_identity_status_and_extra_fields(self):
        team_id, account_id = self.team_detail()["id"], self.account()["id"]
        other = "eeeeeeee-1234-4321-8765-0123456789ab"
        for operation, status in [("apply_team", "applied"), ("withdraw_application", "withdrawn"),
                                  ("approve_application", "approved"), ("reject_application", "rejected")]:
            fields = {"team_id": team_id}
            if operation in ("approve_application", "reject_application"):
                fields["account_id"] = account_id
            ack = {"team_id": team_id, "account_id": account_id, "status": status}
            invalid = [{**ack, "team_id": other}, {**ack, "account_id": "bad"},
                       {**ack, "status": "joined"}, {**ack, "status": True},
                       {**ack, "private": "hidden"}, {"team_id": team_id, "status": status}]
            if "account_id" in fields:
                invalid.append({**ack, "account_id": other})
            for data in invalid:
                self.team_rejects(operation, data, **fields)
            output, _ = self.exercise(self.team_reply(ack, status=201), self.team_request(operation, **fields))
            self.assertFalse(output["ok"])
            self.assertEqual(output["error_code"], "protocol")

    def test_membership_applicants_reject_unbounded_invalid_and_unsorted_payloads(self):
        import copy
        team_id = self.team_detail()["id"]
        member = self.team_detail()["members"][0]
        base = {"team_id": team_id, "applicants": [member], "next": ""}
        cases = [{**base, "team_id": "eeeeeeee-1234-4321-8765-0123456789ab"},
                 {**base, "applicants": [member] * 21}, {**base, "applicants": [member] * 2},
                 {**base, "next": member["id"]}, {**base, "next": "bad/"},
                 {**base, "private": "hidden"}]
        for key, value in [("id", "bad"), ("username", "<b>Member</b>"),
                           ("unit_type", "Spearman\nOther"), ("level", True), ("level", 4),
                           ("private", "hidden")]:
            entry = copy.deepcopy(member)
            entry[key] = value
            cases.append({**base, "applicants": [entry]})
        for data in cases:
            self.team_rejects("team_applicants", data, team_id=team_id)
        self.team_rejects("team_applicants", base, team_id=team_id, after=member["id"])
        members = [{**member, "id": f"{i:08x}-1234-4321-8765-0123456789ab"} for i in (2, 1)]
        self.team_rejects("team_applicants", {**base, "applicants": members}, team_id=team_id)
        self.team_rejects("my_applications", {"teams": [self.team_summary()] * 2, "next": ""})
        self.team_rejects("my_applications", {"teams": [self.team_summary()] * 21, "next": ""})

    def test_membership_invalid_request_fields_rejected_before_socket(self):
        team_id, account_id = self.team_detail()["id"], self.account()["id"]
        requests = [self.team_request("my_applications", team_id=team_id),
                    self.team_request("my_applications", area_id="area-1"),
                    self.team_request("my_applications", after="a" * 65),
                    self.team_request("team_applicants"),
                    self.team_request("team_applicants", team_id=team_id, account_id=account_id),
                    self.team_request("apply_team", team_id=team_id, account_id=account_id),
                    self.team_request("withdraw_application", team_id=team_id, after=account_id),
                    self.team_request("approve_application", team_id=team_id),
                    self.team_request("reject_application", team_id=team_id, account_id="bad"),
                    self.team_request("my_team", account_id=account_id),
                    {**self.login_request(), "account_id": account_id}]
        for request in requests:
            with self.peer() as (port, state):
                output = self.invoke(self.profile(port), request)
            self.assertFalse(output["ok"])
            self.assertEqual(output["error_code"], "invalid_input")
            self.assertEqual(state["connections"], 0)
            self.assertIsNone(state["error"])

    def test_membership_cancel_has_no_retry_and_permission_denial_preserves_session_meaning(self):
        team_id, account_id = self.team_detail()["id"], self.account()["id"]
        for operation in ("apply_team", "withdraw_application", "approve_application", "reject_application"):
            fields = {"team_id": team_id, "cancel_after_ms": 300}
            if operation in ("approve_application", "reject_application"):
                fields["account_id"] = account_id
            output, _ = self.exercise(request=self.team_request(operation, **fields), stall=True)
            self.assertEqual(output["error_code"], "cancelled")
            self.assertLess(output["elapsed_ms"], 3000)
        for status, code, expected in [(401, "forbidden", "invalid_session"),
                                       (403, "forbidden", "forbidden"),
                                       (409, "capacity", "capacity"),
                                       (409, "not_requested", "not_requested")]:
            def error(received):
                return {"v": 1, "id": received["id"], "ok": False, "status": status,
                        "data": {"code": code, "message": "Private server detail"}}
            output, _ = self.exercise(error, self.team_request("approve_application", team_id=team_id,
                                                               account_id=account_id))
            self.assertEqual(output["error_code"], expected)

    def test_positive_login_and_unicode_password_cross_language_wire(self):
        request = self.login_request()
        request["password"] = 'é🔒中文ab"\\:cd\nEF!'
        output, received = self.exercise(request=request)
        self.assertTrue(output["ok"])
        self.assertTrue(output["has_token"])
        self.assertTrue(output["has_profile"])
        self.assertEqual(output["level"], 1)
        self.assertTrue(received["body"]["password"] == request["password"],
                        "Unicode credential changed across the C++/Python wire.")
        self.assertEqual(received["op"], "login")

    def test_mismatched_response_id(self):
        self.rejects(lambda response: response.update(id="not-the-request"))

    def test_status_success_flag_conflicts(self):
        for changes in ({"status": 401}, {"ok": False}, {"status": 201}):
            with self.subTest(case=tuple(changes.keys())):
                self.rejects(lambda response: response.update(changes))

    def test_unknown_response_fields_and_missing_login_token(self):
        for change in (
            lambda response: response.update(extra=True),
            lambda response: response["data"].update(extra=True),
            lambda response: response["data"]["account"].update(extra=True),
            lambda response: response["data"].pop("token"),
            lambda response: response["data"].update(token="short"),
        ):
            self.rejects(change)

    def test_malformed_leader_and_account_are_rejected(self):
        changes = (
            lambda account: account["leader"].update(level="1"),
            lambda account: account["leader"].update(level=True),
            lambda account: account["leader"].update(experience=-1),
            lambda account: account["leader"].update(total_experience=2147483648),
            lambda account: account["leader"].update(starter_id="arbitrary"),
            lambda account: account["leader"].update(unit_type="Spearman\nOther"),
            lambda account: account.update(id="not-a-uuid"),
            lambda account: account.update(username="<Player>"),
        )
        for change in changes:
            self.rejects(lambda response: change(response["data"]["account"]))

    def test_duplicate_response_keys_rejected(self):
        def responder(request):
            normal = compact(self.success(request))
            return b'{"v":1,' + normal[1:]
        output, _ = self.exercise(responder)
        self.assertFalse(output["ok"])
        self.assertEqual(output["error_code"], "protocol")

    def test_401_and_403_revoke_session_meaning_and_do_not_echo_server_message(self):
        secret_message = "server-text-must-not-be-reflected"
        for operation in ("me", "logout"):
            request = {"operation": operation, "token": "b" * 43}
            for status in (401, 403):
                for code in ("scope_mismatch", "invalid_credentials"):
                    def responder(received):
                        return {"v": 1, "id": received["id"], "ok": False, "status": status,
                                "data": {"code": code, "message": secret_message}}
                    output, received = self.exercise(responder, request)
                    self.assertFalse(output["ok"])
                    self.assertEqual(output["error_code"], "invalid_session")
                    self.assertTrue(received["token"] == request["token"], "Token changed on the wire.")
                    self.assertEqual(received["body"], {})
                    self.assertNotIn(secret_message, compact(output).decode())

    def test_login_401_keeps_invalid_credentials_meaning(self):
        def responder(received):
            return {"v": 1, "id": received["id"], "ok": False, "status": 401,
                    "data": {"code": "invalid_credentials", "message": "Invalid credentials."}}
        output, _ = self.exercise(responder)
        self.assertFalse(output["ok"])
        self.assertEqual(output["error_code"], "invalid_credentials")

    def test_public_profile_duplicate_and_escaped_duplicate_keys_before_socket(self):
        for key in ('"port"', r'"p\u006frt"'):
            with self.peer() as (port, state):
                profile = self.profile(port)
                text = "{" + key + ":" + str(port) + "," + json.dumps(profile)[1:]
                output = self.invoke(profile, self.login_request(), profile_text=text)
            self.assertFalse(output["ok"])
            self.assertEqual(output["error_code"], "profile_rejected")
            self.assertEqual(state["connections"], 0)
            self.assertIsNone(state["error"])

    def test_duplicate_lexical_counter_with_escaped_strings_nested_objects_and_arrays(self):
        documents = [
            (compact({"name": 'value: with " quote',
                      "items": [{"a": 1}, {"a": 2}]}).decode(), True),
            ('{"items":[{"a":1,"a":2}]}', False),
            (r'{"a":1,"\u0061":2}', False),
            ('{"outer":{"a":{"x":1},"a":{"y":2}}}', False),
            (compact({"a:b": "c:d", "escaped": 'backslash\\ and quote" : colon'}).decode(), True),
        ]
        for text, expected in documents:
            output = self.invoke(self.profile(15001),
                                 {"mode": "parse_document", "document": text})
            self.assertEqual(output["ok"], expected)
            self.assertEqual(output["error_code"], "" if expected else "document_rejected")

    def test_one_total_deadline_covers_preamble_and_stalled_response(self):
        started = time.monotonic()
        output, _ = self.exercise(stall=True, preamble_delay=2)
        elapsed = time.monotonic() - started
        self.assertFalse(output["ok"])
        self.assertEqual(output["error_code"], "timeout")
        self.assertGreaterEqual(output["elapsed_ms"], 9500)
        self.assertLess(output["elapsed_ms"], 11500)
        self.assertLess(elapsed, 12)

    def test_cancellation_interrupts_stalled_response(self):
        request = self.login_request()
        request["cancel_after_ms"] = 1000
        output, _ = self.exercise(request=request, stall=True)
        self.assertFalse(output["ok"])
        self.assertEqual(output["error_code"], "cancelled")
        self.assertGreaterEqual(output["elapsed_ms"], 800)
        self.assertLess(output["elapsed_ms"], 3000)


if __name__ == "__main__":
    unittest.main()
