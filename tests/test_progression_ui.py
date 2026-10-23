"""Run the shipped progression/order/result UI controllers behind scripted boundaries.

Uses the real private UI includes, consent selectors, strict account JSON parser,
progression response validator and campaign launch policy. Rendering, network,
local journals and engine launch/replay are substituted. No Android UI, real
account, database, or reward is modified by this fixture.
"""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests.test_battle_practice_ui import extract

ROOT = Path(__file__).resolve().parents[1]


class ProgressionUiBehavior(unittest.TestCase):
    def test_shipped_controllers_bind_consent_acknowledgements_and_saved_results(self):
        compiler = shutil.which(os.environ.get("CXX", "c++"))
        self.assertIsNotNone(compiler, "A C++17 compiler is required")
        source = (ROOT / "alliance_game/account_ui.cpp").read_text()
        selectors = "\n".join(extract(source, name) for name in ("int select(", "int select_change("))
        progression = (ROOT / "alliance_game/progression_ui.inc").read_text()
        team = (ROOT / "alliance_game/team_extension_ui.inc").read_text()
        # Replace only the rendered text editor; execute every shipped controller.
        team = team[team.index("void captain_orders("):]
        fixture = (ROOT / "tests/progression_ui_fixture.cpp.in").read_text()
        fixture = fixture.replace("// @SELECTORS@", selectors).replace("// @PROGRESSION@", progression).replace("// @TEAM@", team)
        with tempfile.TemporaryDirectory(prefix="alliance-progression-ui-") as temporary:
            directory = Path(os.environ.get("ALLIANCE_PROGRESSION_UI_AUDIT", temporary))
            directory.mkdir(parents=True, exist_ok=True)
            cpp = directory / "progression_ui_fixture.cpp"
            binary = directory / "progression_ui_fixture"
            cpp.write_text(fixture)
            command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-O0", "-g",
                       "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-I" + str(ROOT),
                       "-I" + str(ROOT / "engine/src"), str(cpp),
                       str(ROOT / "alliance_native/account_protocol.cpp"),
                       str(ROOT / "alliance_game/progression_plan.cpp"), "-lz", "-o", str(binary)]
            built = subprocess.run(command, capture_output=True, text=True, timeout=180)
            (directory / "compile.log").write_text(built.stdout + built.stderr)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            (directory / "behavior.log").write_text(result.stdout + result.stderr)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS all progression UI cases", result.stdout)
            print(result.stdout, end="")
            paths = ["alliance_game/account_ui.cpp", "alliance_game/progression_ui.inc", "alliance_game/team_extension_ui.inc",
                     "alliance_native/progression_protocol.hpp", "alliance_native/account_protocol.cpp",
                     "alliance_game/progression_plan.cpp", "tests/progression_ui_fixture.cpp.in", "tests/test_progression_ui.py"]
            (directory / "validation.json").write_text(json.dumps({"command": command, "exit_code": result.returncode,
                "cases": result.stdout.count("PASS ") - 1, "limitations": __doc__,
                "source_sha256": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths}}, indent=2) + "\n")


if __name__ == "__main__":
    unittest.main()
