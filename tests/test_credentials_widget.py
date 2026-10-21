"""Compile the actual Alliance editor with pinned editing helpers and a GUI stub.

The fixture substitutes rendering, focus, event transport and the logging API,
not the credential editor/privacy guard, Unicode operations, selection/deletion
methods or protocol validator.
It does not claim Android keyboard/IME or graphical rendering coverage.
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

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "alliance_game/account_ui.cpp"
SOURCE = UI.read_text(encoding="utf-8")


def extract(path, signature):
    source = (ROOT / path).read_text(encoding="utf-8")
    start = source.index(signature)
    opening = source.index("{", start)
    end, depth = opening + 1, 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


class CredentialDialogContracts(unittest.TestCase):
    def test_show_toggle_uses_pinned_dispatcher_namespace(self):
        header = (ROOT / "engine/src/gui/core/event/dispatcher.hpp").read_text()
        # Resolve the helper's enclosing named namespace from the pinned
        # declaration, instead of accepting the fixture's simplified GUI API.
        namespaces = re.search(r"namespace (gui2)\s*\{\s*class widget;\s*namespace (event)\s*\{", header)
        self.assertIsNotNone(namespaces)
        event_body = header[namespaces.end():header.index("} // namespace event", namespaces.end())]
        self.assertRegex(event_body, r"void connect_signal_notify_modified\(dispatcher& dispatcher, const signal_notification& signal\);")
        call = re.search(r"(gui2(?:::\w+)*)::connect_signal_notify_modified\(show_password,", SOURCE)
        self.assertIsNotNone(call)
        self.assertEqual(call.group(1), "::".join(namespaces.groups()))

    def test_scoped_replacement_and_default_hidden_toggle(self):
        dialog = SOURCE.split("class CredentialsDialog final", 1)[1].split("class AccountJob final", 1)[0]
        self.assertLess(dialog.index('swap_child("password"'), dialog.index("add_to_tab_order(&password)"))
        self.assertIn('show_password.set_label("Show password")', dialog)
        self.assertIn("show_password.set_value(false)", dialog)
        self.assertIn("keyboard_capture(&password)", dialog)
        self.assertNotIn("set_history", dialog)
        self.assertNotIn("register_password", dialog)
        self.assertNotIn("save_to_history", dialog)

    def test_submit_reads_canonical_before_clear_and_cancel_also_clears(self):
        post = SOURCE.split("void post_show() override", 1)[1].split("std::string& username_", 1)[0]
        self.assertLess(post.index("clear_secret(password_)"), post.index("if(get_retval()"))
        self.assertLess(post.index("password_ = password.get_secret()"), post.index('password.set_value("")'))
        self.assertIn("password.set_password_visible(false)", post)
        self.assertIn('find_widget<gui2::toggle_button>("remember_password").set_value(false)', post)
        destructor = extract("alliance_game/account_ui.cpp", "~CredentialPasswordBox()")
        self.assertIn("clear_secret(secret_)", destructor)
        self.assertNotIn("set_value(", destructor)
        self.assertNotIn("render(", destructor)

    def test_pinned_event_dispatch_reaches_virtual_overrides(self):
        header = (ROOT / "engine/src/gui/widgets/text_box_base.hpp").read_text()
        base = (ROOT / "engine/src/gui/widgets/text_box_base.cpp").read_text()
        self.assertIn("virtual void handle_commit(bool& handled", header)
        self.assertIn("virtual void handle_editing(bool& handled", header)
        self.assertIn("std::bind(&text_box_base::handle_editing, this", base)
        self.assertIn("std::bind(&text_box_base::handle_commit, this", base)
        editor = extract("alliance_game/account_ui.cpp", "class CredentialPasswordBox final")
        self.assertNotIn("set_history", editor)
        self.assertNotIn("copy_to_clipboard", editor)

    def test_render_privacy_outlives_window_and_covers_pinned_text_log_domains(self):
        declaration = SOURCE.split("class CredentialsDialog final", 1)[1].split("{", 1)[0]
        self.assertLess(declaration.index("CredentialRenderPrivacy"), declaration.index("modal_dialog"))
        guard = extract("alliance_game/account_ui.cpp", "class CredentialRenderPrivacy")
        self.assertIn('{{"gui/layout", "gui/draw"}}', guard)
        self.assertIn("lg::severity::LG_NONE", guard)
        self.assertIn('font::get_text_renderer().set_text("", false)', guard)
        destructor = extract("alliance_game/account_ui.cpp", "~CredentialRenderPrivacy()")
        self.assertLess(destructor.index("get_text_renderer"), destructor.index("restore()"))
        self.assertIn("catch(...)", destructor)
        self.assertIn("return;", destructor)
        pinned_font = (ROOT / "engine/src/font/text.cpp").read_text()
        pinned_canvas = (ROOT / "engine/src/gui/core/canvas.cpp").read_text()
        self.assertIn('<< " text \'" << gui2::debug_truncate(text_)', pinned_font)
        self.assertIn('DBG_GUI_D << "Text: Rendering \'" << text', pinned_canvas)


class CredentialEditorBehavior(unittest.TestCase):
    def test_actual_editor_with_dummy_input(self):
        compiler = shutil.which(os.environ.get("CXX", "c++"))
        if not compiler:
            self.skipTest("A C++17 compiler is required for the actual editor fixture")
        unicode = "engine/src/serialization/unicode.cpp"
        base = "engine/src/gui/widgets/text_box_base.cpp"
        box = "engine/src/gui/widgets/text_box.cpp"
        methods = {
            "UNICODE": [(unicode, name) for name in (
                "static int byte_size_from_utf8_first(", "std::size_t index(",
                "std::size_t size(std::string_view", "std::string& insert(",
                "std::string& erase(", "std::string& truncate(")],
            "BASE": [(base, name) for name in (
                "void text_box_base::set_value(", "void text_box_base::set_cursor(",
                "void text_box_base::set_selection(", "void text_box_base::handle_key_backspace(",
                "void text_box_base::handle_key_delete(")]
                + [(box, "void text_box::delete_char(")],
            "SECRET": [("alliance_game/account_ui.cpp", "void clear_secret(")],
            "EDITOR": [("alliance_game/account_ui.cpp", "class CredentialPasswordBox final")],
            "PRIVACY": [("alliance_game/account_ui.cpp", "class CredentialRenderPrivacy")],
        }
        fixture = (ROOT / "tests/credentials_widget_fixture.cpp.in").read_text()
        for marker, sources in methods.items():
            text = "\n\n".join(extract(path, name) for path, name in sources)
            if marker in {"EDITOR", "PRIVACY"}:
                text += ";"
            fixture = fixture.replace("// @" + marker + "@", text)
        audit = os.environ.get("ALLIANCE_CREDENTIAL_AUDIT")
        with tempfile.TemporaryDirectory(prefix="alliance-credential-") as temporary:
            output = Path(audit) if audit else Path(temporary)
            output.mkdir(parents=True, exist_ok=True)
            source = output / "credentials_widget_fixture.cpp"
            binary = output / "credentials_widget_fixture"
            source.write_text(fixture)
            command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-O0", "-g",
                       "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                       "-DBOOST_ALL_NO_LIB", "-DBOOST_ERROR_CODE_HEADER_ONLY",
                       "-I" + str(ROOT / "build/android-boost/prefix/include"),
                       "-I" + str(ROOT / "alliance_native"), str(source),
                       str(ROOT / "alliance_native/account_protocol.cpp"), "-lz", "-o", str(binary)]
            build = subprocess.run(command, capture_output=True, text=True, timeout=180)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn("PASS all credential editor cases", run.stdout)
            if audit:
                (output / "behavior.log").write_text(run.stdout + run.stderr)
                paths = {path for sources in methods.values() for path, _ in sources}
                paths.update({"alliance_native/account_protocol.cpp", "tests/credentials_widget_fixture.cpp.in",
                              "engine/src/font/text.cpp", "engine/src/gui/core/canvas.cpp",
                              "engine/src/log.cpp", "engine/src/log.hpp",
                              "engine/src/gui/core/event/dispatcher.hpp"})
                (output / "source-evidence.json").write_text(json.dumps({
                    "command": command, "exit_code": run.returncode,
                    "cases": run.stdout.count("PASS ") - 1,
                    "source_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                                      for path in sorted(paths)},
                    "limitations": __doc__,
                }, indent=2) + "\n")
            print(run.stdout, end="")


if __name__ == "__main__":
    unittest.main()
