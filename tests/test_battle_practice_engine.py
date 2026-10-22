"""Real Wesnoth 1.19.26 execution of production Alliance practice scenarios.

Optional host integration: explicitly provide the isolated host engine/exporter,
original game data and a fresh output directory. These checks execute real map,
unit, faction, event, AI-turn and replay behavior. They are not Android UI tests
and never connect to the account service or mutate a saved battle plan.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import unittest

REQUIRED = ("ALLIANCE_PRACTICE_ENGINE", "ALLIANCE_PRACTICE_EXPORTER",
            "ALLIANCE_PRACTICE_DATA", "ALLIANCE_PRACTICE_SCRATCH")


@unittest.skipUnless(all(os.environ.get(key) for key in REQUIRED),
                     "Requires explicit isolated host engine and scenario exporter")
class PracticeEngineIntegration(unittest.TestCase):
    def test_real_scenarios_leaders_alliances_ai_round_and_replay(self):
        engine = Path(os.environ[REQUIRED[0]]).resolve()
        exporter = Path(os.environ[REQUIRED[1]]).resolve()
        data = Path(os.environ[REQUIRED[2]]).resolve()
        scratch = Path(os.environ[REQUIRED[3]]).resolve()
        for executable in (engine, exporter):
            self.assertTrue(executable.is_file())
        self.assertTrue((data / "data/core/units.cfg").is_file())
        scratch.mkdir(parents=True, exist_ok=False)
        userdata = scratch / "userdata"
        userdata.mkdir()
        env = os.environ.copy()
        env.update(SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
        env["XDG_CACHE_HOME"] = str(scratch / "font-cache")
        (scratch / "font-cache").mkdir()
        records = []

        def run(name, arguments, timeout=180):
            try:
                result = subprocess.run(list(map(str, arguments)), env=env,
                                        capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired as error:
                output = error.stdout or b""
                errors = error.stderr or b""
                if isinstance(output, bytes):
                    output = output.decode(errors="replace")
                if isinstance(errors, bytes):
                    errors = errors.decode(errors="replace")
                (scratch / (name + ".log")).write_text(output + errors + "\nTIMED OUT\n")
                records.append({"name": name, "exit": "timeout",
                                "command": list(map(str, arguments))})
                (scratch / "commands.json").write_text(json.dumps(records, indent=2))
                raise
            (scratch / (name + ".log")).write_text(result.stdout + result.stderr)
            records.append({"name": name, "exit": result.returncode,
                            "command": list(map(str, arguments))})
            (scratch / "commands.json").write_text(json.dumps(records, indent=2))
            self.assertEqual(result.returncode, 0, f"{name}: {result.stdout[-2000:]} {result.stderr[-4000:]}")
            return result.stdout + result.stderr

        version = run("version", [engine, "--version"])
        self.assertIn("1.19.26", version)
        includes = scratch / "upstream-practice-input.cfg"
        includes.write_text("{multiplayer/eras.cfg}\n"
                            "{multiplayer/scenarios/4p_Isars_Cross.cfg}\n"
                            "{multiplayer/scenarios/6p_Hexcake.cfg}\n"
                            "{multiplayer/scenarios/8p_Mokena_Prairie.cfg}\n")
        preprocessed = scratch / "preprocessed"
        preprocessed.mkdir()
        run("preprocess", [engine, data, "--userdata-dir", userdata,
                           "--preprocess", includes, preprocessed])
        source = preprocessed / "upstream-practice-input.cfg"
        self.assertTrue(source.is_file())
        addon = userdata / "data/add-ons/alliance_practice_engine"
        output = run("export", [exporter, source, data, addon])
        self.assertIn("PRACTICE SCENARIO EXPORT PASSED: scenarios=8; rejected_inputs=40; unit_types=24", output)
        cases = [line.split("\t") for line in (addon / "cases.tsv").read_text().splitlines()]
        self.assertEqual(len(cases), 8)
        (addon / "_main.cfg").write_text("#ifdef TEST\n" + "".join(
            "{~add-ons/alliance_practice_engine/" + row[0] + ".cfg}\n" for row in cases) + "#endif\n")
        results = []
        for index, (scenario_id, map_id, controlled_side, side_count) in enumerate(cases):
            with self.subTest(scenario=scenario_id, map=map_id):
                # The supplied stock playlist references absent undead_siege.ogg.
                # --nosound still resolves playlist paths in this engine version.
                # Audio is outside this headless fixture; other errors stay strict.
                text = run(scenario_id, [engine, data, "--userdata-dir", userdata,
                                        "--unit", scenario_id, "--log-strict", "error",
                                        "--no-log-to-file", "--nosound", "--log-none=audio"])
                self.assertIn(f"PRACTICE START VERIFIED {index}", text)
                self.assertIn(f"PRACTICE AI ROUND VERIFIED {index}", text)
                self.assertIn(f"PASS TEST (0): {scenario_id}", text)
                self.assertNotIn("FAIL TEST", text)
                results.append({"scenario": scenario_id, "map": map_id,
                                "human_side": int(controlled_side), "sides": int(side_count),
                                "result": "passed", "replay_check_enabled": True})
        (scratch / "result.json").write_text(json.dumps({
            "scope": "real Linux host engine; no Android GUI, account service or persistent rewards",
            "engine_sha256": hashlib.sha256(engine.read_bytes()).hexdigest(),
            "exporter_sha256": hashlib.sha256(exporter.read_bytes()).hexdigest(),
            "cases": results,
            "rejected_installed_inputs": 40,
            "saved_unit_types_exercised": 24,
            "unit_serialization_reconstructions_per_leader": 2,
            "audio_log_excluded": "Stock playlist references absent undead_siege.ogg; no-audio fixture only",
            "status": "passed" if len(results) == len(cases) else "failed",
        }, indent=2) + "\n")
        self.assertEqual(len(results), len(cases))
        print("PASS real production practice scenarios: 3 maps, all 24 leader types, AI full round, replay validation", flush=True)


if __name__ == "__main__":
    unittest.main()
