"""ICU source safety, complete linked data, receipt binding and immutable stages."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path, PureWindowsPath
import struct
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from test_android_bootstrap import ar_library, elf_object, elf_program

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('android_icu', REPO / 'tools/build_android_icu.py')
I = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(I)


def data_archive(content=b'complete locale data', alignment=16, flags=2, value=0, info=0x11, name=b'icudt78_dat', size=None):
    """A bounded ELF/ar fixture with actual section/symbol offsets, no mock parser."""
    size = len(content) if size is None else size
    obj = bytearray(512 + len(content))
    obj[:64] = elf_object()
    struct.pack_into('<Q', obj, 40, 64)
    struct.pack_into('<HH', obj, 58, 64, 4)
    # null, allocated data, symbol table, string table
    sections = [(0,)*10, (0,1,flags,0,512,len(content),0,0,alignment,0),
                (0,2,0,0,336,48,3,1,8,24), (0,3,0,0,400,len(name)+2,0,0,1,0)]
    for index, section in enumerate(sections):
        struct.pack_into('<IIQQQQIIQQ', obj, 64 + index*64, *section)
    struct.pack_into('<IBBHQQ', obj, 360, 1, info, 0, 1, value, size)
    obj[400:402+len(name)] = b'\0' + name + b'\0'
    obj[512:] = content
    header = ('icudata.S.o/'.ljust(16) + '0'.ljust(12) + '0'.ljust(6) + '0'.ljust(6)
              + '644'.ljust(8) + str(len(obj)).ljust(10) + '`\n').encode()
    return b'!<arch>\n' + header + obj + (b'\n' if len(obj)%2 else b'')


def create_fixture(repo):
    def write(root, name, content=b'fixture'):
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content); return path
    for name in I.PROJECT_INPUTS:
        write(repo, name, (REPO / name).read_bytes())
    fonts = repo / 'build/android-fonts'
    for name in I.F.REQUIRED_HEADERS:
        write(fonts, 'prefix/include/' + name)
    for name in I.F.REQUIRED_ARCHIVES:
        write(fonts, 'prefix/lib/' + name, ar_library(objects=2))
    for name in I.F.ARTIFACTS:
        write(fonts, name, elf_program(needed=('libc.so', 'libc++_shared.so', 'libm.so')))
    profile, inputs, _, pins = I.F.selected_inputs(repo)
    validation, files = I.F.validate_prefix(fonts, {'cmake_toolchain': repo / 'unused-ndk/build/cmake/android.toolchain.cmake'})
    write(fonts, 'receipt.json', I.B.json_bytes({'schema_version':1, 'kind':I.F.KIND, 'profile':profile,
        'inputs':{'project_files':inputs,'archives':pins}, 'validation':validation, 'prefix_files':files,
        'artifacts':{name:files[name] for name in I.F.ARTIFACTS}}))
    root = repo / 'build/android-icu'
    plan_path = repo / 'tools/native-icu/build-plan.json'
    plan = I.B.read_json(plan_path)
    plan['sources']['LICENSE'] = I.pin(b'complete license')
    plan['sources']['source/data/in/icudt78l.dat'] = I.pin(b'complete locale data')
    for name in I.REQUIRED_HEADERS:
        write(root, 'prefix/include/' + name)
    # Receipt fixture retains the full 203-header contract without source cache.
    for index in range(203 - len(I.REQUIRED_HEADERS)):
        write(root, 'prefix/include/unicode/fixture' + str(index) + '.h')
    headers = {p.relative_to(root/'prefix/include').as_posix(): I.B.fingerprint(p) for p in (root/'prefix/include').rglob('*.h')}
    plan['public_headers'] = {'count':len(headers),'sha256':I.B.sha256(I.B.json_bytes(headers))}
    plan_path.write_bytes(I.B.json_bytes(plan))
    for name, count in [('libicuuc.a',202), ('libicui18n.a',254)]:
        write(root, 'prefix/lib/' + name, ar_library(objects=count))
    write(root, 'prefix/lib/libicudata.a', data_archive())
    for name, content in I.pkg_config_files().items():
        write(root, 'prefix/lib/pkgconfig/' + name, content)
    write(root, 'prefix/share/licenses/wesnoth-icu/LICENSE', b'complete license')
    for name in I.ARTIFACTS:
        write(root, name, (fonts / I.F.ARTIFACTS[1]).read_bytes())
    profile, inputs, plan, pins = I.selected_inputs(repo)
    validation, files = I.validate_prefix(root, plan)
    receipt = {'schema_version':1,'kind':I.KIND,'profile':profile,
        'inputs':{'project_files':inputs,'archives':pins,'prerequisites':I.prior_inputs(repo)},
        'validation':validation,'prefix_files':files,'artifacts':{name:files[name] for name in I.ARTIFACTS}}
    write(root,'receipt.json',I.B.json_bytes(receipt))
    I.save_prefix_state(root)
    return root, receipt


class IcuTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wesnoth ICU tests '); self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo'; self.repo.mkdir()
        self.root = self.repo / 'build/android-icu'

    def write(self, name, content):
        path = self.root / name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(content); return path

    def test_exact_contract_and_existing_prerequisite_scope(self):
        profile, inputs, plan, pins = I.selected_inputs(REPO)
        self.assertEqual(profile, I.B.SELECTED_PROFILE)
        self.assertEqual(len(inputs), 14)
        self.assertEqual(set(pins), {I.ARCHIVE})
        self.assertEqual(plan['source_counts'], {'common':202,'i18n':254})
        self.assertNotIn('tools/build_android_text_layout.py', inputs)

    def test_real_archive_keeps_all_sources_data_and_license(self):
        _,_,plan,pins=I.selected_inputs(REPO)
        archive=REPO/'build/android-icu-sources'/I.ARCHIVE
        if not archive.exists(): self.skipTest('Optional real pinned cache absent')
        data=archive.read_bytes(); self.assertEqual(I.pin(data),pins[I.ARCHIVE])
        files=I.archive_files(data,plan)
        self.assertEqual(len(files),2476)
        self.assertEqual(sum(map(len,files.values())),121155958)
        for name, expected in plan['sources'].items():
            self.assertEqual(I.pin(files['sources/icu/'+name]),expected)

    def test_unsafe_archive_paths_links_duplicates_collisions_fail(self):
        for names, kind in [(['icu/../escape'],tarfile.REGTYPE),(['icu/linked'],tarfile.SYMTYPE),
                            (['icu/a','icu/a'],tarfile.REGTYPE),(['icu/A/file','icu/a/other'],tarfile.REGTYPE),
                            (['other/file'],tarfile.REGTYPE)]:
            with self.subTest(names=names):
                out=io.BytesIO()
                with tarfile.open(fileobj=out,mode='w:gz') as archive:
                    for name in names:
                        item=tarfile.TarInfo(name); item.type=kind; item.linkname='../outside'; item.size=1 if kind==tarfile.REGTYPE else 0
                        archive.addfile(item,io.BytesIO(b'x') if item.size else None)
                with self.assertRaises(I.BuildError): I.archive_files(out.getvalue(),{'sources':{},'source_counts':{}})

    def test_missing_pinned_source_data_blocks(self):
        out=io.BytesIO()
        with tarfile.open(fileobj=out,mode='w:gz'): pass
        with self.assertRaisesRegex(I.BuildError,'differs'):
            I.archive_files(out.getvalue(),{'sources':{'LICENSE':I.pin(b'license')}})

    def test_incbin_native_windows_spaces_and_unsafe_quotes(self):
        assembly=I.data_assembly(PureWindowsPath('D:/Test with spaces/icu/icudt78l.dat')).decode()
        self.assertIn('.incbin "D:/Test with spaces/icu/icudt78l.dat"',assembly)
        self.assertIn('.p2align 4',assembly)
        self.assertIn('.section .note.GNU-stack,"",@progbits',assembly)
        with self.assertRaises(I.BuildError): I.data_assembly(Path('/bad"quote/data'))

    def test_data_symbol_bytes_alignment_flags_name_and_bounds(self):
        p=self.write('libicudata.a',data_archive())
        self.assertEqual(I.data_object_pin(p),I.pin(b'complete locale data'))
        cases=[{'alignment':8},{'flags':3},{'flags':6},{'value':1},{'info':0x12},{'name':b'wrong_symbol'},{'size':10000}]
        for case in cases:
            with self.subTest(case=case):
                p.write_bytes(data_archive(**case))
                with self.assertRaises(I.BuildError): I.data_object_pin(p)
        p.write_bytes(data_archive()[:-7])
        with self.assertRaises(I.BuildError): I.data_object_pin(p)

    def test_receipt_verifies_without_compiler_source_or_subprocess(self):
        root,receipt=create_fixture(self.repo)
        self.assertFalse((self.repo/'build/android-icu-sources').exists())
        with patch.object(I.M,'run',side_effect=AssertionError('must not launch tools')):
            self.assertEqual(I.verify_receipt(self.repo),receipt)

    def test_changed_prior_runtime_and_input_are_rejected(self):
        root,_=create_fixture(self.repo)
        prior=self.repo/'build/android-fonts'/I.ARTIFACTS[1]
        original=prior.read_bytes(); prior.write_bytes(original+b'changed')
        with self.assertRaises(I.BuildError): I.verify_receipt(self.repo)
        prior.write_bytes(original)
        (self.repo/'tools/native-icu/probe.cpp').write_text('changed')
        with self.assertRaises(I.BuildError): I.verify_receipt(self.repo)

    def test_icu_runtime_must_match_verified_font_runtime(self):
        root,receipt=create_fixture(self.repo)
        p=root/I.ARTIFACTS[1]; p.write_bytes(p.read_bytes()+b'changed')
        with self.assertRaisesRegex(I.BuildError,'runtime differs'): I.verify_receipt(self.repo)

    def test_incomplete_data_and_archive_source_count_rejected(self):
        root,_=create_fixture(self.repo); plan=I.selected_inputs(self.repo)[2]
        p=root/'prefix/lib/libicudata.a'; p.write_bytes(data_archive(b'partial data'))
        with self.assertRaisesRegex(I.BuildError,'linked data bytes'): I.validate_prefix(root,plan)
        p.write_bytes(data_archive())
        (root/'prefix/lib/libicuuc.a').write_bytes(ar_library(objects=201))
        with self.assertRaisesRegex(I.BuildError,'complete selected source lists'): I.validate_prefix(root,plan)

    def test_installed_license_and_pc_are_exact(self):
        root,_=create_fixture(self.repo); plan=I.selected_inputs(self.repo)[2]
        p=root/'prefix/share/licenses/wesnoth-icu/LICENSE'; p.write_bytes(b'partial')
        with self.assertRaisesRegex(I.BuildError,'license differs'): I.validate_prefix(root,plan)
        p.write_bytes(b'complete license')
        (root/'prefix/lib/pkgconfig/icu-i18n.pc').write_bytes(b'prefix=/host/path')
        with self.assertRaisesRegex(I.BuildError,'metadata differs'): I.validate_prefix(root,plan)

    def test_owned_sources_and_prefix_refuse_overwrite(self):
        sources={'sources/icu/LICENSE':b'license'}
        marker=I.prepare_sources(self.root,sources,dict(I.B.SELECTED_PROFILE),{})
        self.assertEqual(I.prepare_sources(self.root,sources,dict(I.B.SELECTED_PROFILE),{}),marker)
        (self.root/'sources/icu/LICENSE').write_bytes(b'user edit')
        with self.assertRaisesRegex(I.BuildError,'changed'): I.prepare_sources(self.root,sources,dict(I.B.SELECTED_PROFILE),{})
        self.assertEqual((self.root/'sources/icu/LICENSE').read_bytes(),b'user edit')
        self.write('prefix/local-file',b'user data')
        with self.assertRaisesRegex(I.BuildError,'no owned'): I.verify_prefix(self.root)
        I.save_prefix_state(self.root)
        self.write('prefix/local-file',b'changed')
        with self.assertRaisesRegex(I.BuildError,'changed'): I.verify_prefix(self.root)

    def test_unmanaged_stage_and_symlink_rejected(self):
        self.root.mkdir(parents=True)
        with self.assertRaisesRegex(I.BuildError,'unmanaged'): I.prepare_sources(self.root,{},dict(I.B.SELECTED_PROFILE),{})
        outside=self.repo/'outside'; outside.write_bytes(b'untouched')
        try: (self.root/'prefix').symlink_to(outside)
        except OSError: self.skipTest('Symlinks not supported')
        with self.assertRaises(I.BuildError): I.verify_prefix(self.root)
        self.assertEqual(outside.read_bytes(),b'untouched')

    def test_android_loader_api_and_shared_dependency_constraints(self):
        root,_=create_fixture(self.repo); plan=I.selected_inputs(self.repo)[2]
        for kw in [{'api':24},{'interpreter':'/lib64/ld-linux-x86-64.so.2'},{'needed':('libc.so','libicuuc.so')},{'rpath':True}]:
            with self.subTest(kw=kw):
                (root/I.ARTIFACTS[0]).write_bytes(elf_program(**kw))
                with self.assertRaises(I.BuildError): I.validate_prefix(root,plan)


if __name__=='__main__': unittest.main()
