"""Owned Alliance build/receipt safety using real ELF parsing and mocked tools."""
from contextlib import ExitStack, redirect_stdout
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


E = load('alliance_builder_tests', REPO / 'tools/build_android_alliance.py')
ELF = load('alliance_elf_fixtures', REPO / 'tests/test_android_engine_artifacts.py')
PWSH = os.environ.get('WESNOTH_TEST_PWSH') or shutil.which('pwsh')
if not PWSH:
    candidate = REPO / 'build/host-tools/powershell-7.4.6/pwsh'
    if candidate.is_file():
        PWSH = str(candidate)


def write(root, relative, content):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob('*') if path.is_file() and not path.is_symlink()}


class AllianceBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='alliance build with spaces ')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.root = self.repo / E.STAGE
        self.runtime = bytes(ELF.elf(api=21, needed=('libc.so',)))
        self.probe = bytes(ELF.elf(executable=True, needed=('libc.so', 'libc++_shared.so', 'libdl.so')))
        self.prior = write(self.repo, 'build/android-network/prefix/lib/libc++_shared.so', self.runtime)
        write(self.repo, 'build/android-boost/prefix/lib/libc++_shared.so', self.runtime)
        self.private = write(self.repo, '.env', b'private preserved sentinel')
        self.cache = write(self.repo, 'packaging/android/download/keep.tar', b'preserved source archive')
        self.profile = {'wesnoth_version': '1.19.26', 'abi': 'x86_64', 'min_sdk': 23,
                        'ndk_version': '26.3.11579264'}
        self.project = {'recipe': {'bytes': 7, 'sha256': 'fixture'}}
        self.prerequisites = {'runtime': E.B.fingerprint(self.prior)}
        self.inputs = {'project_files': self.project, 'prerequisites': self.prerequisites}
        self.binding = {'inputs': self.inputs, 'tools': {'cmake': '4.3.0'}}
        self.args = SimpleNamespace(ndk=self.repo / 'fixture ndk', cmake=self.repo / 'cmake',
                                    ninja=self.repo / 'ninja', jobs=2)
        self.command = ['fixture-cmake', '-DCMAKE_SUPPRESS_REGENERATION=ON']

    def tearDown(self):
        self.assertEqual(self.prior.read_bytes(), self.runtime)
        self.assertEqual(self.private.read_bytes(), b'private preserved sentinel')
        self.assertEqual(self.cache.read_bytes(), b'preserved source archive')

    def selected(self):
        return copy.deepcopy(self.profile), {'project_files': copy.deepcopy(self.project)}

    def stage(self):
        E.prepare_stage(self.root, self.inputs, self.profile)

    def outputs(self):
        write(self.root, E.ARTIFACTS[0], self.probe)
        write(self.root, E.ARTIFACTS[1], self.runtime)

    def completed(self):
        self.stage()
        self.outputs()
        validation, files = E.validate_prefix(self.repo, self.root)
        saved = {'schema_version': 1, 'kind': E.KIND, 'profile': self.profile, 'inputs': self.inputs,
                 'artifacts': files, 'validation': validation, 'device_executed': False}
        write(self.root, 'receipt.json', E.B.json_bytes(saved))
        return saved

    def selections(self, stack):
        stack.enter_context(patch.object(E, 'selected_inputs', side_effect=lambda repo: self.selected()))
        stack.enter_context(patch.object(E, 'prior_inputs', return_value=copy.deepcopy(self.prerequisites)))

    def generated(self, command, env):
        for name in E.CONFIG_FILES:
            write(self.root / 'build', name, ('generated ' + name).encode())

    def configure(self):
        self.stage()
        with patch.object(E.M, 'run', side_effect=self.generated):
            E.configure_once(self.root, self.binding, self.command, {})

    def test_valid_actual_elf_and_receipt_verify_without_build_tools(self):
        self.completed()
        before = snapshot(self.repo)
        with ExitStack() as stack:
            self.selections(stack)
            stack.enter_context(patch.object(E.B, 'ndk_paths', side_effect=AssertionError('compiler requested')))
            stack.enter_context(patch.object(E.M, 'run', side_effect=AssertionError('tool requested')))
            with redirect_stdout(io.StringIO()) as output:
                saved = E.verify_receipt(self.repo)
        self.assertFalse(saved['device_executed'])
        self.assertIn('ANDROID ALLIANCE ARTIFACTS VERIFIED', output.getvalue())
        self.assertEqual(snapshot(self.repo), before)

    def test_probe_api_dependency_architecture_and_rpath_fail_closed(self):
        self.stage()
        self.outputs()
        bad = [
            ELF.elf(executable=True, api=24, needed=('libc.so', 'libc++_shared.so')),
            ELF.elf(executable=True, needed=('libc.so', 'libc++_shared.so', 'libSDL3.so')),
            ELF.elf(executable=True, needed=('libc.so',)),
            ELF.elf(executable=True, needed=('libc.so', 'libc++_shared.so'), extra_dynamic=((29, 0),)),
            b'not an ELF',
        ]
        for value in bad:
            with self.subTest(value=value[:10]):
                write(self.root, E.ARTIFACTS[0], value)
                with self.assertRaises(E.BuildError):
                    E.validate_prefix(self.repo, self.root)

    def test_tampered_artifact_receipt_inputs_and_execution_claim_rejected(self):
        original = self.completed()
        for changed in ('inputs', 'profile', 'artifacts', 'validation', 'device_executed'):
            with self.subTest(field=changed):
                saved = copy.deepcopy(original)
                saved[changed] = True if changed == 'device_executed' else {}
                write(self.root, 'receipt.json', E.B.json_bytes(saved))
                before = snapshot(self.repo)
                with self.assertRaises(E.BuildError):
                    E.checked_receipt(self.repo, self.root, self.inputs, self.profile)
                self.assertEqual(snapshot(self.repo), before)
        write(self.root, 'receipt.json', E.B.json_bytes(original))
        write(self.root, E.ARTIFACTS[0], self.probe + b'changed bytes')
        with self.assertRaises(E.BuildError):
            E.checked_receipt(self.repo, self.root, self.inputs, self.profile)

    def test_unknown_prefix_file_and_different_runtime_rejected(self):
        self.completed()
        extra = write(self.root, 'prefix/extra', b'unknown output')
        with self.assertRaises(E.BuildError):
            E.validate_prefix(self.repo, self.root)
        extra.unlink()
        write(self.root, E.ARTIFACTS[1], self.runtime + b'different runtime')
        with self.assertRaisesRegex(E.BuildError, 'runtime differs'):
            E.validate_prefix(self.repo, self.root)

    def test_completed_build_uses_no_compiler_or_installation(self):
        self.completed()
        before = snapshot(self.repo)
        with ExitStack() as stack:
            self.selections(stack)
            stack.enter_context(patch.object(E.B, 'ndk_paths', side_effect=AssertionError('compiler requested')))
            stack.enter_context(patch.object(E.M, 'run', side_effect=AssertionError('tool requested')))
            stack.enter_context(patch.object(E, 'install_files', side_effect=AssertionError('rewrite attempted')))
            E.build(self.repo, self.args)
        self.assertEqual(snapshot(self.repo), before)

    def legacy_completed(self, legacy=None):
        self.project[E.HELPER_INPUT] = copy.deepcopy(legacy or E.LEGACY_HELPER_INPUT)
        saved = copy.deepcopy(self.completed())
        self.project[E.HELPER_INPUT] = E.B.fingerprint(Path(E.__file__))
        return saved

    def test_python312_verifies_and_reuses_legacy_completed_stage_without_rebinding(self):
        original = self.legacy_completed()
        before = snapshot(self.repo)
        with ExitStack() as stack:
            self.selections(stack)
            stack.enter_context(patch.object(E.B, 'ndk_paths', side_effect=AssertionError('compiler requested')))
            stack.enter_context(patch.object(E.M, 'run', side_effect=AssertionError('tool requested')))
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(E.verify_receipt(self.repo), original)
                E.build(self.repo, self.args)
        self.assertIn('retained historical launcher receipt', output.getvalue())
        self.assertEqual(snapshot(self.repo), before)

    def test_legacy_helper_exception_does_not_accept_other_changes(self):
        self.legacy_completed()
        current = copy.deepcopy(self.inputs)
        for changed in (
                dict(current, prerequisites={'changed': True}),
                dict(current, project_files=dict(current['project_files'], recipe={'changed': True})),
                dict(current, project_files=dict(current['project_files'], **{E.HELPER_INPUT: {'bytes': 1, 'sha256': 'unreviewed'}}))):
            with self.subTest(current=changed):
                with self.assertRaises(E.BuildError):
                    E.checked_receipt(self.repo, self.root, changed, self.profile)
        saved = E.record(self.root / 'receipt.json')
        saved['inputs']['project_files'][E.HELPER_INPUT]['sha256'] = '0' * 64
        write(self.root, 'receipt.json', E.B.json_bytes(saved))
        with self.assertRaises(E.BuildError):
            E.checked_receipt(self.repo, self.root, current, self.profile)

    def test_known_windows_crlf_legacy_helper_is_retained_without_rewriting(self):
        original = self.legacy_completed(E.LEGACY_HELPER_INPUT_CRLF)
        before = snapshot(self.repo)
        self.assertEqual(E.checked_receipt(self.repo, self.root, self.inputs, self.profile), original)
        self.assertEqual(snapshot(self.repo), before)

    def test_legacy_receipt_marker_and_output_checks_still_apply(self):
        self.legacy_completed()
        original_marker = (self.root / E.MARKER).read_bytes()
        write(self.root, E.MARKER, E.B.json_bytes(
            {'schema_version': 1, 'kind': E.KIND, 'profile': self.profile, 'inputs': self.inputs}))
        with self.assertRaisesRegex(E.BuildError, 'ownership differs'):
            E.checked_receipt(self.repo, self.root, self.inputs, self.profile)
        write(self.root, E.MARKER, original_marker)
        write(self.root, E.ARTIFACTS[0], self.probe + b'modified')
        with self.assertRaises(E.BuildError):
            E.checked_receipt(self.repo, self.root, self.inputs, self.profile)

    def test_incomplete_legacy_stage_is_not_resumed_under_another_python_binding(self):
        self.project[E.HELPER_INPUT] = copy.deepcopy(E.LEGACY_HELPER_INPUT)
        self.stage()
        self.project[E.HELPER_INPUT] = E.B.fingerprint(Path(E.__file__))
        before = snapshot(self.repo)
        with ExitStack() as stack:
            self.selections(stack)
            stack.enter_context(patch.object(E.B, 'ndk_paths', side_effect=AssertionError('compiler requested')))
            with self.assertRaisesRegex(E.BuildError, 'different inputs'):
                E.build(self.repo, self.args)
        self.assertEqual(snapshot(self.repo), before)

    def test_native_windows_runtime_requires_exact_cpython31210_x64(self):
        with patch.object(E.sys, 'platform', 'win32'), \
                patch.object(E.sys, 'version_info', (3, 12, 10)), \
                patch.object(E.platform, 'python_implementation', return_value='CPython'), \
                patch.object(E.platform, 'machine', return_value='AMD64'), \
                patch.object(E.struct, 'calcsize', return_value=8):
            E.check_windows_python()
            for version in ((3, 12, 3), (3, 14, 3), (3, 11, 10)):
                with self.subTest(version=version), patch.object(E.sys, 'version_info', version):
                    with self.assertRaisesRegex(E.BuildError, '3.12.10 x64'):
                        E.check_windows_python()
            for owner, name, value in ((E.platform, 'machine', 'ARM64'),
                                       (E.platform, 'python_implementation', 'PyPy'),
                                       (E.struct, 'calcsize', 4)):
                with self.subTest(field=name), patch.object(owner, name, return_value=value):
                    with self.assertRaises(E.BuildError):
                        E.check_windows_python()

    def test_owner_input_mismatch_unknown_stage_and_nested_link_preserved(self):
        self.root.mkdir()
        write(self.root, 'user-file', b'preserve')
        before = snapshot(self.repo)
        with self.assertRaises(E.BuildError):
            self.stage()
        self.assertEqual(snapshot(self.repo), before)
        write(self.root, E.MARKER, E.B.json_bytes(
            {'schema_version': 1, 'kind': E.KIND, 'profile': self.profile, 'inputs': self.inputs}))
        with self.assertRaisesRegex(E.BuildError, 'different inputs'):
            E.prepare_stage(self.root, {'changed': True}, self.profile)
        (self.root / 'linked').symlink_to(self.private)
        with self.assertRaises(E.BuildError):
            self.stage()
        self.assertTrue((self.root / 'linked').is_symlink())

    def test_successful_configuration_resumes_without_touching_ninja_history(self):
        self.configure()
        for name in ('.ninja_log', '.ninja_deps', 'object.o', 'object.o.d'):
            write(self.root / 'build', name, ('keep ' + name).encode())
        before = snapshot(self.repo)
        with patch.object(E.M, 'run', side_effect=AssertionError('reconfigure attempted')):
            E.configure_once(self.root, self.binding, self.command, {})
        self.assertEqual(snapshot(self.repo), before)

    def test_configure_failure_and_partial_files_never_reconfigured(self):
        self.stage()
        def fail(command, env):
            write(self.root / 'build', 'CMakeCache.txt', b'partial')
            raise E.BuildError('injected configure failure')
        with patch.object(E.M, 'run', side_effect=fail):
            with self.assertRaisesRegex(E.BuildError, 'injected'):
                E.configure_once(self.root, self.binding, self.command, {})
        before = snapshot(self.repo)
        with patch.object(E.M, 'run', side_effect=AssertionError('reconfigure attempted')):
            with self.assertRaisesRegex(E.BuildError, 'incomplete'):
                E.configure_once(self.root, self.binding, self.command, {})
        self.assertEqual(snapshot(self.repo), before)

    def test_changed_configuration_or_tool_binding_is_preserved_and_rejected(self):
        self.configure()
        for name in E.CONFIG_FILES:
            path = self.root / 'build' / name
            original = path.read_bytes()
            path.write_bytes(b'changed config')
            before = snapshot(self.repo)
            with self.assertRaises(E.BuildError):
                E.configure_once(self.root, self.binding, self.command, {})
            self.assertEqual(snapshot(self.repo), before)
            path.write_bytes(original)
        with self.assertRaises(E.BuildError):
            E.configure_once(self.root, {'changed tools': True}, self.command, {})

    def test_lock_conflict_and_lock_tampering_do_not_delete_existing_record(self):
        path = self.repo / 'build' / E.LOCK
        with E.operation(self.repo):
            with self.assertRaisesRegex(E.BuildError, 'interrupted Alliance build'):
                with E.operation(self.repo):
                    self.fail('unexpected concurrent operation')
            with self.assertRaisesRegex(E.BuildError, 'cannot verify concurrently'):
                E.verify_receipt(self.repo)
        self.assertFalse(path.exists())
        with self.assertRaises(E.BuildError):
            with E.operation(self.repo):
                path.write_bytes(b'changed owner')
        self.assertEqual(path.read_bytes(), b'changed owner')

    def test_install_is_exclusive_and_preserves_changed_partial_prefix(self):
        self.stage()
        write(self.root, 'build/android-alliance-check', self.probe)
        E.install_files(self.repo, self.root)
        before = snapshot(self.repo)
        E.install_files(self.repo, self.root)
        self.assertEqual(snapshot(self.repo), before)
        write(self.root, E.ARTIFACTS[0], b'changed partial output')
        before = snapshot(self.repo)
        with self.assertRaisesRegex(E.BuildError, 'artifact differs'):
            E.install_files(self.repo, self.root)
        self.assertEqual(snapshot(self.repo), before)

    def test_first_build_uses_only_selected_target_and_completed_dependencies(self):
        ndk = {'runtime': self.prior, 'cmake_toolchain': self.repo / 'fixture.toolchain'}
        commands = []
        def native(command, env):
            commands.append(command)
            if '--build' in command:
                self.assertIn('android-alliance-check', command)
                self.assertEqual(command[-3:], ['--', '-d', 'keepdepfile'])
                write(self.root, 'build/android-alliance-check', self.probe)
            else:
                self.assertIn('-DWESNOTH_ALLIANCE_ANDROID_CHECK=ON', command)
                self.assertIn('-DCMAKE_SUPPRESS_REGENERATION=ON', command)
                self.assertNotIn('CC', env)
                self.generated(command, env)
        with ExitStack() as stack:
            self.selections(stack)
            stack.enter_context(patch.object(E.B, 'ndk_paths', return_value=ndk))
            stack.enter_context(patch.object(E.B, 'check_tools', return_value={'cmake': '4.3.0'}))
            stack.enter_context(patch.object(E.M, 'run', side_effect=native))
            E.build(self.repo, self.args)
            E.verify_receipt(self.repo)
        self.assertEqual(len(commands), 2)

    def test_mid_build_project_change_never_writes_receipt(self):
        ndk = {'runtime': self.prior, 'cmake_toolchain': self.repo / 'fixture.toolchain'}
        def native(command, env):
            if '--build' in command:
                write(self.root, 'build/android-alliance-check', self.probe)
                self.project['changed'] = True
            else:
                self.generated(command, env)
        with ExitStack() as stack:
            self.selections(stack)
            stack.enter_context(patch.object(E.B, 'ndk_paths', return_value=ndk))
            stack.enter_context(patch.object(E.B, 'check_tools', return_value={}))
            stack.enter_context(patch.object(E.M, 'run', side_effect=native))
            with self.assertRaisesRegex(E.BuildError, 'inputs changed during compilation'):
                E.build(self.repo, self.args)
        self.assertFalse((self.root / 'receipt.json').exists())
        self.assertTrue((self.root / 'build/android-alliance-check').exists())

    def test_prerequisites_are_verified_and_runtime_disagreement_blocks(self):
        for stage in ('boost', 'network'):
            write(self.repo, 'build/android-' + stage + '/receipt.json', b'fixture receipt')
        with patch.object(E.O, 'verify_receipt', return_value={'prefix_files': {'boost': True}}) as boost:
            with patch.object(E.N, 'verify_receipt', return_value={'prefix_files': {'network': True}}) as network:
                result = E.prior_inputs(self.repo)
                self.assertEqual(result['runtime'], E.B.fingerprint(self.prior))
                self.assertEqual(boost.call_count, 1)
                self.assertEqual(network.call_count, 1)
                write(self.repo, 'build/android-boost/prefix/lib/libc++_shared.so', b'different')
                with self.assertRaisesRegex(E.BuildError, 'identical'):
                    E.prior_inputs(self.repo)

    def test_selected_inputs_bind_all_native_sources_and_helpers_but_not_ui_wrappers(self):
        self.assertIn('alliance_native/transport.cpp', E.PROJECT_INPUTS)
        self.assertIn('alliance_native/transport.hpp', E.PROJECT_INPUTS)
        self.assertIn('tools/native-alliance/android_check.cpp', E.PROJECT_INPUTS)
        self.assertIn('tools/native-alliance/AndroidCheck.cmake', E.PROJECT_INPUTS)
        self.assertNotIn('tools/Test-AndroidAlliance.ps1', E.PROJECT_INPUTS)
        for name in E.PROJECT_INPUTS:
            write(self.repo, name, name.encode())
        with patch.object(E.B, 'profile_and_inputs', return_value=(self.profile, {})):
            _, before = E.selected_inputs(self.repo)
            write(self.repo, 'alliance_native/transport.cpp', b'changed transport')
            _, after = E.selected_inputs(self.repo)
        self.assertNotEqual(before, after)


