"""Owned APK packaging, immutable prerequisites and runtime-byte preservation."""
from contextlib import ExitStack, redirect_stdout
import copy
import importlib.util
import io
from pathlib import Path
import struct
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import zipfile

REPO = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


G = load('game_builder', REPO / 'tools/build_android_game.py')
F = load('game_elf_fixtures', REPO / 'tests/test_android_engine_artifacts.py')


def write(root, relative, content):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob('*') if path.is_file() and not path.is_symlink()}


class GameBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='game owned packaging ')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo with spaces'
        self.root = self.repo / 'build/android-game'
        self.repo.mkdir()
        self.manifest = b'["data/_main.cfg",4,"' + b'a' * 64 + b'"]\n'
        self.manifest_patch = patch.object(G, 'MANIFEST_HASH', G.B.sha256(self.manifest))
        self.manifest_patch.start(); self.addCleanup(self.manifest_patch.stop)
        self.profile = {'min_sdk': 23, 'gradle_version': '8.9', 'gradle_sha256': 'f' * 64}
        self.inputs = {'project_files': {'fixture': {'bytes': 1, 'sha256': 'a' * 64}}}
        self.prior = {'engine_receipt': {'bytes': 7, 'sha256': 'b' * 64}, 'data_receipt': {'bytes': 8, 'sha256': 'c' * 64}}
        self.wrapper = b'preserved wrapper fixture'
        self.sources = {'sources/' + G.S.SDL_ROOT + '/android-project/app/src/main/java/org/libsdl/app/' + name:
                        ('pinned SDL glue ' + name).encode() for name in G.S.JAVA_NAMES}
        for name in G.TEMPLATES:
            write(self.repo, 'tools/native-game/' + name, ('template ' + name + ' @MIN_SDK@').encode())
        write(self.repo, 'build/android-game-data/' + G.MANIFEST, self.manifest)
        write(self.repo, 'build/android-game-data/receipt.json', b'data receipt sentinel')
        write(self.repo, 'build/android-engine/receipt.json', b'engine receipt sentinel')
        for name in G.LIBRARIES:
            write(self.repo, 'build/android-engine/prefix/lib/' + name,
                  bytes(F.elf(api=21 if name == 'libc++_shared.so' else 23)))
        write(self.repo, 'packaging/android/download/source-cache-sentinel.tar', b'archive sentinel')
        write(self.repo, '.env', b'private sentinel')
        self.protected = snapshot(self.repo)
        self.ndk = self.repo / 'fake ndk'
        write(self.ndk, 'source.properties', b'Pkg.Revision = 26.3.11579264\n')
        suffix = '.exe' if G.sys.platform == 'win32' else ''
        write(self.ndk, 'toolchains/llvm/prebuilt/' + G.B.host_tag() + '/bin/llvm-strip' + suffix, b'strip executable fixture')
        self.aapt2 = write(self.repo, 'sdk/aapt2', b'aapt2 fixture')
        self.signer = write(self.repo, 'sdk/apksigner.jar', b'signer fixture')
        self.java = write(self.repo, 'jdk/bin/java', b'java fixture')
        self.launcher = write(self.repo, 'gradle launcher.jar', b'gradle fixture')
        self.args = SimpleNamespace(ndk=self.ndk, sdk=self.repo / 'sdk', java=self.java,
                                    gradle_user_home=self.repo / 'gradle user home', archive_directory=None)
        self.calls = []
        self.failure = None

    def tearDown(self):
        for relative, content in self.protected.items():
            self.assertEqual((self.repo / relative).read_bytes(), content, relative)

    def selected(self):
        return self.profile, copy.deepcopy(self.inputs), self.wrapper

    def files(self):
        return G.generated_files(self.repo, self.profile, self.wrapper, self.sources)

    def prepared(self):
        self.root.parent.mkdir(exist_ok=True)
        return G.prepare(self.root, self.files(), self.profile, self.inputs)

    def apk(self, extra=None, changed=None):
        target = self.root / G.APK
        target.parent.mkdir(parents=True, exist_ok=True)
        contents = {'AndroidManifest.xml': b'compiled manifest', 'classes.dex': b'dex\nfixture',
                    'assets/' + G.MANIFEST: self.manifest}
        contents.update({'lib/x86_64/' + name: (self.root / G.JNI_ROOT / name).read_bytes() for name in G.LIBRARIES})
        contents.update(extra or {})
        for name, value in (changed or {}).items():
            if value is None: contents.pop(name)
            else: contents[name] = value
        with zipfile.ZipFile(target, 'w') as archive:
            for name, data in contents.items(): archive.writestr(name, data)
        return target

    def invoke_tool(self, arguments, env=None, capture=False):
        args = [str(value) for value in arguments]
        self.calls.append((args, env, capture))
        if '--strip-debug' in args:
            if self.failure == 'strip':
                Path(args[3]).write_bytes(b'partial interrupted native copy')
                raise G.S.BuildError('fixture strip failure')
            self.assertFalse(Path(args[3]).exists())
            Path(args[3]).write_bytes(Path(args[4]).read_bytes())
        elif 'org.gradle.wrapper.GradleWrapperMain' in args:
            self.assertIn('--offline', args)
            self.assertIn('--no-daemon', args)
            self.assertEqual(env['GRADLE_USER_HOME'], str(self.args.gradle_user_home))
            if self.failure == 'gradle': raise G.S.BuildError('fixture Gradle failure')
            self.apk()
        elif args[1:3] == ['dump', 'badging']:
            return ("package: name='org.wesnoth.offline' versionCode='1192600' versionName='1.19.26'\n"
                    "sdkVersion:'23'\ntargetSdkVersion:'35'\n"
                    "launchable-activity: name='org.wesnoth.offline.OfflineInitActivity'\n"
                    "native-code: 'x86_64'\n")
        elif args[1:3] == ['dump', 'xmltree']:
            return 'manifest activity name="org.wesnoth.offline.OfflineGameActivity"'
        elif args[1] == '-jar':
            if self.failure == 'signature': raise G.S.BuildError('fixture signature verification failed')
        else:
            raise AssertionError('Unexpected external command: ' + repr(args))
        return ''

    def patches(self, stack):
        stack.enter_context(patch.object(G, 'selected_inputs', side_effect=lambda repo: self.selected()))
        stack.enter_context(patch.object(G, 'prior_inputs', return_value=self.prior))
        stack.enter_context(patch.object(G.S, 'verified_archive', return_value=(G.ARCHIVE, b'pinned source archive')))
        stack.enter_context(patch.object(G.S, 'archive_files', return_value=self.sources))
        stack.enter_context(patch.object(G.S, 'require_cached_gradle', return_value=self.launcher))
        stack.enter_context(patch.object(G, 'android_tools', return_value=(self.aapt2, self.signer, 'Java21 fixture')))
        stack.enter_context(patch.object(G.S, 'run', side_effect=self.invoke_tool))
        stack.enter_context(patch('subprocess.run', side_effect=AssertionError('Real process not allowed in unit fixtures')))

    def built(self):
        with ExitStack() as stack:
            self.patches(stack)
            return G.build(self.repo, self.args)

    def test_real_plan_and_pinned_constants_agree(self):
        with patch.object(G, 'MANIFEST_HASH', 'd805fd055b263a5b09cd19576dc9df83c64e47bbcf7821cbb6550b686a74e53c'):
            self.assertEqual(G.B.read_json(REPO / 'tools/native-game/build-plan.json'), G.expected_plan())

    def test_project_generation_uses_exact_glue_index_and_offline_wrapper(self):
        files = self.files()
        self.assertEqual(files['app/src/main/assets/' + G.MANIFEST], self.manifest)
        self.assertEqual(len([name for name in files if '/org/libsdl/app/' in name]), 11)
        self.assertEqual(files['gradle/wrapper/gradle-wrapper.jar'], self.wrapper)
        self.assertIn(b'distributionSha256Sum=' + b'f' * 64, files['gradle/wrapper/gradle-wrapper.properties'])
        for malformed in ({}, dict(self.sources, **{'sources/' + G.S.SDL_ROOT + '/android-project/app/src/main/java/org/libsdl/app/Extra.java': b'extra'})):
            with self.assertRaises(G.BuildError): G.generated_files(self.repo, self.profile, self.wrapper, malformed)

    def test_prepare_reuses_exact_inputs_and_preserves_unrelated_files(self):
        marker = self.prepared()
        write(self.root, 'app/build/intermediates/owned.bin', b'Gradle output')
        before = snapshot(self.repo)
        self.assertEqual(G.prepare(self.root, self.files(), self.profile, self.inputs), marker)
        self.assertEqual(snapshot(self.repo), before)
        with self.assertRaises(G.BuildError): G.prepare(self.root, self.files(), self.profile, {'different': True})
        self.assertEqual(snapshot(self.repo), before)

    def test_modified_missing_extra_and_linked_sources_are_rejected(self):
        marker = self.prepared()
        path = self.root / 'app/src/main/java/org/wesnoth/offline/OfflineGameActivity.java'
        original = path.read_bytes(); path.write_bytes(b'changed')
        with self.assertRaises(G.BuildError): G.verify_owned(self.root, marker)
        path.unlink()
        with self.assertRaises(FileNotFoundError): G.verify_owned(self.root, marker)
        path.write_bytes(original)
        extra = write(self.root, 'app/src/main/java/Unexpected.java', b'extra')
        with self.assertRaises(G.BuildError): G.verify_owned(self.root, marker)
        extra.unlink(); path.unlink(); path.symlink_to(self.java)
        with self.assertRaises(G.BuildError): G.verify_owned(self.root, marker)

    def test_prepare_resumes_only_missing_exact_generated_files(self):
        marker = self.prepared()
        path = self.root / 'app/src/main/AndroidManifest.xml'
        original = path.read_bytes(); path.unlink()
        G.prepare(self.root, self.files(), self.profile, self.inputs)
        self.assertEqual(path.read_bytes(), original)
        G.verify_owned(self.root, marker)

    def test_loadable_signature_allows_only_section_bookkeeping_and_nonloaded_debug(self):
        data = F.elf()
        expected = G.loadable_signature(data)
        data += b'unloaded debug data'
        struct.pack_into('<Q', data, 40, 0x999)
        struct.pack_into('<HHH', data, 58, 64, 7, 3)
        self.assertEqual(G.loadable_signature(data), expected)
        data[0x500] ^= 1
        self.assertNotEqual(G.loadable_signature(data), expected)
        for malformed in (b'', bytes(64), bytes(F.elf())[:100]):
            with self.assertRaises(G.BuildError): G.loadable_signature(malformed)

    def test_linux_ndk_strip_alias_retains_multicall_name_and_regular_fingerprint_target(self):
        directory = self.ndk / 'toolchains/llvm/prebuilt/linux-x86_64/bin'
        directory.mkdir(parents=True, exist_ok=True)
        command = directory / 'llvm-strip'
        if command.exists(): command.unlink()
        binary = write(directory, 'llvm-objcopy', b'NDK LLVM multicall binary')
        command.symlink_to('llvm-objcopy')
        self.assertEqual(G.strip_tool(self.ndk, 'linux-x86_64'), (command, binary))
        self.assertEqual(G.fingerprint(binary)['bytes'], len(b'NDK LLVM multicall binary'))
        with self.assertRaises(G.BuildError): G.regular(command, 'ordinary artifact still rejects links')

    def test_strip_alias_rejects_escape_other_tool_and_alias_chain(self):
        directory = self.ndk / 'toolchains/llvm/prebuilt/linux-x86_64/bin'
        directory.mkdir(parents=True, exist_ok=True)
        command = directory / 'llvm-strip'
        if command.exists(): command.unlink()
        write(directory, 'llvm-ar', b'wrong LLVM dispatch target')
        outside = write(self.repo, 'outside-llvm-objcopy', b'outside tool')
        for target in (str(outside), 'llvm-ar', '../llvm-objcopy'):
            command.symlink_to(target)
            with self.assertRaises((G.BuildError, OSError)): G.strip_tool(self.ndk, 'linux-x86_64')
            command.unlink()
        (directory / 'llvm-objcopy').symlink_to(outside)
        command.symlink_to('llvm-objcopy')
        with self.assertRaises(G.BuildError): G.strip_tool(self.ndk, 'linux-x86_64')

    def test_regular_ndk_tool_still_supported_and_linked_bin_rejected(self):
        command, binary = G.strip_tool(self.ndk, G.B.host_tag())
        self.assertEqual(command, binary)
        linked_ndk = self.repo / 'linked ndk'
        linked_ndk.symlink_to(self.ndk, target_is_directory=True)
        with self.assertRaises(G.BuildError): G.strip_tool(linked_ndk, G.B.host_tag())

    def test_native_copies_retain_runtime_bytes_and_are_reused_without_strip(self):
        self.prepared()
        with patch.object(G.S, 'run', side_effect=self.invoke_tool):
            first = G.prepare_libraries(self.repo, self.root, Path('selected llvm-strip'))
        self.assertEqual(len(self.calls), 5)
        before = snapshot(self.repo)
        with patch.object(G.S, 'run', side_effect=AssertionError('Repeated stripping')):
            self.assertEqual(G.prepare_libraries(self.repo, self.root, Path('unused')), first)
        self.assertEqual(snapshot(self.repo), before)
        path = self.root / G.JNI_ROOT / 'libmain.so'
        changed = bytearray(path.read_bytes()); changed[0x500] ^= 1; path.write_bytes(changed)
        with self.assertRaisesRegex(G.BuildError, 'runtime bytes'):
            G.prepare_libraries(self.repo, self.root, Path('unused'))

    def test_failed_strip_preserves_partial_copy_and_blocks_unrecorded_retry(self):
        self.prepared(); self.failure = 'strip'
        with patch.object(G.S, 'run', side_effect=self.invoke_tool):
            with self.assertRaises(G.BuildError): G.prepare_libraries(self.repo, self.root, Path('strip'))
        before = snapshot(self.repo)
        with patch.object(G.S, 'run', side_effect=AssertionError('Overwrote failed output')):
            with self.assertRaisesRegex(G.BuildError, 'unfinished/unrecorded'):
                G.prepare_libraries(self.repo, self.root, Path('strip'))
        self.assertEqual(snapshot(self.repo), before)

    def test_offline_build_receipt_and_toolless_verification(self):
        receipt = self.built()
        self.assertEqual(set(receipt['artifacts']), set(G.ARTIFACTS))
        self.assertIs(receipt['device_executed'], False)
        self.assertTrue(receipt['validation']['apk']['signature_verified_at_build'])
        before = snapshot(self.repo)
        with ExitStack() as stack:
            self.patches(stack)
            stack.enter_context(patch.object(G.S, 'run', side_effect=AssertionError('Toolless verifier launched process')))
            self.assertEqual(G.verify_receipt(self.repo), receipt)
        self.assertEqual(snapshot(self.repo), before)

    def test_completed_build_rechecks_signature_but_never_strips_or_gradles(self):
        self.built(); self.calls.clear(); output = io.StringIO()
        with ExitStack() as stack, redirect_stdout(output):
            self.patches(stack)
            G.build(self.repo, self.args)
        self.assertIn('\nANDROID GAME BUILD PASS\n', output.getvalue())
        self.assertEqual(len(self.calls), 3)
        self.assertFalse(any('--strip-debug' in args or 'org.gradle.wrapper.GradleWrapperMain' in args for args, _, _ in self.calls))

    def test_gradle_or_signer_failure_never_writes_success_receipt(self):
        for failure in ('gradle', 'signature'):
            with self.subTest(failure=failure):
                self.failure = failure
                with ExitStack() as stack:
                    self.patches(stack)
                    with self.assertRaises(G.BuildError): G.build(self.repo, self.args)
                self.assertFalse((self.root / 'receipt.json').exists())
                self.assertFalse((self.repo / 'build' / G.LOCK).exists())

    def test_signature_identity_checks_reject_wrong_package_and_missing_game_activity(self):
        self.built()
        for answers in (['package: wrong'], [self.invoke_tool([self.aapt2, 'dump', 'badging']), 'wrong activity']):
            with patch.object(G.S, 'run', side_effect=answers):
                with self.assertRaises(G.BuildError): G.verify_android_signature(self.root / G.APK, self.aapt2, self.java, self.signer)

    def test_apk_rejects_extra_abi_changed_native_index_missing_dex_and_traversal(self):
        self.built()
        cases = [({'lib/arm64-v8a/libmain.so': b'wrong ABI'}, {}), ({}, {'lib/x86_64/libmain.so': b'changed'}),
                 ({}, {'assets/' + G.MANIFEST: b'changed index'}), ({}, {'classes.dex': None}),
                 ({'../unexpected': b'unsafe'}, {}), ({'assets/unexpected': b'extra'}, {})]
        for extra, changed in cases:
            with self.subTest(extra=tuple(extra), changed=tuple(changed)):
                apk = self.apk(extra, changed)
                with self.assertRaises(G.BuildError): G.inspect_apk(apk, self.root)

    def test_modified_apk_or_signature_claim_blocks_receipt_verification(self):
        self.built()
        path = self.root / 'receipt.json'; original = path.read_bytes()
        receipt = G.B.read_json(path); receipt['validation']['apk']['signature_verified_at_build'] = False
        path.write_bytes(G.B.json_bytes(receipt))
        with ExitStack() as stack:
            self.patches(stack)
            with self.assertRaises(G.BuildError): G.verify_receipt(self.repo)
        path.write_bytes(original)
        with (self.root / G.APK).open('ab') as stream: stream.write(b'changed APK trailer')
        with ExitStack() as stack:
            self.patches(stack)
            with self.assertRaises(G.BuildError): G.verify_receipt(self.repo)

    def test_missing_prerequisites_stop_before_stage_and_operation_lock(self):
        with ExitStack() as stack:
            self.patches(stack)
            stack.enter_context(patch.object(G, 'prior_inputs', side_effect=G.BuildError('prerequisite changed')))
            with self.assertRaises(G.BuildError): G.build(self.repo, self.args)
        self.assertFalse(self.root.exists())
        self.assertFalse((self.repo / 'build' / G.LOCK).exists())

    def test_data_path_failure_is_a_normal_blocked_build_error(self):
        with patch.object(G.E, 'verify_receipt', return_value={}), patch.object(
                G.D, 'verify_receipt', side_effect=G.D.V.VerificationError('linked data file')):
            with self.assertRaisesRegex(G.BuildError, 'linked data file'):
                G.prior_inputs(self.repo)

    def test_operation_lock_and_symlink_ancestors_block_without_overwrite(self):
        self.root.parent.mkdir(exist_ok=True)
        lock = write(self.repo, 'build/' + G.LOCK, b'existing operation')
        with self.assertRaises(G.BuildError):
            with G.operation(self.repo): self.fail('entered locked stage')
        self.assertEqual(lock.read_bytes(), b'existing operation')
        linked = self.repo / 'linked'; linked.symlink_to(self.root.parent, target_is_directory=True)
        with self.assertRaises(G.BuildError): G.safe(linked / 'new output')


if __name__ == '__main__':
    unittest.main()
