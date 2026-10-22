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
        phase = os.environ.get("ALLIANCE_PRACTICE_PHASE", "all")
        self.assertIn(phase, ("all", "lifecycle"), "Only the explicit lifecycle phase may omit already-validated scenario cases")
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
        self.assertIn("PRACTICE SCENARIO EXPORT PASSED: scenarios=18; rejected_inputs=180; unit_types=24", output)
        cases = [line.split("\t") for line in (addon / "cases.tsv").read_text().splitlines()]
        self.assertEqual(len(cases), 18)
        (addon / "_main.cfg").write_text("#ifdef TEST\n" + "".join(
            "{~add-ons/alliance_practice_engine/" + row[0] + ".cfg}\n" for row in cases) + "#endif\n"
            "#ifdef MULTIPLAYER\n" + "".join("{~add-ons/alliance_practice_engine/" + name + ".cfg}\n"
                for name in ("alliance_practice_engine_0_save", "alliance_practice_engine_9_save",
                             "alliance_practice_engine_16_save", "alliance_practice_timeout")) + "#endif\n")
        results = []
        scenario_cases = cases if phase == "all" else []
        for index, (scenario_id, map_id, controlled_side, side_count, mode, humans, gold, turns, fog) in enumerate(scenario_cases):
            with self.subTest(scenario=scenario_id, map=map_id):
                # The supplied stock playlist references absent undead_siege.ogg.
                # --nosound still resolves playlist paths in this engine version.
                # Audio is outside this headless fixture; other errors stay strict.
                text = run(scenario_id, [engine, data, "--userdata-dir", userdata,
                                        "--unit", scenario_id, "--log-strict", "error",
                                        "--no-log-to-file", "--nosound", "--log-none=audio"])
                self.assertIn(f"PRACTICE START VERIFIED {index}", text)
                self.assertIn(f"PRACTICE ROUND VERIFIED {index}", text)
                self.assertIn(f"PASS TEST (0): {scenario_id}", text)
                self.assertNotIn("FAIL TEST", text)
                results.append({"scenario": scenario_id, "map": map_id,
                                "viewer_side": int(controlled_side), "sides": int(side_count),
                                "mode": mode, "human_sides": int(humans), "starting_gold": int(gold),
                                "turn_limit": int(turns), "fog": fog == "1",
                                "result": "passed", "replay_check_enabled": True})
        # Exercise the actual local multiplayer controller, disk save, fresh
        # process load, and another complete round in all three control modes.
        # WML only ends first-round human turns. Afterwards a stock engine
        # plugin invokes the same save/end-turn callbacks as the game UI.
        saves = []
        for index in (0, 9, 16):
            scenario_id = f"alliance_practice_engine_{index}_save"
            save_name = f"training-mode-{index}"
            save_plugin = scratch / f"save-{index}.lua"
            save_plugin.write_text("""
return function(events, context, info)
    local requested = false
    while true do
        if info.name == 'Game' and info.can_move().can_move and
                info.current_turn().current_turn == 2 and not requested then
            requested = true
            std_print('PRACTICE SAVE REQUESTED')
            context.save_game{filename=SAVE_NAME}
            events, context, info = wesnoth.plugin.next_slice()
            if info.name == 'Game' then context.quit{} end
        elseif info.name == 'Dialog' then context.skip_dialog{}
        elseif info.name == 'titlescreen' and requested then context.exit{code=0}
        end
        events, context, info = wesnoth.plugin.next_slice()
    end
end
""".replace('SAVE_NAME', json.dumps(save_name)))
            text = run(f"save-{index}", [engine, data, "--userdata-dir", userdata,
                       "--multiplayer", "--scenario", scenario_id, "--nogui", "--nosound",
                       "--no-log-to-file", "--log-strict", "error", "--log-none=audio",
                       "--plugin", save_plugin])
            self.assertIn(f"PRACTICE ROUND VERIFIED {index}", text)
            self.assertIn("PRACTICE SAVE REQUESTED", text)
            files = list((userdata / "sync/saves").glob(save_name + "*"))
            self.assertEqual(len(files), 1, files)
            self.assertGreater(files[0].stat().st_size, 100)
            before_hash = hashlib.sha256(files[0].read_bytes()).hexdigest()
            resume_plugin = scratch / f"resume-{index}.lua"
            assertions = (addon / f"alliance_practice_engine_{index}_resume.lua").read_text()
            resume_plugin.write_text("""
return function(events, context, info)
    local verified, done = false, false
    while true do
        if info.name == 'Game' and info.can_move().can_move then
            local turn = info.current_turn().current_turn
            if not verified then
                assert(turn == 2, 'disk save must resume the saved round')
                verified = true
                wesnoth.plugin.execute(context, function()
                    RESUME_ASSERTIONS
                    std_print('PRACTICE DISK RELOAD VERIFIED')
                end)
            elseif turn >= 3 then
                done = true
                std_print('PRACTICE RESUMED ROUND VERIFIED')
                context.quit{}
            else context.end_turn{} end
        elseif info.name == 'Dialog' then context.skip_dialog{}
        elseif info.name == 'titlescreen' and done then context.exit{code=0}
        end
        events, context, info = wesnoth.plugin.next_slice()
    end
end
""".replace('RESUME_ASSERTIONS', assertions))
            text = run(f"resume-{index}", [engine, data, "--userdata-dir", userdata,
                       "--load", files[0].name, "--nogui", "--nosound", "--no-log-to-file",
                       "--log-strict", "error", "--log-none=audio", "--plugin", resume_plugin])
            self.assertIn("PRACTICE DISK RELOAD VERIFIED", text)
            self.assertIn("PRACTICE RESUMED ROUND VERIFIED", text)
            self.assertEqual(hashlib.sha256(files[0].read_bytes()).hexdigest(), before_hash)
            saves.append({"mode": cases[index][4], "map": cases[index][1],
                          "save_sha256": before_hash, "loaded_round": 2,
                          "resumed_through_round": 3, "result": "passed"})
        text = run("time-limit", [engine, data, "--userdata-dir", userdata,
                   "--multiplayer", "--scenario", "alliance_practice_timeout", "--nogui",
                   "--nosound", "--no-log-to-file", "--log-strict", "error", "--log-none=audio"])
        self.assertIn("PRACTICE TIME LIMIT REACHED", text)
        self.assertIn("PRACTICE STOCK TIMEOUT DEFEAT VERIFIED", text)
        (scratch / "result.json").write_text(json.dumps({
            "scope": "real Linux host engine; no Android GUI, account service or persistent rewards",
            "engine_sha256": hashlib.sha256(engine.read_bytes()).hexdigest(),
            "exporter_sha256": hashlib.sha256(exporter.read_bytes()).hexdigest(),
            "phase": phase,
            "cases": results,
            "disk_save_reload": saves,
            "finite_turn_limit_native_defeat": "passed",
            "rejected_installed_inputs_and_settings": 180,
            "saved_unit_types_exercised": 24,
            "unit_serialization_reconstructions_per_leader": 2,
            "audio_log_excluded": "Stock playlist references absent undead_siege.ogg; no-audio fixture only",
            "status": "passed" if len(results) == len(scenario_cases) else "failed",
        }, indent=2) + "\n")
        self.assertEqual(len(results), len(scenario_cases))
        print(f"PASS real production practice: {len(results)} scenario/replay cases; three actual disk saves, fresh-process reloads and finite-turn defeat", flush=True)


if __name__ == "__main__":
    unittest.main()
