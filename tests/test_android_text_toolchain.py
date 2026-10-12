"""Exercise shell failure handling with real Make/pkg-config and a fake NDK.

The separate network-disabled NDK run validates actual compilation. These
fixtures deliberately control missing tools, compiler failures and malformed
outputs without changing an installed toolchain or a completed build stage.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "tools/check_android_text_toolchain.sh"


class TextToolchainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real_make = shutil.which("make")
        cls.real_pkgconfig = shutil.which("pkg-config")
        if not cls.real_make or not cls.real_pkgconfig:
            raise unittest.SkipTest("Fixtures require GNU Make and pkg-config")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="wesnoth-text-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.scratch = self.root / "scratch with spaces"
        self.scratch.mkdir()
        self.ndk = self.root / "NDK with spaces"
        self.ndk.mkdir()
        (self.ndk / "source.properties").write_text("Pkg.Revision = 26.3.11579264\n")
        self.ndk_bin = self.ndk / "toolchains/llvm/prebuilt/linux-x86_64/bin"
        self.ndk_bin.mkdir(parents=True)
        # Restrict PATH to the fixture. An omitted tool is genuinely missing,
        # even when the surrounding verification container has it installed.
        for name in ("bash", "uname", "timeout", "mkdir", "cat"):
            (self.bin / name).symlink_to(shutil.which(name))
        (self.bin / "make").symlink_to(self.real_make)
        (self.bin / "pkg-config").symlink_to(self.real_pkgconfig)
        for name in ("ninja", "gperf", "patch", "perl"):
            self.executable(self.bin / name, "#!/bin/bash\nprintf 'fixture 1.0\\n'\n")

        module = self.root / "modules/mesonbuild"
        module.mkdir(parents=True)
        (module / "__init__.py").write_text("")
        (module / "coredata.py").write_text("version = '1.12.0'\n")
        self.python = self.bin / "python3"
        self.dispatch = self.root / "python_dispatch.py"
        self.dispatch.write_text(textwrap.dedent(f"""
            import runpy, sys
            sys.path.insert(0, {str(module.parent)!r})
            invoked_python = sys.argv.pop(1)
            sys.executable = invoked_python
            args = sys.argv[1:]
            while args and args[0] in ('-I', '-B'):
                args.pop(0)
            if args == ['--version']:
                print('Python fixture')
            elif args[0] == '-c':
                sys.argv = ['-c'] + args[2:]
                exec(compile(args[1], '<binding probe>', 'exec'))
            else:
                sys.argv = args
                runpy.run_path(args[0], run_name='__main__')
        """))
        self.executable(self.python, f'#!/bin/bash\nexec "{sys.executable}" -I -B "{self.dispatch}" "$0" "$@"\n')
        self.executable(self.bin / "meson", f"#!{self.python}\nfrom mesonbuild.coredata import version\nprint(version)\n")

        self.call_log = self.root / "compiler.json"
        clang_code = f"""
            import json, os, pathlib, struct, sys
            args = sys.argv[1:]
            if args == ['--version']:
                print('Android clang version 17.0.2')
                raise SystemExit(0)
            assert '--target=x86_64-linux-android23' in args, args
            include = [arg[2:] for arg in args if arg.startswith('-I')]
            assert len(include) == 1, args
            assert 'dependency with spaces' in include[0], args
            header = pathlib.Path(include[0]) / 'wesnoth_text_bridge.h'
            assert header.read_text() == '#define WESNOTH_TEXT_BRIDGE_VALUE 73\\n'
            source = pathlib.Path(args[args.index('-c') + 1])
            assert '__ANDROID_API__ != 23' in source.read_text()
            assert not os.environ.get('CPATH')
            assert not os.environ.get('PKG_CONFIG_PATH')
            pathlib.Path({str(self.call_log)!r}).write_text(json.dumps(args))
            elf = bytearray(64)
            elf[:6] = b'\\x7fELF\\x02\\x01'
            struct.pack_into('<HH', elf, 16, 1, 62)
            pathlib.Path(args[args.index('-o') + 1]).write_bytes(elf)
        """
        self.python_executable(self.ndk_bin / "clang", clang_code)
        self.python_executable(self.ndk_bin / "llvm-ar", """
            import pathlib, sys
            if sys.argv[1] == 'rcs':
                pathlib.Path(sys.argv[2]).write_bytes(b'!<arch>\\n' + pathlib.Path(sys.argv[3]).read_bytes())
            elif sys.argv[1] == 't':
                assert pathlib.Path(sys.argv[2]).read_bytes().startswith(b'!<arch>\\n')
                print('probe.o')
            else:
                raise SystemExit(9)
        """)
        self.python_executable(self.ndk_bin / "llvm-readelf", """
            import pathlib, struct, sys
            blob = pathlib.Path(sys.argv[2]).read_bytes()
            assert blob[:8] == b'!<arch>\\n'
            assert blob[8:14] == b'\\x7fELF\\x02\\x01'
            assert struct.unpack_from('<HH', blob, 24) == (1, 62)
            print('File: probe.a(probe.o)\\nELF Header:\\n  Class: ELF64\\n  Type: REL (Relocatable file)\\n  Machine: Advanced Micro Devices X86-64')
        """)

    def executable(self, path, content):
        path.write_text(content)
        path.chmod(0o755)

    def python_executable(self, path, content):
        self.executable(path, f"#!{sys.executable}\n" + textwrap.dedent(content))

    def run_check(self, **extra_env):
        env = os.environ.copy()
        env.update(PATH=str(self.bin), PYTHONPATH="/untrusted/python", PYTHONHOME="/untrusted/python")
        env.update(extra_env)
        return subprocess.run(
            ["/usr/bin/bash", "--noprofile", "--norc", str(SCRIPT), str(self.ndk), str(self.scratch)],
            env=env, capture_output=True, text=True, timeout=40,
        )

    def assert_blocked(self, result, message):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(message, result.stdout)
        self.assertNotIn("ANDROID TEXT TOOLCHAIN CHECK PASSED", result.stdout)

    def test_spaces_and_isolated_pkgconfig_compile_successfully(self):
        result = self.run_check(PKG_CONFIG_PATH="/host/pkgconfig", PKG_CONFIG_LIBDIR="/host/pkgconfig", CPATH="/host/include")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ANDROID TEXT TOOLCHAIN CHECK PASSED", result.stdout)
        self.assertIn("Meson launcher/interpreter binding verified", result.stdout)
        self.assertIn("[OPTIONAL] autoconf is missing", result.stdout)
        self.assertIn("probe.o", (self.scratch / "ndk bridge/Makefile").read_text())
        self.assertEqual(len([arg for arg in json.loads(self.call_log.read_text()) if arg.startswith("-I")]), 1)
        self.assertLess(len(result.stdout.splitlines()), 100)

    def test_reports_every_missing_core_tool(self):
        for name in ("gperf", "ninja", "perl"):
            (self.bin / name).unlink()
        result = self.run_check()
        for name in ("gperf", "ninja", "perl"):
            self.assert_blocked(result, "Missing core tool: " + name)
        self.assertFalse((self.scratch / "ndk bridge").exists())

    def test_optional_tool_failure_is_nonfatal(self):
        self.executable(self.bin / "autoconf", "#!/bin/bash\necho optional-generator-broken\nexit 7\n")
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("[OPTIONAL] autoconf probe failed (exit 7)", result.stdout)

    def test_core_version_failure_is_fatal(self):
        self.executable(self.bin / "gperf", "#!/bin/bash\necho generator-broken\nexit 7\n")
        self.assert_blocked(self.run_check(), "gperf probe failed (exit 7)")

    def test_empty_core_version_is_fatal(self):
        self.executable(self.bin / "gperf", "#!/bin/bash\nexit 0\n")
        self.assert_blocked(self.run_check(), "tool returned no version output")

    def test_meson_interpreter_mismatch_blocks_build(self):
        self.executable(self.bin / "meson", "#!/bin/bash\nprintf '1.12.0\\n'\n")
        self.assert_blocked(self.run_check(), "Meson/Python binding failed")
        self.assertFalse(self.call_log.exists())

    def test_meson_module_version_mismatch_blocks_build(self):
        self.executable(self.bin / "meson", f"#!{self.python}\nprint('0.1.0')\n")
        self.assert_blocked(self.run_check(), "different versions")

    def assert_binding_details(self, result, interpreter):
        self.assertIn("Selected Python=" + str(self.python), result.stdout)
        self.assertIn("Meson launcher=" + str(self.bin / "meson"), result.stdout)
        self.assertIn("Meson shebang interpreter=" + str(interpreter), result.stdout)
        self.assertIn("platform=", result.stdout)
        self.assertIn("Python version=", result.stdout)
        self.assertIn("Meson module=", result.stdout)
        self.assertIn("version=1.12.0", result.stdout)

    def test_hardlinked_interpreter_alias_with_different_realpath_is_accepted(self):
        alias = self.bin / "python3.12.exe"
        os.link(self.python, alias)
        self.assertNotEqual(os.path.realpath(alias), os.path.realpath(self.python))
        self.assertTrue(os.path.samefile(alias, self.python))
        self.executable(self.bin / "meson", f"#!{alias}\nfrom mesonbuild.coredata import version\nprint(version)\n")
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_binding_details(result, alias)
        self.assertIn("Meson launcher/interpreter binding verified", result.stdout)
        self.assertIn("ANDROID TEXT TOOLCHAIN CHECK PASSED", result.stdout)
        self.assertTrue(self.call_log.exists())

    def test_copied_identical_interpreter_alias_in_same_directory_is_accepted(self):
        alias = self.bin / "python3.12.exe"
        shutil.copy2(self.python, alias)
        self.assertFalse(os.path.samefile(alias, self.python))
        self.assertEqual(alias.read_bytes(), self.python.read_bytes())
        self.executable(self.bin / "meson", f"#!{alias}\nfrom mesonbuild.coredata import version\nprint(version)\n")
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_binding_details(result, alias)
        self.assertIn("ANDROID TEXT TOOLCHAIN CHECK PASSED", result.stdout)
        self.assertTrue(self.call_log.exists())

    def test_distinct_interpreter_bytes_are_rejected_with_binding_details(self):
        alias = self.bin / "different-python.exe"
        self.executable(alias, self.python.read_text() + "\n# A different executable file.\n")
        self.assertNotEqual(alias.read_bytes(), self.python.read_bytes())
        self.executable(self.bin / "meson", f"#!{alias}\nfrom mesonbuild.coredata import version\nprint(version)\n")
        result = self.run_check()
        self.assert_blocked(result, "different executable files")
        self.assert_binding_details(result, alias)
        self.assertFalse(self.call_log.exists())
        self.assertFalse((self.scratch / "ndk bridge").exists())

    def test_identical_interpreter_copy_with_different_runtime_prefix_is_rejected(self):
        alias = self.bin / "python3.12.exe"
        shutil.copy2(self.python, alias)
        # Keep both executable files byte-identical; alter only the controlled
        # dispatcher's runtime identity for the alias's child identity query.
        self.dispatch.write_text(self.dispatch.read_text().replace(
            "args = sys.argv[1:]",
            f"if invoked_python == {str(alias)!r} and '-c' in sys.argv:\n"
            f"    sys.prefix = {str(self.root / 'different-runtime-prefix')!r}\n"
            "args = sys.argv[1:]"))
        self.assertEqual(alias.read_bytes(), self.python.read_bytes())
        self.executable(self.bin / "meson", f"#!{alias}\nfrom mesonbuild.coredata import version\nprint(version)\n")
        result = self.run_check()
        self.assert_blocked(result, "Meson interpreter runtime/module identity differs:")
        self.assertIn("prefix", result.stdout)
        self.assert_binding_details(result, alias)
        self.assertFalse(self.call_log.exists())

    def test_identical_interpreter_copy_outside_selected_directory_is_rejected(self):
        alias = self.root / "different-installation/python3.12.exe"
        alias.parent.mkdir()
        shutil.copy2(self.python, alias)
        self.assertEqual(alias.read_bytes(), self.python.read_bytes())
        self.executable(self.bin / "meson", f"#!{alias}\nfrom mesonbuild.coredata import version\nprint(version)\n")
        result = self.run_check()
        self.assert_blocked(result, "different executable files")
        self.assert_binding_details(result, alias)
        self.assertFalse(self.call_log.exists())

    def test_interpreter_stat_error_reports_paths_and_prevents_compilation(self):
        alias = self.bin / "disappearing-python.exe"
        os.link(self.python, alias)
        # The launcher version query succeeds, then its alias disappears before
        # the binding probe. The selected Python executable remains intact.
        self.executable(self.bin / "meson", f"#!{alias}\nimport os\n"
                        f"os.unlink({str(alias)!r})\nprint('1.12.0')\n")
        result = self.run_check()
        self.assert_blocked(result, "Could not verify Meson/Python interpreter file identity")
        self.assert_binding_details(result, alias)
        self.assertIn("No such file or directory", result.stdout)
        self.assertTrue(self.python.is_file())
        self.assertFalse(alias.exists())
        self.assertFalse(self.call_log.exists())

    def test_wrong_ndk_revision_blocks_build(self):
        (self.ndk / "source.properties").write_text("Pkg.Revision = 28.0.0\n")
        self.assert_blocked(self.run_check(), "Expected NDK revision 26.3.11579264")

    def test_duplicate_ndk_revision_blocks_build(self):
        (self.ndk / "source.properties").write_text("Pkg.Revision = 26.3.11579264\n" * 2)
        self.assert_blocked(self.run_check(), "Expected NDK revision 26.3.11579264")

    def test_native_compile_failure_propagates(self):
        self.executable(self.ndk_bin / "clang", "#!/bin/bash\nif [[ $1 == --version ]]; then echo clang; else echo cannot-compile; exit 12; fi\n")
        self.assert_blocked(self.run_check(), "Make/native NDK compiler boundary failed")

    def test_empty_successful_make_is_rejected(self):
        (self.bin / "make").unlink()
        self.executable(self.bin / "make", "#!/bin/bash\necho fixture-make\n")
        self.assert_blocked(self.run_check(), "Make returned success without both native output files")

    def test_wrong_elf_architecture_is_rejected(self):
        self.executable(self.ndk_bin / "llvm-readelf", "#!/bin/bash\nprintf 'Class: ELF64\\nType: REL\\nMachine: AArch64\\n'\n")
        self.assert_blocked(self.run_check(), "Unexpected compiler-boundary ELF identity")

    def test_archive_membership_is_verified(self):
        self.executable(self.ndk_bin / "llvm-ar", "#!/bin/bash\nif [[ $1 == t ]]; then echo wrong.o; else printf archive >\"$2\"; fi\n")
        self.assert_blocked(self.run_check(), "NDK archive does not contain exactly probe.o")

    def test_native_crlf_versions_and_archive_listing_are_accepted(self):
        clang = self.ndk_bin / "clang"
        clang.write_text(clang.read_text().replace("print('Android clang version 17.0.2')", "print('Android clang version 17.0.2', end='\\r\\n')"))
        archiver = self.ndk_bin / "llvm-ar"
        archiver.write_text(archiver.read_text().replace("print('probe.o')", "print('probe.o', end='\\r\\n')"))
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ANDROID TEXT TOOLCHAIN CHECK PASSED", result.stdout)

    def test_scratch_write_failure_is_reported(self):
        (self.bin / "cat").unlink()
        self.executable(self.bin / "cat", "#!/bin/bash\nexit 71\n")
        self.assert_blocked(self.run_check(), "Could not write the owned probe header")
        self.assertFalse(self.call_log.exists())

    def test_existing_scratch_contents_are_preserved(self):
        sentinel = self.scratch / "user-file.txt"
        sentinel.write_text("preserve this\n")
        self.assert_blocked(self.run_check(), "Scratch root contains existing files")
        self.assertEqual(sentinel.read_text(), "preserve this\n")
        self.assertEqual(list(self.scratch.iterdir()), [sentinel])

    def test_scratch_symlink_is_rejected(self):
        original = self.scratch
        self.scratch = self.root / "scratch link"
        self.scratch.symlink_to(original, target_is_directory=True)
        self.assert_blocked(self.run_check(), "Scratch root must be an existing empty directory")
        self.assertEqual(list(original.iterdir()), [])

    def test_make_path_metacharacters_are_rejected(self):
        self.scratch = self.root / "scratch $(touch injected)"
        self.scratch.mkdir()
        self.assert_blocked(self.run_check(), "unsupported Make/shell metacharacters")
        self.assertEqual(list(self.scratch.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
