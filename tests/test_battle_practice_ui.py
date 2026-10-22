"""Execute shipped practice/menu controllers with scripted GUI and network stubs.

The fixture extracts actual UI functions and the generated title callback. It
does not render Android widgets or execute the battle engine; those boundaries
are substituted so consent, identity binding and modal unwinding are observable.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "alliance_game/account_ui.cpp"


def extract(source, signature):
    start = source.index(signature)
    opening = source.index("\n{", start) + 1
    end, depth = opening + 1, 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


class PracticeUiBehavior(unittest.TestCase):
    def test_actual_consent_and_all_menu_unwinding(self):
        compiler = shutil.which(os.environ.get("CXX", "c++"))
        self.assertIsNotNone(compiler, "A C++17 compiler is required for practice UI behavior")
        source = UI.read_text(encoding="utf-8")
        signatures = [
            "int select(", "int select_change(", "std::string battle_validity_text(",
            "std::string practice_availability_text(", "std::string battle_text(",
            "bool can_practice_plan(", "bool load_practice_plan(", "void practice_plan(", "bool battle_mutation(",
            "void battle_detail(", "void my_battles(", "void battle_plans(",
            "void teams(", "void show_account_dialog(",
        ]
        functions = "\n\n".join(extract(source, signature) for signature in signatures)
        spec = importlib.util.spec_from_file_location("practice_title_overlay", ROOT / "alliance_game/title_overlay.py")
        overlay = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(overlay)
        original = (ROOT / "engine/src/gui/dialogs/title_screen.cpp").read_bytes()
        generated = overlay.generate_title_overlay(original)
        self.assertEqual(hashlib.sha256(original).hexdigest(), overlay.SOURCE_SHA256)
        self.assertIn(b'if(alliance_game::practice_pending())', generated)
        callback = overlay._NEW.decode("utf-8").split('register_button("addons",', 1)[1]
        callback = callback.split('[this]() {', 1)[1].rsplit('});', 1)[0]
        fixture = (ROOT / "tests/battle_practice_ui_fixture.cpp.in").read_text(encoding="utf-8")
        fixture = fixture.replace("// @FUNCTIONS@", functions).replace("// @TITLE_CALLBACK@", callback)
        audit = os.environ.get("ALLIANCE_PRACTICE_UI_AUDIT")
        with tempfile.TemporaryDirectory(prefix="alliance-practice-ui-") as temporary:
            directory = Path(audit) if audit else Path(temporary)
            directory.mkdir(parents=True, exist_ok=True)
            cpp = directory / "battle_practice_ui_fixture.cpp"
            binary = directory / "battle_practice_ui_fixture"
            cpp.write_text(fixture, encoding="utf-8")
            policy = directory / "practice_policy.o"
            policy_command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-O0", "-g",
                "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-I" + str(ROOT),
                "-Dqueue_practice=unused_queue_practice", "-Dpractice_pending=unused_practice_pending",
                "-Dtake_pending_practice=unused_take_pending_practice", "-c",
                str(ROOT / "alliance_game/practice_plan.cpp"), "-o", str(policy)]
            compiled = subprocess.run(policy_command, capture_output=True, text=True, timeout=120)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-O0", "-g",
                       "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-I" + str(ROOT),
                       str(cpp), str(policy), "-o", str(binary)]
            built = subprocess.run(command, capture_output=True, text=True, timeout=120)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("PASS all practice UI cases", ran.stdout)
            print(ran.stdout, end="")
            if audit:
                (directory / "behavior.log").write_text(ran.stdout + ran.stderr)
                paths = ["alliance_game/account_ui.cpp", "alliance_game/title_overlay.py",
                         "alliance_native/account_client.hpp", "tests/battle_practice_ui_fixture.cpp.in",
                         "engine/src/gui/dialogs/title_screen.cpp", "alliance_game/practice_plan.cpp",
                         "alliance_game/practice_plan.hpp"]
                (directory / "source-evidence.json").write_text(json.dumps({
                    "command": command, "policy_command": policy_command, "exit_code": ran.returncode,
                    "cases": ran.stdout.count("PASS ") - 1,
                    "source_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                                      for path in paths}, "limitations": __doc__,
                }, indent=2) + "\n")


if __name__ == "__main__":
    unittest.main()
