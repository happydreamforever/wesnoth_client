"""Real native battle-planning client against disposable authenticated TLS peers."""
import copy
import unittest

from test_account_client_responses import AccountClientResponseTests


class BattleClientResponseTests(AccountClientResponseTests):
    @staticmethod
    def maps():
        return [
            {"id": "isars-cross", "name": "Isar's Cross", "scenario_id": "multiplayer_Isars_Cross",
             "size": 2, "sides": [[1, 4], [2, 3]]},
            {"id": "hexcake", "name": "Hexcake", "scenario_id": "multiplayer_Hexcake",
             "size": 3, "sides": [[1, 4, 5], [2, 3, 6]]},
            {"id": "mokena-prairie", "name": "Mokena Prairie", "scenario_id": "multiplayer_8p_Mokena_Prairie",
             "size": 4, "sides": [[1, 3, 5, 8], [2, 4, 6, 7]]},
        ]

    @classmethod
    def battle(cls, index=1, map_index=0):
        map_entry = cls.maps()[map_index]
        teams = []
        for i in range(2):
            players = [{"id": f"{i * 4 + j + 1:08x}-1234-4321-8765-0123456789ab", "username": f"Player_{i}_{j}",
                        "selection": {"hero_id":"konrad", "form_id":"konrad_fighter", "unit_type":"AllianceHero_konrad_fighter", "unit_level":1, "hero_level":1, "hero_revision":1}, "side": side, "ready": False}
                       for j, side in enumerate(map_entry["sides"][i])]
            teams.append({"id": f"{i + 100:08x}-1234-4321-8765-0123456789ab", "name": f"Team {i}",
                          "captain_id": players[0]["id"], "approved": False, "players": players})
        return {"id": f"c{index:07x}-1234-4321-8765-0123456789ab", "area_id": "area-1", "state": "draft",
                "created_at": "2026-10-21T12:00:00Z", "map": map_entry, "validity": "valid", "teams": teams}

    @classmethod
    def summary(cls, index=1):
        battle = cls.battle(index)
        return {key: battle[key] for key in ("id", "area_id", "state", "created_at")} | {
            "map_id": battle["map"]["id"], "teams": [{"id": t["id"], "name": t["name"]} for t in battle["teams"]]}

    @staticmethod
    def proposal(battle):
        return {"team_id": battle["teams"][0]["id"], "opponent_team_id": battle["teams"][1]["id"],
                "map_id": battle["map"]["id"],
                "lineups": [{"team_id": t["id"], "account_ids": [p["id"] for p in t["players"]]} for t in battle["teams"]]}

    def reject_battle(self, operation, data, **fields):
        output, _ = self.exercise(self.team_reply(data, 201 if operation == "propose_battle" else 200),
                                  self.team_request(operation, **fields))
        self.assertFalse(output["ok"])
        self.assertEqual(output["error_code"], "protocol")
        self.assertFalse(output["has_battle"])
        self.assertFalse(output["has_token"])

    def test_battle_operations_exact_request_bodies_and_success_states(self):
        battle = self.battle()
        cases = [("battle_maps", {"area_id": "area-1"}, {"area_id": "area-1", "maps": self.maps()}),
                 ("my_battles", {"after": ""}, {"battles": [self.summary()], "next": ""}),
                 ("battle_detail", {"battle_id": battle["id"]}, {"battle": battle}),
                 ("propose_battle", self.proposal(battle), {"battle": battle}),
                 ("approve_battle", {"battle_id": battle["id"]}, {"battle": battle}),
                 ("battle_ready", {"battle_id": battle["id"], "ready": False}, {"battle": battle}),
                 ("cancel_battle", {"battle_id": battle["id"]}, {"battle": {**battle, "state": "cancelled"}})]
        for operation, fields, response in cases:
            with self.subTest(operation=operation):
                output, received = self.exercise(self.team_reply(response, 201 if operation == "propose_battle" else 200),
                                                 self.team_request(operation, **fields))
                self.assertTrue(output["ok"])
                self.assertEqual(received["body"], fields)
                self.assertEqual(output["has_battle"], "battle" in response)
        ready = copy.deepcopy(battle)
        for team in ready["teams"]:
            team["approved"] = True
            for player in team["players"]:
                player["ready"] = True
        output, received = self.exercise(self.team_reply({"battle": ready}),
            self.team_request("battle_ready", battle_id=battle["id"], ready=True))
        self.assertTrue(output["ok"])
        self.assertIs(received["body"]["ready"], True)
        for map_index in (1, 2):
            larger = self.battle(map_index=map_index)
            fields = self.proposal(larger)
            output, received = self.exercise(self.team_reply({"battle": larger}, 201),
                                             self.team_request("propose_battle", **fields))
            self.assertTrue(output["ok"])
            self.assertEqual(received["body"], fields)

    def test_battle_maps_require_exact_pinned_ids_names_scenarios_sizes_and_sides(self):
        for maps in ([], self.maps(), list(reversed(self.maps()))):
            output, _ = self.exercise(self.team_reply({"area_id": "area-1", "maps": maps}), self.team_request("battle_maps", area_id="area-1"))
            self.assertTrue(output["ok"])
            self.assertEqual(output["map_count"], len(maps))
        cases = [{"area_id": "area-2", "maps": self.maps()}, {"area_id": "area-1", "maps": self.maps() * 2},
                 {"area_id": "area-1", "maps": [self.maps()[0]] * 2}, {"area_id": "area-1", "maps": [], "next": ""}]
        for map_entry in self.maps():
            changes = [("id", "unknown-map"), ("name", "Wrong name"), ("scenario_id", "multiplayer_fake"),
                       ("size", True), ("size", 8), ("sides", []), ("sides", map_entry["sides"][::-1]),
                       ("sides", [[True] * map_entry["size"], map_entry["sides"][1]]),
                       ("sides", [map_entry["sides"][0] + [7], map_entry["sides"][1]]), ("private", "hidden")]
            for key, value in changes:
                cases.append({"area_id": "area-1", "maps": [{**map_entry, key: value}]})
            for key in map_entry:
                cases.append({"area_id": "area-1", "maps": [{k: v for k, v in map_entry.items() if k != key}]})
        for data in cases:
            self.reject_battle("battle_maps", data, area_id="area-1")

    def test_battle_summary_pages_are_bounded_unique_sorted_and_cursor_bound(self):
        page = [self.summary(i) for i in range(1, 21)]
        output, received = self.exercise(self.team_reply({"battles": page, "next": page[-1]["id"]}), self.team_request("my_battles"))
        self.assertTrue(output["ok"])
        self.assertEqual(output["battle_count"], 20)
        self.assertEqual(received["body"], {"after": ""})
        output, _ = self.exercise(self.team_reply({"battles": [], "next": ""}), self.team_request("my_battles", after=page[-1]["id"]))
        self.assertTrue(output["ok"])
        cases = [{"battles": page + [self.summary(21)], "next": ""}, {"battles": page[:1] * 2, "next": ""},
                 {"battles": page[::-1], "next": ""}, {"battles": page[:1], "next": page[0]["id"]},
                 {"battles": page, "next": page[0]["id"]}, {"battles": [], "next": None},
                 {"battles": [], "next": "", "private": "hidden"}]
        for key, value in (("id", "bad"), ("map_id", "fake"), ("state", "pending"), ("area_id", "bad/area"),
                           ("created_at", "2026-02-30T00:00:00Z"), ("teams", []), ("teams", page[0]["teams"][:1] * 2)):
            cases.append({"battles": [{**page[0], key: value}], "next": ""})
        for key in page[0]:
            cases.append({"battles": [{k: v for k, v in page[0].items() if k != key}], "next": ""})
        for data in cases:
            self.reject_battle("my_battles", data)
        self.reject_battle("my_battles", {"battles": page[:1], "next": ""}, after=page[0]["id"])

    def test_battle_detail_all_maps_states_and_stale_validities_remain_readable(self):
        for map_index in range(3):
            battle = self.battle(map_index=map_index)
            output, _ = self.exercise(self.team_reply({"battle": battle}), self.team_request("battle_detail", battle_id=battle["id"]))
            self.assertTrue(output["ok"])
        for state in ("draft", "running", "completed", "cancelled"):
            for validity in ("valid", "roster_changed", "area_changed", "map_unavailable", "leader_changed",
                             "player_unavailable", "team_unavailable", "busy", "rules_changed"):
                battle = self.battle()
                battle.update(state=state, validity=validity)
                # Captains need not occupy a selected slot. Archived team has no current captain.
                battle["teams"][0]["captain_id"] = "ffffffff-1234-4321-8765-0123456789ab"
                if validity == "team_unavailable":
                    battle["teams"][1]["captain_id"] = ""
                output, _ = self.exercise(self.team_reply({"battle": battle}), self.team_request("battle_detail", battle_id=battle["id"]))
                self.assertTrue(output["ok"])
                self.assertEqual(output["battle_state"], state)
                self.assertEqual(output["battle_validity"], validity)

    def test_battle_detail_rejects_private_malformed_duplicate_and_inconsistent_payloads(self):
        base = self.battle()
        cases = [{**base, "id": "bad"}, {**base, "area_id": "../area"}, {**base, "created_at": "2026-02-29T00:00:00Z"},
                 {**base, "state": "started"}, {**base, "validity": "unknown"}, {**base, "private": "secret"},
                 {**base, "teams": base["teams"][:1]}, {**base, "teams": base["teams"] * 2}]
        cases.extend({key: value for key, value in base.items() if key != missing} for missing in base)
        for key, value in (("id", base["teams"][1]["id"]), ("name", "<b>Team</b>"), ("captain_id", "bad"),
                           ("captain_id", base["teams"][1]["captain_id"]), ("approved", 1), ("approved", "false"),
                           ("players", []), ("players", base["teams"][0]["players"] * 3), ("private", "secret")):
            battle = copy.deepcopy(base); battle["teams"][0][key] = value; cases.append(battle)
        battle = copy.deepcopy(base); battle["teams"][0].update(captain_id="", approved=True); cases.append(battle)
        for key, value in (("id", base["teams"][1]["players"][0]["id"]), ("id", "bad"),
                           ("username", "bad<player>"), ("unit_type", "Unknown Leader"), ("unit_type", "Spearman\n"),
                           ("level", 2), ("level", True), ("level", 4), ("side", 4), ("side", True),
                           ("ready", 1), ("ready", "true"), ("ready", None), ("experience", 0)):
            battle = copy.deepcopy(base); battle["teams"][0]["players"][0][key] = value; cases.append(battle)
        for key in base["teams"][0]["players"][0]:
            battle = copy.deepcopy(base); del battle["teams"][0]["players"][0][key]; cases.append(battle)
        for battle in cases:
            self.reject_battle("battle_detail", {"battle": battle}, battle_id=base["id"])

    def test_battle_proposal_binds_ordered_teams_map_and_players_and_requires_fresh_unapproved_snapshot(self):
        base = self.battle()
        fields = self.proposal(base)
        cases = []
        for key, value in (("state", "cancelled"), ("validity", "leader_changed"), ("teams", base["teams"][::-1])):
            cases.append({**base, key: value})
        battle = copy.deepcopy(base); battle["teams"][0]["id"] = "ffffffff-1234-4321-8765-0123456789ab"; cases.append(battle)
        battle = copy.deepcopy(base); battle["teams"][0]["approved"] = True; cases.append(battle)
        battle = copy.deepcopy(base); battle["teams"][0]["players"][0]["ready"] = True; cases.append(battle)
        battle = copy.deepcopy(base)
        battle["teams"][0]["players"][0]["id"], battle["teams"][0]["players"][1]["id"] = (
            battle["teams"][0]["players"][1]["id"], battle["teams"][0]["players"][0]["id"])
        cases.append(battle)
        cases.append(self.battle(map_index=1))
        for battle in cases:
            self.reject_battle("propose_battle", {"battle": battle}, **fields)
        output, _ = self.exercise(self.team_reply({"battle": base}, 200), self.team_request("propose_battle", **fields))
        self.assertEqual(output["error_code"], "protocol")

    def test_battle_mutations_bind_id_state_and_validity_without_implicit_ready_permissions(self):
        base = self.battle()
        for operation in ("battle_detail", "approve_battle", "battle_ready", "cancel_battle"):
            response = {**base, "state": "cancelled" if operation == "cancel_battle" else "draft"}
            self.reject_battle(operation, {"battle": response}, battle_id=self.battle(2)["id"])
        for operation in ("approve_battle", "battle_ready"):
            self.reject_battle(operation, {"battle": {**base, "state": "running"}}, battle_id=base["id"])
        self.reject_battle("cancel_battle", {"battle": base}, battle_id=base["id"])
        stale = {**base, "validity": "roster_changed"}
        self.reject_battle("approve_battle", {"battle": stale}, battle_id=base["id"])
        self.reject_battle("battle_ready", {"battle": stale}, battle_id=base["id"], ready=True)
        self.reject_battle("battle_ready", {"battle": base}, battle_id=base["id"], ready=True)
        for operation, response in (("battle_ready", stale), ("cancel_battle", {**stale, "state": "cancelled"})):
            output, _ = self.exercise(self.team_reply({"battle": response}), self.team_request(operation, battle_id=base["id"]))
            self.assertTrue(output["ok"])

    def test_battle_invalid_request_scope_uuid_lineups_and_unsupported_start_refused_before_socket(self):
        battle = self.battle(); fields = self.proposal(battle)
        requests = [self.team_request("start_battle", battle_id=battle["id"]), self.team_request("battle_maps"),
                    self.team_request("my_battles", after="area-1"), self.team_request("battle_detail"),
                    self.team_request("approve_battle", battle_id="bad"), self.team_request("propose_battle")]
        for key, value in (("team_id", fields["opponent_team_id"]), ("map_id", "unknown"),
                           ("opponent_team_id", "bad"), ("lineups", []), ("lineups", fields["lineups"][::-1]),
                           ("lineups", fields["lineups"][:1]), ("lineups", fields["lineups"] * 2)):
            requests.append(self.team_request("propose_battle", **{**fields, key: value}))
        for ids in ([fields["lineups"][0]["account_ids"][0]] * 2, ["bad", "bad"], [],
                    fields["lineups"][1]["account_ids"], fields["lineups"][0]["account_ids"] + [battle["id"]]):
            changed = copy.deepcopy(fields); changed["lineups"][0]["account_ids"] = ids
            requests.append(self.team_request("propose_battle", **changed))
        for operation, legitimate in (("battle_maps", {"area_id": "area-1"}), ("my_battles", {}),
                                     ("battle_detail", {"battle_id": battle["id"]}), ("propose_battle", fields),
                                     ("approve_battle", {"battle_id": battle["id"]}), ("battle_ready", {"battle_id": battle["id"]}),
                                     ("cancel_battle", {"battle_id": battle["id"]})):
            extras = {"username": "Unused", "password": "unused", "starter_id": "spearman", "name": "Unused", "account_id": battle["id"],
                      "decision_id": battle["id"], "allow_archive": True, "token": "bad", "team_id": fields["team_id"],
                      "opponent_team_id": fields["opponent_team_id"], "map_id": "hexcake", "lineups": fields["lineups"]}
            for key, value in extras.items():
                if key not in legitimate:
                    requests.append(self.team_request(operation, **legitimate, **{key: value}))
        for request in requests:
            with self.peer() as (port, state):
                output = self.invoke(self.profile(port), request)
            self.assertEqual(output["error_code"], "invalid_input")
            self.assertEqual(state["connections"], 0)
            self.assertIsNone(state["error"])

    def test_battle_uncertain_mutation_cancellation_and_discarded_ack_never_retry(self):
        battle = self.battle()
        for operation in ("propose_battle", "approve_battle", "battle_ready", "cancel_battle"):
            fields = self.proposal(battle) if operation == "propose_battle" else {"battle_id": battle["id"]}
            output, _ = self.exercise(request=self.team_request(operation, **fields, cancel_after_ms=300), stall=True)
            self.assertEqual(output["error_code"], "cancelled")
            self.assertLess(output["elapsed_ms"], 3000)
            output, _ = self.exercise(request=self.team_request(operation, **fields), drop_response=True)
            self.assertEqual(output["error_code"], "unavailable")
            self.assertFalse(output["has_battle"])

    def test_battle_safe_errors_preserve_session_except_authenticated_401(self):
        battle = self.battle()
        cases = [(409, code, code) for code in ("planning_exists", "invalid_map", "invalid_lineup", "different_area",
                 "stale_proposal", "advancement_required", "approval_required", "invalid_state", "busy", "not_member", "conflict")]
        cases += [(403, "forbidden", "forbidden"), (401, "forbidden", "invalid_session"), (503, "private", "team_failed")]
        for status, code, expected in cases:
            def responder(request):
                return {"v": 2, "id": request["id"], "ok": False, "status": status,
                        "data": {"code": code, "message": "Private backend detail must not be shown"}}
            output, _ = self.exercise(responder, self.team_request("approve_battle", battle_id=battle["id"]))
            self.assertEqual(output["error_code"], expected)


def load_tests(loader, tests, pattern):
    # The inherited helpers are shared, while the account regression suite is
    # run separately against the same rebuilt binary without duplicate cases.
    return unittest.TestSuite(BattleClientResponseTests(name) for name in
        loader.getTestCaseNames(BattleClientResponseTests) if name.startswith("test_battle_"))


if __name__ == "__main__":
    unittest.main()
