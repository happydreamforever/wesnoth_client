"""Exercise the production credential dialog against the matching WML hierarchy.

The dialog and pinned grid replacement algorithm are compiled unchanged. Widget
storage, typed lookup, drawing, events and the modal loop are test substitutes;
the hierarchy comes from the real 1.19.26 game data. This is construction and
lifecycle coverage, not an Android, SDL, rendering or keyboard acceptance test.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from test_credentials_widget import extract

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("WESNOTH_TEST_GAMEDATA", ROOT.parent / "gamedata-1.19.26"))


class Node:
    def __init__(self, tag):
        self.tag, self.attrs, self.children = tag, {}, []

    def child(self, tag):
        return next(node for node in self.children if node.tag == tag)


def read_layout(path):
    """Read literal WML tags/attributes needed by these two pinned layouts.

    This intentionally does not interpret WML macros, formulas or GUI styling.
    The default window grid is literal inside its resolution macro.
    """
    root = Node("root")
    stack = [root]
    for line in path.read_text().splitlines():
        tag = re.fullmatch(r"\s*\[(/?)([a-z_]+)\]\s*", line)
        if tag:
            closing, name = tag.groups()
            if closing:
                if len(stack) == 1 or stack[-1].tag != name:
                    raise ValueError("Unexpected WML nesting")
                stack.pop()
            else:
                node = Node(name)
                stack[-1].children.append(node)
                stack.append(node)
            continue
        attribute = re.fullmatch(r'\s*([a-z_]+)\s*=\s*(?:_\s*)?"([^"]*)"\s*', line)
        if attribute:
            stack[-1].attrs[attribute[1]] = attribute[2]
    if len(stack) != 1:
        raise ValueError("Unclosed WML node")
    return root


def layout_cpp(window_path, login_path):
    window = read_layout(window_path).child("resolution").child("grid")
    login = read_layout(login_path).child("window").child("resolution").child("grid")
    seen = []

    def render(node, override_id=None):
        node_id = node.attrs.get("id", "") if override_id is None else override_id
        if node.tag == "grid" and node_id == "_window_content_grid":
            node = login
        seen.append((node.tag, node_id))
        if node.tag in {"grid", "definition"}:
            rows = [row for row in node.children if row.tag == "row"]
            columns = [[column for column in row.children if column.tag == "column"] for row in rows]
            widths = {len(row) for row in columns}
            if len(widths) != 1:
                raise ValueError("Nonrectangular or empty fixture grid")
            cells = []
            for row in columns:
                for column in row:
                    if len(column.children) != 1:
                        raise ValueError("Fixture cell must contain exactly one widget")
                    cells.append(render(column.children[0]))
            args = [str(len(rows)), str(widths.pop()), json.dumps(node_id), *cells]
            return "make_grid(" + ", ".join(args) + ")"
        if node.tag == "scrollbar_panel":
            return "std::make_unique<scrollbar_panel>(" + render(node.child("definition")) + ")"
        if node.tag in {"label", "button", "toggle_button"}:
            args = json.dumps(node_id) + ", " + json.dumps(node.attrs.get("label", ""))
        else:
            args = json.dumps(node_id)
        kind = "widget" if node.tag == "spacer" else node.tag
        if kind not in {"widget", "label", "button", "toggle_button", "text_box", "password_box"}:
            raise ValueError("Unsupported fixture widget: " + kind)
        return "std::make_unique<" + kind + ">(" + args + ")"

    source = "std::unique_ptr<grid> stock_layout() { return " + render(window) + "; }"
    required = {("scrollbar_panel", ""), ("grid", "_window_content_grid"),
                ("password_box", "password"), ("text_box", "user_name"),
                ("toggle_button", "remember_password")}
    if not required.issubset(seen):
        raise ValueError("Matching credential layout structure is missing")
    return source


class CredentialDialogConstruction(unittest.TestCase):
    def test_android_reset_dialog_and_matching_layout(self):
        self.run_dialog(android=True)

    def test_actual_dialog_and_pinned_swap_with_matching_layout(self):
        self.run_dialog(android=False)

    def run_dialog(self, android):
        compiler = shutil.which(os.environ.get("CXX", "c++"))
        if not compiler:
            self.skipTest("A C++17 compiler is required for the dialog fixture")
        window = DATA / "data/gui/themes/default/widgets/window_default.cfg"
        login = DATA / "data/gui/themes/default/dialogs/mp_login.cfg"
        if not window.is_file() or not login.is_file():
            self.skipTest("Matching 1.19.26 game data is required for the dialog hierarchy")
        fixture_path = ROOT / "tests/credentials_dialog_fixture.cpp.in"
        fixture = fixture_path.read_text()
        dialog = extract("alliance_game/account_ui.cpp", "class CredentialsDialog final") + ";"
        # Mutation recreates the 1192607 replacement in the same production
        # pre_show body. It must fail on the WML-derived stock and wrapped trees.
        legacy = dialog.replace("CredentialsDialog", "LegacyCredentialsDialog")
        start = legacy.index('auto& original_password = find_widget<gui2::text_box>("password");')
        end = legacy.index("auto& password = *replacement_pointer;", start) + len("auto& password = *replacement_pointer;")
        legacy = legacy[:start] + '''get_grid().swap_child("password", std::make_unique<CredentialPasswordBox>(
            gui2::implementation::builder_text_box(password_config)), true);
        auto& password = find_widget<CredentialPasswordBox>("password");''' + legacy[end:]
        replacements = {
            "GRID_SWAP": extract("engine/src/gui/widgets/grid.cpp", "std::unique_ptr<widget> grid::swap_child("),
            "CLEAR_SECRET": extract("alliance_game/account_ui.cpp", "void clear_secret("),
            "DIALOG": dialog,
            "LEGACY_DIALOG": legacy,
            "LAYOUT": layout_cpp(window, login),
        }
        for marker, value in replacements.items():
            fixture = fixture.replace("// @" + marker + "@", value)
        with tempfile.TemporaryDirectory(prefix="alliance-credential-dialog-") as temporary:
            audit = os.environ.get("ALLIANCE_CREDENTIAL_DIALOG_AUDIT")
            output = Path(audit) / ("android" if android else "desktop") if audit else Path(temporary)
            output.mkdir(parents=True, exist_ok=True)
            source, binary = output / "credential_dialog.cpp", output / "credential_dialog"
            source.write_text(fixture)
            command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-O0", "-g",
                       "-fsanitize=address,undefined", "-fno-omit-frame-pointer", str(source), "-o", str(binary)]
            if android: command.insert(1, "-D__ANDROID__")
            build = subprocess.run(command, capture_output=True, text=True, timeout=180)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn("PASS all credential dialog construction cases", run.stdout)
            if audit:
                (output / "behavior.log").write_text(run.stdout + run.stderr)
                paths = [ROOT / "alliance_game/account_ui.cpp", ROOT / "engine/src/gui/widgets/grid.cpp",
                         fixture_path, Path(__file__), window, login]
                (output / "source-evidence.json").write_text(json.dumps({
                    "command": command, "exit_code": run.returncode, "cases": run.stdout.count("PASS ") - 1,
                    "source_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
                    "limitations": __doc__,
                }, indent=2) + "\n")
            print(run.stdout, end="")


if __name__ == "__main__":
    unittest.main()
