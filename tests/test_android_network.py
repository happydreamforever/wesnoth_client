"""Network source safety, native artifacts, generator reuse and receipt binding."""
import importlib.util
import io
from pathlib import Path, PurePosixPath, PureWindowsPath
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from test_android_bootstrap import ar_library, elf_program

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('android_network', REPO / 'tools/build_android_network.py')
N = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(N)


def fixture(repo):
    def write(root, name, content):
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
    for name in N.PROJECT_INPUTS: write(repo, name, (REPO / name).read_bytes())
    fonts = repo / 'build/android-fonts'
    for name in N.F.REQUIRED_HEADERS: write(fonts, 'prefix/include/' + name, b'font header')
    for name in N.F.REQUIRED_ARCHIVES: write(fonts, 'prefix/lib/' + name, ar_library(objects=2))
    for name in N.F.ARTIFACTS: write(fonts, name, elf_program(needed=('libc.so', 'libc++_shared.so', 'libm.so')))
    profile, inputs, _, pins = N.F.selected_inputs(repo)
    validation, files = N.F.validate_prefix(fonts, {'cmake_toolchain': repo / 'unused-ndk/build/cmake/android.toolchain.cmake'})
    write(fonts, 'receipt.json', N.B.json_bytes({'schema_version': 1, 'kind': N.F.KIND, 'profile': profile,
        'inputs': {'project_files': inputs, 'archives': pins}, 'validation': validation, 'prefix_files': files,
        'artifacts': {name: files[name] for name in N.F.ARTIFACTS}}))
    root = repo / 'build/android-network'
    plan_path = repo / 'tools/native-network/build-plan.json'; plan = N.B.read_json(plan_path)
    headers = {}
    names = plan['public_header_paths']['openssl'] + plan['public_header_paths']['curl']
    for index, name in enumerate(names):
        content = ('header ' + str(index)).encode()
        write(root, 'prefix/include/' + name, content); headers[name] = N.pin(content)
    plan['public_headers'] = N.inventory_pin(headers)
    plan['licenses'] = {'openssl-LICENSE.txt': N.pin(b'complete OpenSSL license'), 'curl-COPYING': N.pin(b'complete curl license')}
    plan_path.write_bytes(N.B.json_bytes(plan))
    for name, expected in plan['licenses'].items():
        write(root, 'prefix/share/licenses/wesnoth-network/' + name, b'complete OpenSSL license' if name.startswith('openssl') else b'complete curl license')
    for name, content in N.pkg_config_files().items(): write(root, 'prefix/lib/pkgconfig/' + name, content)
    for name, count in plan['object_counts'].items(): write(root, 'prefix/lib/' + name, ar_library(objects=count))
    write(root, N.ARTIFACTS[0], elf_program(needed=('libc.so', 'libc++_shared.so', 'libz.so')))
    write(root, N.ARTIFACTS[1], (fonts / N.F.ARTIFACTS[1]).read_bytes())
    profile, inputs, plan, pins = N.selected_inputs(repo)
    validation, files = N.validate_prefix(root, plan)
    receipt = {'schema_version': 1, 'kind': N.KIND, 'profile': profile,
        'inputs': {'project_files': inputs, 'archives': pins, 'prerequisites': N.prior_inputs(repo)},
        'validation': validation, 'prefix_files': files, 'artifacts': {n: files[n] for n in N.ARTIFACTS}}
    write(root, 'receipt.json', N.B.json_bytes(receipt)); N.save_prefix_state(root)
    return root, receipt


class NetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='wesnoth network fixtures ')
        cls.repo = Path(cls.temp.name) / 'repo'; cls.repo.mkdir()
        cls.root, cls.receipt = fixture(cls.repo)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def change(self, path, data):
        old = path.read_bytes(); self.addCleanup(path.write_bytes, old); path.write_bytes(data)

    def test_contract_and_independent_prerequisite(self):
        profile, inputs, plan, pins = N.selected_inputs(REPO)
        self.assertEqual(profile, N.B.SELECTED_PROFILE)
        self.assertEqual(set(pins), set(N.ARCHIVES))
        self.assertEqual(plan['versions'], {'openssl': '3.4.1', 'curl': '8.1.1'})
        self.assertEqual(len(plan['openssl_generated']), 47)
        self.assertNotIn('tools/build_android_boost.py', inputs)
        self.assertNotIn('tools/build_android_icu.py', inputs)

    def test_public_header_plan_retains_original_hashes(self):
        plan = N.selected_inputs(REPO)[2]
        self.assertEqual(plan['openssl_headers'], {'count': 141, 'sha256': 'd44dedfaad6ed6bc1deb66ab04e36a13676a8869617f5377f2cc9b89455e1579'})
        self.assertEqual(plan['public_headers'], {'count': 153, 'sha256': 'cef1e52818ac64d2e22ddf4785fb6ff310087fadef2e7f3ac49e9ae70965f83e'})
        self.assertEqual({name: len(paths) for name, paths in plan['public_header_paths'].items()}, {'openssl': 141, 'curl': 12})
        self.assertEqual(plan['prefix_file_count'], 164)

    def test_header_list_rejects_traversal_duplicates_wrong_case_and_omissions(self):
        path = self.repo / 'tools/native-network/build-plan.json'; original = path.read_bytes()
        for replacement in ('openssl/../outside.h', 'openssl/__DECC_INCLUDE_EPILOGUE.H', 'curl/aes.h', 'openssl/asn1.h', None):
            with self.subTest(replacement=replacement):
                plan = N.B.read_json(path); paths = plan['public_header_paths']['openssl']
                if replacement is None: paths.pop(0)
                else: paths[0] = replacement
                paths.sort(); path.write_bytes(N.B.json_bytes(plan))
                try:
                    with self.assertRaisesRegex(N.BuildError, 'header paths'): N.selected_inputs(self.repo)
                finally: path.write_bytes(original)

    def test_explicit_headers_ignore_host_glob_rules_and_preserve_vms_sources(self):
        names = N.selected_inputs(self.repo)[2]['public_header_paths']['openssl']
        uppercase = ['openssl/__DECC_INCLUDE_EPILOGUE.H', 'openssl/__DECC_INCLUDE_PROLOGUE.H']
        all_names = names + uppercase
        posix = [name for name in all_names if PurePosixPath(name).match('*.h')]
        windows = [name for name in all_names if PureWindowsPath(name).match('*.h')]
        self.assertEqual(posix, names)
        self.assertEqual(len(windows), 143)
        self.assertEqual(set(windows) - set(posix), set(uppercase))
        with tempfile.TemporaryDirectory(prefix='network explicit headers ') as tmp:
            include = Path(tmp); records = {}
            for name in all_names:
                path = include / name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(('source ' + name).encode()); records[name] = N.B.fingerprint(path)
            expected = N.inventory_pin({name: records[name] for name in names})
            # The production selector must not enumerate suffixes, regardless
            # of whether Python would apply Windows or POSIX glob semantics.
            with patch.object(Path, 'glob', side_effect=AssertionError('unexpected glob')), \
                 patch.object(Path, 'rglob', side_effect=AssertionError('unexpected recursive glob')):
                actual = N.verify_public_headers(include, names, expected, 'Fixture headers')
            self.assertEqual(actual, {name: records[name] for name in names})
            self.assertEqual({name: N.B.fingerprint(include / name) for name in all_names}, records)

    def test_explicit_missing_and_tampered_headers_still_rejected(self):
        with tempfile.TemporaryDirectory(prefix='network missing header ') as tmp:
            include = Path(tmp); path = include / 'openssl/aes.h'; path.parent.mkdir(); path.write_bytes(b'original')
            expected = N.inventory_pin({'openssl/aes.h': N.B.fingerprint(path)})
            path.write_bytes(b'changed')
            with self.assertRaisesRegex(N.BuildError, 'differ'): N.verify_public_headers(include, ['openssl/aes.h'], expected, 'Fixture headers')
            path.unlink()
            with self.assertRaisesRegex(N.BuildError, 'missing'): N.verify_public_headers(include, ['openssl/aes.h'], expected, 'Fixture headers')

    def test_unexpected_uppercase_header_in_prefix_is_rejected(self):
        plan = N.selected_inputs(self.repo)[2]
        path = self.root / 'prefix/include/openssl/__DECC_INCLUDE_EPILOGUE.H'
        path.write_bytes(b'unexpected VMS-only header'); self.addCleanup(path.unlink)
        with self.assertRaisesRegex(N.BuildError, 'Unexpected network prefix'): N.validate_prefix(self.root, plan)
        self.assertEqual(path.read_bytes(), b'unexpected VMS-only header')

    def test_archive_traversal_link_duplicate_and_case_collision(self):
        for names, kind in [(['openssl-3.4.1/../outside'], tarfile.REGTYPE),
                            (['openssl-3.4.1/crypto/link'], tarfile.SYMTYPE),
                            (['openssl-3.4.1/a', 'openssl-3.4.1/a'], tarfile.REGTYPE),
                            (['openssl-3.4.1/crypto/A/x', 'openssl-3.4.1/crypto/a/y'], tarfile.REGTYPE),
                            (['wrong/root'], tarfile.REGTYPE)]:
            with self.subTest(names=names):
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode='w:gz') as archive:
                    for name in names:
                        entry = tarfile.TarInfo(name); entry.type = kind; entry.linkname = '../outside'; entry.size = int(kind == tarfile.REGTYPE)
                        archive.addfile(entry, io.BytesIO(b'x') if entry.size else None)
                with self.assertRaises(N.BuildError): N.archive_files(stream.getvalue(), 'openssl-3.4.1')

    def test_receipt_verifies_without_cache_tools_or_subprocess(self):
        self.assertFalse((self.repo / 'packaging/android/download').exists())
        with patch.object(N.M, 'run', side_effect=AssertionError('unexpected child process')), patch.object(N.subprocess, 'run', side_effect=AssertionError('unexpected compiler')):
            self.assertEqual(N.verify_receipt(self.repo), self.receipt)

    def test_changed_prior_fonts_or_project_input_rejected(self):
        path = self.repo / 'build/android-fonts' / N.F.ARTIFACTS[0]; old = path.read_bytes()
        self.change(path, old + b'changed')
        with self.assertRaisesRegex(N.BuildError, 'font receipt'): N.verify_receipt(self.repo)
        path.write_bytes(old)
        path = self.repo / 'tools/native-network/probe.cpp'; self.change(path, path.read_bytes() + b'//changed')
        with self.assertRaisesRegex(N.BuildError, 'receipt'): N.verify_receipt(self.repo)

    def test_header_license_and_metadata_rejected(self):
        plan = N.selected_inputs(self.repo)[2]
        for relative, message in [('prefix/include/openssl/aes.h', 'headers'),
                                  ('prefix/share/licenses/wesnoth-network/curl-COPYING', 'license'),
                                  ('prefix/lib/pkgconfig/libcurl.pc', 'metadata')]:
            path = self.root / relative; old = path.read_bytes()
            try:
                path.write_bytes(b'changed')
                with self.assertRaisesRegex(N.BuildError, message): N.validate_prefix(self.root, plan)
            finally: path.write_bytes(old)

    def test_missing_static_object_and_unexpected_dependency_rejected(self):
        plan = N.selected_inputs(self.repo)[2]
        path = self.root / 'prefix/lib/libssl.a'; old = path.read_bytes()
        self.change(path, ar_library(objects=89))
        with self.assertRaisesRegex(N.BuildError, 'source lists'): N.validate_prefix(self.root, plan)
        path.write_bytes(old)
        for options in [{'api': 24}, {'interpreter': '/host/loader'}, {'needed': ('libc.so', 'libc++_shared.so', 'libz.so', 'libssl.so')}]:
            path = self.root / N.ARTIFACTS[0]; old = path.read_bytes()
            try:
                path.write_bytes(elf_program(**options))
                with self.assertRaises(N.BuildError): N.validate_prefix(self.root, plan)
            finally: path.write_bytes(old)

    def test_runtime_matches_completed_fonts(self):
        path = self.root / N.ARTIFACTS[1]; self.change(path, path.read_bytes() + b'changed')
        with self.assertRaisesRegex(N.BuildError, 'runtime differs'): N.verify_receipt(self.repo)

    def test_receipt_missing_artifact_hash_is_rejected(self):
        path = self.root / 'receipt.json'; receipt = N.B.read_json(path); receipt['artifacts'].pop(N.ARTIFACTS[0])
        self.change(path, N.B.json_bytes(receipt))
        with self.assertRaisesRegex(N.BuildError, 'receipt'): N.verify_receipt(self.repo)

    def test_preserves_modified_sources_and_prefix(self):
        with tempfile.TemporaryDirectory(prefix='network owned ') as tmp:
            root = Path(tmp) / 'stage'; files = {'sources/openssl-3.4.1/LICENSE.txt': b'license'}
            N.prepare_sources(root, files, N.B.SELECTED_PROFILE, {})
            path = root / 'sources/openssl-3.4.1/LICENSE.txt'; path.write_bytes(b'user content')
            with self.assertRaisesRegex(N.BuildError, 'changed'): N.prepare_sources(root, files, N.B.SELECTED_PROFILE, {})
            self.assertEqual(path.read_bytes(), b'user content')
            (root / 'prefix').mkdir(); (root / 'prefix/user-file').write_bytes(b'user file')
            with self.assertRaisesRegex(N.BuildError, 'no owned'): N.verify_prefix(root)
            N.save_prefix_state(root); (root / 'prefix/user-file').write_bytes(b'changed')
            with self.assertRaisesRegex(N.BuildError, 'changed'): N.verify_prefix(root)

    def test_symlink_prefix_is_refused(self):
        with tempfile.TemporaryDirectory(prefix='network link ') as tmp:
            root = Path(tmp) / 'stage'; root.mkdir(); outside = Path(tmp) / 'outside'; outside.mkdir()
            try: (root / 'prefix').symlink_to(outside, target_is_directory=True)
            except OSError: self.skipTest('Symlinks unavailable')
            with self.assertRaises(N.BuildError): N.verify_prefix(root)

    def test_generation_binding_changes_for_scripts_patch_and_targets(self):
        plan = N.selected_inputs(self.repo)[2]; original = N.generation_binding(self.repo, plan)
        path = self.repo / 'tools/native-network/configure-openssl.sh'; self.change(path, path.read_bytes() + b'\n# changed')
        self.assertNotEqual(original, N.generation_binding(self.repo, plan))
        changed = dict(plan, openssl_generated=plan['openssl_generated'] + ['unexpected.c'])
        self.assertNotEqual(N.generation_binding(self.repo, plan), N.generation_binding(self.repo, changed))
        changed = dict(plan, public_header_paths={'openssl': ['openssl/changed.h'], 'curl': []})
        self.assertNotEqual(N.generation_binding(self.repo, plan), N.generation_binding(self.repo, changed))

    def test_generation_missing_or_modified_state_refused(self):
        plan = N.selected_inputs(self.repo)[2]
        with tempfile.TemporaryDirectory(prefix='network generation ') as tmp:
            root = Path(tmp); work = root / 'work/openssl-3.4.1'; work.mkdir(parents=True)
            sentinel = work / 'user-file'; sentinel.write_bytes(b'unchanged')
            with self.assertRaisesRegex(N.BuildError, 'generation area'): N.verify_generated(self.repo, root, plan)
            self.assertEqual(sentinel.read_bytes(), b'unchanged')
            (root / '.generated-state.json').write_bytes(N.B.json_bytes({'inputs': {}, 'files': N.M.inventory(root, 'work')}))
            with self.assertRaisesRegex(N.BuildError, 'generation area'): N.verify_generated(self.repo, root, plan)

    def test_modified_generation_does_not_remove_completed_receipt(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory(prefix='network receipt preservation ') as tmp:
            repo = Path(tmp); root = repo / 'build/android-network'; work = root / 'work/openssl-3.4.1'
            work.mkdir(parents=True); (work / 'user-file').write_bytes(b'preserve changed source')
            receipt = root / 'receipt.json'; receipt.write_bytes(b'previous completed receipt')
            plan = {'source_inventories': {name: N.inventory_pin({}) for name in N.ARCHIVES.values()}}
            args = SimpleNamespace(ndk=repo / 'unused', cmake=repo / 'unused', ninja=repo / 'unused')
            with patch.object(N, 'selected_inputs', return_value=({}, {}, plan, {name: {} for name in N.ARCHIVES})), \
                 patch.object(N, 'prior_inputs', return_value={}), patch.object(N.M, 'archive_data', return_value=b''), \
                 patch.object(N, 'archive_files', return_value={}), patch.object(N.B, 'ndk_paths', return_value={}), \
                 patch.object(N.B, 'check_tools', return_value={}), patch.object(N, 'prepare_sources'), \
                 patch.object(N, 'verify_prefix'):
                with self.assertRaisesRegex(N.BuildError, 'generation area'): N.build(repo, args)
            self.assertEqual(receipt.read_bytes(), b'previous completed receipt')
            self.assertEqual((work / 'user-file').read_bytes(), b'preserve changed source')

    def test_generation_reuse_never_runs_configure(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory(prefix='network reuse ') as tmp:
            root = Path(tmp)
            with patch.object(N, 'verify_generated', return_value=True), patch.object(N.M, 'run', side_effect=AssertionError('must not regenerate')):
                self.assertEqual(N.generate_openssl(self.repo, root, SimpleNamespace(), {}, {}, {}), root / 'work/openssl-3.4.1')


if __name__ == '__main__': unittest.main()
