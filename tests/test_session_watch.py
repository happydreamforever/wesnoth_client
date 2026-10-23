"""Run the production session watcher with real threads and bounded fake boundaries."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SessionWatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="session-watch-")
        cls.addClassCleanup(cls.temp.cleanup)
        folder = Path(cls.temp.name)
        (folder / "alliance_game").mkdir()
        (folder / "events.hpp").write_text("""#pragma once
namespace events { class pump_monitor { public: virtual ~pump_monitor() = default; virtual void process() = 0; }; }
""")
        (folder / "alliance_game/account_ui.hpp").write_text("""#pragma once
#include "alliance_native/account_client.hpp"
#include <optional>
namespace alliance_game {
struct PlayerSession {
    alliance::TrustedProfile trusted;
    alliance::account::Profile account;
    std::string token;
    ~PlayerSession();
};
bool has_player_session() noexcept;
std::optional<PlayerSession> player_session();
void invalidate_player_session();
bool ensure_player_session(const std::string&, const std::string&, const std::string&);
}
""")
        (folder / "alliance_game/android_lifecycle.hpp").write_text("""#pragma once
namespace alliance_game { void finish_game_task() noexcept; }
""")
        cls.binary = folder / "session-watch"
        compiler = shutil.which(os.environ.get("CXX", "c++"))
        if compiler is None:
            raise AssertionError("A C++ compiler is required for the watcher regression.")
        built = subprocess.run([
            compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-pthread",
            "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-g",
            "-I" + str(folder), "-I" + str(ROOT),
            str(ROOT / "alliance_game/session_watch.cpp"),
            str(ROOT / "tests/session_watch_fixture.cpp"), "-o", str(cls.binary),
        ], capture_output=True, text=True)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    def run_fixture(self, mode):
        result = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("private-error", result.stdout + result.stderr)
        self.assertNotIn("private-token", result.stdout + result.stderr)
        return result.stdout

    def test_real_worker_state_and_pause_lifecycle(self):
        self.assertIn("PASS 15 session watcher groups", self.run_fixture("normal"))

    def test_cancelled_auth_finishes_task_before_terminal_exit(self):
        self.assertEqual(self.run_fixture("quit"), "TASK FINISHED\n")

    def test_unexpected_auth_failure_cannot_resume_unauthenticated(self):
        self.assertEqual(self.run_fixture("auth-error"), "TASK FINISHED\n")


if __name__ == "__main__":
    unittest.main()