FAKE_BUILDER = r'''#!/usr/bin/python3
import json, os, pathlib, sys
cfg = json.loads(pathlib.Path(os.environ['ALLIANCE_BUILD_FIXTURE']).read_text())
pathlib.Path(cfg['calls']).write_text(json.dumps(sys.argv[1:]))
print('VISIBLE NATIVE OUTPUT', flush=True)
print(cfg.get('stderr', 'VISIBLE NATIVE STDERR'), file=sys.stderr, flush=True)
if cfg.get('receipt', True):
    path = pathlib.Path(cfg['receipt_path'])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"fixture":"receipt"}')
print(cfg.get('stdout', 'ANDROID ALLIANCE BUILD PASS'), flush=True)
raise SystemExit(cfg.get('exit', 0))
'''


@unittest.skipUnless(PWSH, 'PowerShell required for native-process simulations')
class AllianceBuildWrapperTests(unittest.TestCase):
    """Actual PowerShell execution; Windows native tools are Linux fixtures."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='alliance wrapper ')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "client & literal $ ' with spaces"
        (self.repo / 'tools').mkdir(parents=True)
        self.helper = self.repo / 'tools/Build-WindowsAndroidAlliance.ps1'
        shutil.copyfile(REPO / 'tools/Build-WindowsAndroidAlliance.ps1', self.helper)
        self.builder = write(self.repo, 'tools/build_android_alliance.py', b'fixture builder')
        self.python = write(self.base, 'native Python/python.exe', FAKE_BUILDER.encode())
        self.python.chmod(0o755)
        self.cmake = write(self.base, 'CMake with spaces/cmake.exe', b'unused executable')
        self.ninja = write(self.base, 'MSYS relocated/ucrt64/bin/ninja.exe', b'unused executable')
        self.sdk = self.base / 'Android SDK'
        self.ndk = self.sdk / 'ndk/26.3.11579264'
        self.ndk.mkdir(parents=True)
        self.keep = write(self.repo, 'build/android-game/keep', b'completed game unchanged')
        write(self.repo, '.env', b'PRIVATE_SENTINEL_DO_NOT_PRINT')
        self.config_path = self.base / 'config.json'
        self.config = {'calls': str(self.base / 'calls.json'),
                       'receipt_path': str(self.repo / 'build/android-alliance/receipt.json')}

    def invoke(self):
        self.config_path.write_text(json.dumps(self.config))
        env = dict(os.environ, OS='Windows_NT', LOCALAPPDATA=str(self.base),
                   ALLIANCE_BUILD_FIXTURE=str(self.config_path))
        result = subprocess.run([PWSH, '-NoLogo', '-NoProfile', '-File', str(self.helper),
            '-SdkRoot', str(self.sdk), '-PythonPath', str(self.python), '-CMakePath', str(self.cmake),
            '-NinjaPath', str(self.ninja), '-Jobs', '3'], cwd=self.base,
            env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
        self.output = result.stdout + result.stderr
        logs = list((self.repo / 'diagnostics').glob('android-alliance-build-*.txt'))
        self.assertEqual(len(logs), 1)
        self.log = logs[0].read_text()
        self.assertIn('Saved log:', self.output)
        self.assertNotIn('PRIVATE_SENTINEL_DO_NOT_PRINT', self.output + self.log)
        self.assertEqual(self.keep.read_bytes(), b'completed game unchanged')
        return result.returncode

    def test_success_forwards_literal_paths_and_saves_both_native_streams(self):
        self.assertEqual(self.invoke(), 0, self.output)
        self.assertEqual(json.loads(Path(self.config['calls']).read_text()),
            ['-I', '-B', '-u', str(self.builder), '--ndk', str(self.ndk),
             '--cmake', str(self.cmake), '--ninja', str(self.ninja), '--jobs', '3'])
        for expected in ('VISIBLE NATIVE OUTPUT', 'VISIBLE NATIVE STDERR', 'ANDROID ALLIANCE BUILD COMPLETE'):
            self.assertIn(expected, self.log)

    def test_nonzero_exit_rejects_success_marker_and_saves_failure(self):
        self.config['exit'] = 7
        self.assertEqual(self.invoke(), 2, self.output)
        self.assertIn('failed with exit 7', self.log)
        self.assertNotIn('ANDROID ALLIANCE BUILD COMPLETE', self.output)

    def test_missing_marker_is_failure_even_with_receipt_and_zero_exit(self):
        self.config['stdout'] = 'no completion marker'
        self.assertEqual(self.invoke(), 2, self.output)
        self.assertIn('without its required completion marker', self.log)

    def test_stderr_completion_marker_does_not_prove_success(self):
        self.config.update(stdout='no stdout completion', stderr='ANDROID ALLIANCE BUILD PASS')
        self.assertEqual(self.invoke(), 2, self.output)
        self.assertIn('without its required completion marker', self.log)

    def test_missing_receipt_is_failure_even_with_success_marker(self):
        self.config['receipt'] = False
        self.assertEqual(self.invoke(), 2, self.output)
        self.assertIn('without its artifact receipt', self.log)

    def test_missing_tool_is_logged_without_running_builder(self):
        self.ninja.unlink()
        self.assertEqual(self.invoke(), 2, self.output)
        self.assertIn('Required build tool is missing or empty', self.log)
        self.assertFalse(Path(self.config['calls']).exists())


if __name__ == '__main__':
    unittest.main()
