"""Boost selected-source safety, full headers, Android archives and receipt binding."""
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from test_android_bootstrap import ar_library, elf_program
from test_android_icu import create_fixture as create_icu_fixture

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('android_boost', REPO / 'tools/build_android_boost.py')
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


def fixture(repo):
    create_icu_fixture(repo)
    for name in P.PROJECT_INPUTS:
        path = repo / name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes((REPO / name).read_bytes())
    root = repo / 'build/android-boost'
    def write(name, content):
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
    headers = {}
    for index in range(16010):
        name = 'boost/fixture' + str(index) + '.hpp'
        content = ('header ' + str(index)).encode()
        write('prefix/include/' + name, content); headers[name] = P.pin(content)
    plan_path = repo / 'tools/native-boost/build-plan.json'
    plan = P.B.read_json(plan_path)
    plan['public_headers'] = {'count': len(headers), 'sha256': P.B.sha256(P.B.json_bytes(headers))}
    plan['licenses'] = {'boost': P.pin(b'boost license'), 'bzip2': P.pin(b'bzip license')}
    plan['bzip2_header'] = P.pin(b'bzlib header')
    plan_path.write_bytes(P.B.json_bytes(plan))
    write('prefix/include/bzlib.h', b'bzlib header')
    write('prefix/share/licenses/wesnoth-boost/LICENSE_1_0.txt', b'boost license')
    write('prefix/share/licenses/wesnoth-boost/bzip2-LICENSE', b'bzip license')
    for name,content in P.pkg_config_files().items(): write('prefix/lib/pkgconfig/' + name, content)
    for name,count in P.COUNTS.items(): write('prefix/lib/libboost_' + name + '.a', ar_library(objects=count))
    write('prefix/lib/libbz2.a', ar_library(objects=7))
    write(P.ARTIFACTS[0], elf_program(needed=('libc.so', 'libc++_shared.so', 'libz.so')))
    write(P.ARTIFACTS[1], (repo / 'build/android-icu' / P.I.ARTIFACTS[1]).read_bytes())
    profile, inputs, plan, pins = P.selected_inputs(repo)
    validation, files = P.validate_prefix(root,plan)
    receipt = {'schema_version':1, 'kind':P.KIND, 'profile':profile,
        'inputs':{'project_files':inputs, 'archives':pins, 'prerequisites':P.prior_inputs(repo)},
        'validation':validation, 'prefix_files':files, 'artifacts':{n:files[n] for n in P.ARTIFACTS}}
    write('receipt.json', P.B.json_bytes(receipt)); P.save_prefix_state(root)
    return root, receipt


class BoostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='wesnoth boost tests ')
        cls.repo = Path(cls.temp.name)/'repo'; cls.repo.mkdir()
        cls.root, cls.receipt = fixture(cls.repo)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def change(self, path, content):
        old = path.read_bytes(); self.addCleanup(path.write_bytes, old); path.write_bytes(content)

    def test_exact_recipe_contract(self):
        profile, inputs, plan, pins = P.selected_inputs(REPO)
        self.assertEqual(profile,P.B.SELECTED_PROFILE)
        self.assertEqual(len(inputs),18)
        self.assertEqual(len(plan['libraries']),12)
        self.assertEqual(sum(P.COUNTS.values())+7,93)
        self.assertEqual(set(pins),{P.BOOST_ARCHIVE,P.B.ARCHIVE_NAME})
        self.assertEqual(plan['public_headers']['count'],16010)
        self.assertNotIn('tools/build_android_text_layout.py',inputs)

    def test_recipe_mutation_rejected(self):
        path=self.repo/'tools/native-boost/build-plan.json'; plan=P.B.read_json(path)
        plan['features']['locale']='host-default'
        self.change(path,P.B.json_bytes(plan))
        with self.assertRaisesRegex(P.BuildError,'recipe'):P.selected_inputs(self.repo)

    def test_selected_members_include_complete_headers_only_needed_sources(self):
        for name in ['boost/locale.hpp','boost/regex/v5/regex.hpp','libs/locale/src/icu/conversion.cpp',
                     'libs/filesystem/config/has_stat_st_mtim.cpp','libs/context/build/Jamfile.v2','LICENSE_1_0.txt']:
            self.assertTrue(P.selected_member(name),name)
        for name in ['doc/html/deep_unused.html','libs/locale/test/test.cpp','libs/unrelated/src/a.cpp']:
            self.assertFalse(P.selected_member(name),name)

    def test_unsafe_selected_archive_members_rejected(self):
        cases=[(['boost_1_88_0/boost/../outside'],tarfile.REGTYPE),
               (['boost_1_88_0/boost/link'],tarfile.SYMTYPE),
               (['boost_1_88_0/boost/a','boost_1_88_0/boost/a'],tarfile.REGTYPE),
               (['boost_1_88_0/boost/A/x','boost_1_88_0/boost/a/y'],tarfile.REGTYPE),
               (['wrong/boost/a'],tarfile.REGTYPE)]
        for names,kind in cases:
            with self.subTest(names=names):
                stream=io.BytesIO()
                with tarfile.open(fileobj=stream,mode='w:bz2') as tar:
                    for name in names:
                        item=tarfile.TarInfo(name);item.type=kind;item.linkname='../outside';item.size=1 if kind==tarfile.REGTYPE else 0
                        tar.addfile(item,io.BytesIO(b'x') if item.size else None)
                with self.assertRaises(P.BuildError):P.archive_files(stream.getvalue(),{})

    def test_missing_selected_source_inventory_rejected(self):
        stream=io.BytesIO()
        with tarfile.open(fileobj=stream,mode='w:bz2'):pass
        with self.assertRaisesRegex(P.BuildError,'inventory differs'):
            P.archive_files(stream.getvalue(),{'source_inventory':{'count':1,'sha256':'0'*64}})

    def test_receipt_verifies_without_compiler_source_or_subprocess(self):
        self.assertFalse((self.repo/'build/android-icu-sources').exists())
        with patch.object(P.M,'run',side_effect=AssertionError('must not launch tools')):
            self.assertEqual(P.verify_receipt(self.repo),self.receipt)

    def test_changed_prior_icu_artifact_rejected(self):
        path=self.repo/'build/android-icu'/P.I.ARTIFACTS[0]
        self.change(path,path.read_bytes()+b'changed')
        with self.assertRaises(P.BuildError):P.verify_receipt(self.repo)

    def test_changed_current_project_input_rejected(self):
        path=self.repo/'tools/native-boost/probe.cpp'
        self.change(path,path.read_bytes()+b'//changed')
        with self.assertRaisesRegex(P.BuildError,'receipt'):P.verify_receipt(self.repo)

    def test_changed_header_or_missing_object_rejected(self):
        plan=P.selected_inputs(self.repo)[2]
        path=self.root/'prefix/include/boost/fixture0.hpp'; old=path.read_bytes()
        self.change(path,b'changed')
        with self.assertRaisesRegex(P.BuildError,'headers'):P.validate_prefix(self.root,plan)
        path.write_bytes(old)
        self.change(self.root/'prefix/lib/libboost_context.a',ar_library(objects=4))
        with self.assertRaisesRegex(P.BuildError,'source lists'):P.validate_prefix(self.root,plan)

    def test_license_and_nonrelocatable_metadata_rejected(self):
        plan=P.selected_inputs(self.repo)[2]
        path=self.root/'prefix/share/licenses/wesnoth-boost/LICENSE_1_0.txt'; old=path.read_bytes()
        self.change(path,b'partial')
        with self.assertRaisesRegex(P.BuildError,'license'):P.validate_prefix(self.root,plan)
        path.write_bytes(old)
        self.change(self.root/'prefix/lib/pkgconfig/wesnoth-boost.pc',b'prefix=/host/path')
        with self.assertRaisesRegex(P.BuildError,'metadata'):P.validate_prefix(self.root,plan)

    def test_runtime_must_match_verified_icu(self):
        path=self.root/P.ARTIFACTS[1]
        self.change(path,path.read_bytes()+b'changed')
        with self.assertRaisesRegex(P.BuildError,'runtime differs'):P.verify_receipt(self.repo)

    def test_android_loader_api_and_dependencies(self):
        plan=P.selected_inputs(self.repo)[2]
        path=self.root/P.ARTIFACTS[0]
        for options in [{'interpreter':'/host/loader'}, {'api':24}, {'needed':('libc.so','libc++_shared.so','libz.so','libicuuc.so')}]:
            with self.subTest(options=options):
                old=path.read_bytes();path.write_bytes(elf_program(**options))
                try:
                    with self.assertRaises(P.BuildError):P.validate_prefix(self.root,plan)
                finally:path.write_bytes(old)

    def test_owned_sources_and_prefix_are_not_overwritten(self):
        with tempfile.TemporaryDirectory(prefix='boost owned ') as tmp:
            root=Path(tmp)/'stage';files={'sources/boost_1_88_0/LICENSE_1_0.txt':b'license'}
            marker=P.prepare_sources(root,files,P.B.SELECTED_PROFILE,{})
            self.assertEqual(P.prepare_sources(root,files,P.B.SELECTED_PROFILE,{}),marker)
            path=root/'sources/boost_1_88_0/LICENSE_1_0.txt';path.write_bytes(b'user change')
            with self.assertRaisesRegex(P.BuildError,'changed'):P.prepare_sources(root,files,P.B.SELECTED_PROFILE,{})
            self.assertEqual(path.read_bytes(),b'user change')
            (root/'prefix').mkdir();(root/'prefix/user-file').write_bytes(b'user file')
            with self.assertRaisesRegex(P.BuildError,'no owned'):P.verify_prefix(root)
            P.save_prefix_state(root);(root/'prefix/user-file').write_bytes(b'changed')
            with self.assertRaisesRegex(P.BuildError,'changed'):P.verify_prefix(root)

    def test_unmanaged_stage_and_symlink_refused(self):
        with tempfile.TemporaryDirectory(prefix='boost unsafe ') as tmp:
            root=Path(tmp)/'stage';root.mkdir()
            with self.assertRaisesRegex(P.BuildError,'unmanaged'):P.prepare_sources(root,{},P.B.SELECTED_PROFILE,{})
            outside=Path(tmp)/'outside';outside.write_bytes(b'unchanged')
            try:(root/'prefix').symlink_to(outside)
            except OSError:self.skipTest('Symlinks not available')
            with self.assertRaises(P.BuildError):P.verify_prefix(root)
            self.assertEqual(outside.read_bytes(),b'unchanged')


if __name__=='__main__':unittest.main()
