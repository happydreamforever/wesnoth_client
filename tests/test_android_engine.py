"""Owned engine-stage configuration and retry safety; no native tools run.

Real ELF validation is exercised separately by test_android_engine_artifacts.
These fixtures preserve earlier-stage sentinels and the failed current stage.
"""
from contextlib import ExitStack, redirect_stdout
import copy
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('android_engine_build', REPO / 'tools/build_android_engine.py')
E = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(E)
CONFIG_FILES = ('CMakeCache.txt', 'build.ninja', 'CMakeFiles/rules.ninja', 'config.h')


def write(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob('*') if path.is_file() and not path.is_symlink()}


class EngineStageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='engine owned stages ')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo with spaces'
        self.root = self.repo / 'build/android-engine'
        self.root.mkdir(parents=True)
        self.protected = write(self.repo, 'build/android-boost/prefix/lib/keep.a', b'prior completed library')
        self.cache = write(self.repo, 'packaging/android/download/keep.tar', b'unchanged cache')
        self.private = write(self.repo, '.env', b'private sentinel')
        self.profile = {'wesnoth_version': '1.19.26', 'abi': 'x86_64'}
        self.inputs = {'project_files': {'recipe': {'bytes': 7, 'sha256': 'fixture'}}, 'prerequisites': {}}
        self.binding = {'inputs': self.inputs, 'tools': {'cmake': '4.3.0'}, 'repository': str(self.repo)}
        self.command = ['fixture-cmake', '-S', 'fixture source', '-B', self.root / 'build', '-DCMAKE_SUPPRESS_REGENERATION=ON']
        self.env = {'FIXTURE': 'only'}

    def tearDown(self):
        self.assertEqual(self.protected.read_bytes(), b'prior completed library')
        self.assertEqual(self.cache.read_bytes(), b'unchanged cache')
        self.assertEqual(self.private.read_bytes(), b'private sentinel')

    def generated(self, command, env):
        self.assertEqual(command, self.command)
        self.assertEqual(env, self.env)
        for name in CONFIG_FILES:
            write(self.root / 'build', name, ('generated ' + name).encode())

    def configure(self):
        with patch.object(E.M, 'run', side_effect=self.generated) as run:
            E.configure_once(self.root, self.binding, self.command, self.env)
        self.assertEqual(run.call_count, 1)

    def completed(self, device_executed=False):
        # Build's completed-artifact branch has no compiler/configure/install work.
        marker = {'schema_version': 1, 'kind': E.KIND, 'profile': self.profile, 'inputs': self.inputs}
        write(self.root, E.MARKER, E.B.json_bytes(marker))
        receipt = dict(marker, artifacts={'fixture': 'verified'}, validation={'fixture': True},
                       tools={'cmake': '4.3.0'}, device_executed=device_executed)
        write(self.root, 'receipt.json', E.B.json_bytes(receipt))
        return receipt

    def build_patches(self, stack):
        stack.enter_context(patch.object(E, 'selected_inputs', return_value=(self.profile, copy.deepcopy(self.inputs), {})))
        stack.enter_context(patch.object(E, 'prior_inputs', return_value={}))
        stack.enter_context(patch.object(E.B, 'ndk_paths', return_value={}))
        stack.enter_context(patch.object(E.B, 'check_tools', return_value={'cmake': '4.3.0'}))
        stack.enter_context(patch.object(E, 'validate_prefix', return_value=({'fixture': True}, {'fixture': 'verified'})))
        stack.enter_context(patch.object(E.M, 'run', side_effect=AssertionError('Unexpected native command')))
        stack.enter_context(patch.object(E, 'install_files', side_effect=AssertionError('Unexpected installation')))
        return SimpleNamespace(ndk=Path('/fixture ndk'), cmake=Path('/fixture cmake'), ninja=Path('/fixture ninja'), jobs=2)

    def test_configure_once_then_resume_preserves_ninja_history_and_objects(self):
        self.configure()
        for name in ('.ninja_log', '.ninja_deps', 'partial.cpp.o', 'partial.cpp.o.d'):
            write(self.root / 'build', name, ('preserved ' + name).encode())
        before = snapshot(self.repo)
        with patch.object(E.M, 'run', side_effect=AssertionError('Reconfigured completed tree')):
            E.configure_once(self.root, self.binding, self.command, self.env)
        self.assertEqual(snapshot(self.repo), before)

    def test_changed_binding_stops_before_native_process_and_preserves_stage(self):
        self.configure()
        before = snapshot(self.repo)
        changed = copy.deepcopy(self.binding)
        changed['tools']['cmake'] = 'unreviewed tool'
        with patch.object(E.M, 'run', side_effect=AssertionError('Unexpected reconfiguration')):
            with self.assertRaisesRegex(E.BuildError, 'configuration or tools changed'):
                E.configure_once(self.root, changed, self.command, self.env)
        self.assertEqual(snapshot(self.repo), before)

    def test_each_changed_or_missing_generated_configuration_file_is_rejected(self):
        self.configure()
        for name in CONFIG_FILES:
            path = self.root / 'build' / name
            original = path.read_bytes()
            for altered in (b'edited generated file', None):
                with self.subTest(name=name, missing=altered is None):
                    if altered is None:
                        path.unlink()
                    else:
                        path.write_bytes(altered)
                    before = snapshot(self.repo)
                    with patch.object(E.M, 'run', side_effect=AssertionError('Unexpected reconfiguration')):
                        with self.assertRaises(E.BuildError):
                            E.configure_once(self.root, self.binding, self.command, self.env)
                    self.assertEqual(snapshot(self.repo), before)
                    path.write_bytes(original)

    def test_failed_configure_preserves_partial_outputs_and_blocks_retry(self):
        def fail(command, env):
            write(self.root / 'build', 'CMakeCache.txt', b'incomplete generated cache')
            raise E.BuildError('injected configure failure')
        with patch.object(E.M, 'run', side_effect=fail):
            with self.assertRaisesRegex(E.BuildError, 'injected configure failure'):
                E.configure_once(self.root, self.binding, self.command, self.env)
        self.assertTrue((self.root / 'configure-started.json').is_file())
        self.assertFalse((self.root / 'configured.json').exists())
        before = snapshot(self.repo)
        with patch.object(E.M, 'run', side_effect=AssertionError('Retried incomplete configuration')):
            with self.assertRaisesRegex(E.BuildError, 'configuration is incomplete'):
                E.configure_once(self.root, self.binding, self.command, self.env)
        self.assertEqual(snapshot(self.repo), before)

    def test_existing_build_without_configuration_is_not_adopted(self):
        write(self.root / 'build', 'unmanaged output', b'preserved user output')
        before = snapshot(self.repo)
        with patch.object(E.M, 'run', side_effect=AssertionError('Adopted unknown build')):
            with self.assertRaisesRegex(E.BuildError, 'configuration is incomplete'):
                E.configure_once(self.root, self.binding, self.command, self.env)
        self.assertEqual(snapshot(self.repo), before)

    def test_success_exit_without_all_configuration_files_does_not_record_success(self):
        with patch.object(E.M, 'run', return_value=''):
            with self.assertRaises(E.BuildError):
                E.configure_once(self.root, self.binding, self.command, self.env)
        self.assertFalse((self.root / 'configured.json').exists())
        self.assertTrue((self.root / 'configure-started.json').is_file())

    def test_stage_ownership_and_input_changes_fail_without_overwriting(self):
        write(self.root, 'unrelated user output', b'keep this')
        before = snapshot(self.repo)
        with self.assertRaises(E.BuildError):
            E.prepare_stage(self.root, self.inputs, self.profile)
        self.assertEqual(snapshot(self.repo), before)
        marker = {'schema_version': 1, 'kind': E.KIND, 'profile': self.profile, 'inputs': self.inputs}
        write(self.root, E.MARKER, E.B.json_bytes(marker))
        E.prepare_stage(self.root, self.inputs, self.profile)
        before = snapshot(self.repo)
        with self.assertRaisesRegex(E.BuildError, 'different inputs'):
            E.prepare_stage(self.root, {'changed': True}, self.profile)
        self.assertEqual(snapshot(self.repo), before)

    def test_stage_rejects_nested_symlink_and_exclusive_write_keeps_existing(self):
        self.completed()
        target = write(self.repo, 'unrelated', b'keep outside target')
        link = self.root / 'redirected'
        link.symlink_to(target)
        before = snapshot(self.repo)
        with self.assertRaises(E.BuildError):
            E.prepare_stage(self.root, self.inputs, self.profile)
        with self.assertRaises(E.BuildError):
            E.exclusive(link, b'replace target')
        with self.assertRaises(FileExistsError):
            E.exclusive(target, b'replace existing')
        self.assertEqual(snapshot(self.repo), before)
        self.assertTrue(link.is_symlink())

    def test_operation_lock_rejects_concurrency_and_releases_after_failure(self):
        before = snapshot(self.repo)
        with self.assertRaisesRegex(RuntimeError, 'injected failure'):
            with E.operation(self.repo):
                path = self.repo / 'build' / E.LOCK
                self.assertTrue(path.is_file())
                original = path.read_bytes()
                with self.assertRaisesRegex(E.BuildError, 'operation lock exists'):
                    with E.operation(self.repo):
                        self.fail('concurrent operation entered')
                self.assertEqual(path.read_bytes(), original)
                raise RuntimeError('injected failure')
        self.assertEqual(snapshot(self.repo), before)

    def test_operation_does_not_remove_replaced_lock(self):
        path = self.repo / 'build' / E.LOCK
        with self.assertRaisesRegex(E.BuildError, 'changed; nothing removed'):
            with E.operation(self.repo):
                path.write_bytes(b'replaced by another owner')
        self.assertEqual(path.read_bytes(), b'replaced by another owner')

    def test_completed_build_revalidates_without_native_commands_or_mutation(self):
        self.completed()
        before = snapshot(self.repo)
        with ExitStack() as stack:
            args = self.build_patches(stack)
            out = io.StringIO()
            with redirect_stdout(out):
                E.build(self.repo, args)
        self.assertEqual(snapshot(self.repo), before)
        self.assertIn('ANDROID ENGINE BUILD PASS', out.getvalue())

    def test_completed_build_rejects_false_device_validation_claim(self):
        self.completed(device_executed=True)
        before = snapshot(self.repo)
        with ExitStack() as stack:
            args = self.build_patches(stack)
            out = io.StringIO()
            with redirect_stdout(out), self.assertRaises(E.BuildError):
                E.build(self.repo, args)
        self.assertEqual(snapshot(self.repo), before)
        self.assertNotIn('ANDROID ENGINE BUILD PASS', out.getvalue())

    def test_foreign_helper_errors_are_translated_to_builder_error(self):
        for kind in (E.A.BuildError, E.L.BuildError, E.N.BuildError, E.P.PreparationError):
            with self.subTest(kind=kind):
                def fail():
                    raise kind('specific validation failure')
                with self.assertRaisesRegex(E.BuildError, 'specific validation failure'):
                    E.checked_call(fail)

    def test_partial_install_resumes_without_replacing_completed_or_previous_files(self):
        runtime = self.protected.parent / 'fixture runtime'
        for index, relative in enumerate(E.ARTIFACTS):
            if index < 2:
                write(self.root / 'build', 'wesnoth-engine-cli' if index == 0 else 'libmain.so', ('artifact ' + relative).encode())
            else:
                write(self.repo / 'build/android-media', relative, ('artifact ' + relative).encode())
        runtime.write_bytes((self.repo / 'build/android-media' / E.ARTIFACTS[2]).read_bytes())
        first = write(self.root, E.ARTIFACTS[0], (self.root / 'build/wesnoth-engine-cli').read_bytes())
        before = snapshot(self.repo)
        E.install_files(self.repo, self.root, {'runtime': runtime})
        for name, data in before.items():
            self.assertEqual((self.repo / name).read_bytes(), data)
        self.assertEqual(set(E.M.inventory(self.root)), set(E.ARTIFACTS))
        installed = snapshot(self.repo)
        E.install_files(self.repo, self.root, {'runtime': runtime})
        self.assertEqual(snapshot(self.repo), installed)
        first.write_bytes(b'user-modified current artifact')
        before = snapshot(self.repo)
        with self.assertRaisesRegex(E.BuildError, 'Installed engine artifact differs'):
            E.install_files(self.repo, self.root, {'runtime': runtime})
        self.assertEqual(snapshot(self.repo), before)

    def test_unknown_install_file_prevents_copying_and_preserves_all_bytes(self):
        runtime = write(self.repo, 'build/android-media/prefix/lib/libc++_shared.so', b'runtime')
        write(self.root, 'prefix/user-owned.txt', b'unknown prefix file')
        before = snapshot(self.repo)
        with self.assertRaisesRegex(E.BuildError, 'Unexpected installed engine files'):
            E.install_files(self.repo, self.root, {'runtime': runtime})
        self.assertEqual(snapshot(self.repo), before)

    def test_completed_verification_is_read_only_and_requires_false_device_claim(self):
        self.completed()
        before = snapshot(self.repo)
        with ExitStack() as stack:
            self.build_patches(stack)
            E.verify_receipt(self.repo)
        self.assertEqual(snapshot(self.repo), before)
        self.completed(device_executed=True)
        before = snapshot(self.repo)
        with ExitStack() as stack:
            self.build_patches(stack)
            with self.assertRaisesRegex(E.BuildError, 'receipt, prerequisites, source or artifacts differ'):
                E.verify_receipt(self.repo)
        self.assertEqual(snapshot(self.repo), before)

    def test_source_inventory_rejects_extra_changed_or_missing_files(self):
        # The earlier source helper's publisher-pin check is already tested;
        # here a tiny complete fixture exercises this builder's additional
        # strict inventory check against actual directory contents.
        sources = {'src/fixture.hpp': b'pinned implicit include'}
        cpp = ['wesnoth.cpp'] + ['fixture-' + str(index) + '.cpp' for index in range(536)]
        lua = ['lua-' + str(index) + '.c' for index in range(31)]
        for name in cpp + lua:
            sources['src/' + name] = ('nonempty ' + name).encode()
        for name in ('wesnoth', 'libwesnoth_core', 'libwesnoth', 'libwesnoth_sdl', 'libwesnoth_widgets'):
            sources['source_lists/' + name] = ('\n'.join(cpp[1:]) + '\n').encode() if name == 'wesnoth' else b''
        sources['source_lists/lua'] = ('\n'.join(lua) + '\n').encode()
        for name, data in sources.items():
            write(self.repo / 'engine', name, data)
        write(self.repo, 'engine/source-manifest.json', b'fixture manifest')
        plan = E.B.read_json(REPO / 'tools/native-engine/build-plan.json')
        for name in plan['libraries']:
            write(self.repo, name, b'fixture archive validated by prior receipt')
        write(self.repo, 'tools/native-engine/build-plan.json', E.B.json_bytes(plan))
        with patch.object(E, 'PROJECT_INPUTS', ('tools/native-engine/build-plan.json',)), \
             patch.object(E.B, 'profile_and_inputs', return_value=(self.profile, {})), \
             patch.object(E.P, 'verified_source', return_value=(sources, {})):
            E.selected_inputs(self.repo)
            extra = write(self.repo, 'engine/src/unrecorded.hpp', b'new implicit include')
            before = snapshot(self.repo)
            with self.assertRaisesRegex(E.BuildError, 'changed or unrecorded'):
                E.selected_inputs(self.repo)
            self.assertEqual(snapshot(self.repo), before)
            extra.unlink()
            header = self.repo / 'engine/src/fixture.hpp'
            header.write_bytes(b'changed header')
            before = snapshot(self.repo)
            with self.assertRaisesRegex(E.BuildError, 'changed or unrecorded'):
                E.selected_inputs(self.repo)
            self.assertEqual(snapshot(self.repo), before)
            header.unlink()
            before = snapshot(self.repo)
            with self.assertRaisesRegex(E.BuildError, 'changed or unrecorded'):
                E.selected_inputs(self.repo)
            self.assertEqual(snapshot(self.repo), before)

    def test_selected_real_source_and_dependency_plan_are_read_only(self):
        selected = (REPO / 'engine/source-manifest.json', REPO / 'engine/src/wesnoth.cpp',
                    REPO / 'tools/native-engine/build-plan.json')
        before = {str(path): E.B.fingerprint(path) for path in selected}
        profile, inputs, plan = E.selected_inputs(REPO)
        self.assertEqual(profile['wesnoth_version'], '1.19.26')
        self.assertEqual((plan['engine_cpp_count'], plan['lua_cpp_count']), (537, 31))
        self.assertEqual(len(plan['libraries']), 50)
        self.assertEqual(inputs['upstream_commit'], E.P.UPSTREAM_COMMIT)
        self.assertEqual({str(path): E.B.fingerprint(path) for path in selected}, before)

    def test_plan_wrong_counts_and_unsafe_dependency_are_rejected(self):
        plan_path = REPO / 'tools/native-engine/build-plan.json'
        original = E.B.read_json
        baseline = original(plan_path)
        variants = []
        for field in ('engine_cpp_count', 'lua_cpp_count'):
            plan = copy.deepcopy(baseline); plan[field] -= 1; variants.append(plan)
        plan = copy.deepcopy(baseline); plan['libraries'][0] = '../outside.a'; variants.append(plan)
        for plan in variants:
            def read(path):
                return plan if path == plan_path else original(path)
            with self.subTest(plan=plan['libraries'][0], cpp=plan['engine_cpp_count'], lua=plan['lua_cpp_count']):
                with patch.object(E.B, 'read_json', side_effect=read):
                    with self.assertRaises(E.BuildError):
                        E.selected_inputs(REPO)


if __name__ == '__main__':
    unittest.main()
