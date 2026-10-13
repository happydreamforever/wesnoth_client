"""Inspect Android ELF bytes and Media receipts without native build tools."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('engine_artifacts', REPO / 'tools/android_engine_artifacts.py')
A = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(A)


def elf(*, executable=False, api=23, needed=('libc.so',), symbol='SDL_main',
        symbol_info=0x12, symbol_visibility=0, symbol_section=3, symbol_value=0x500,
        extra_dynamic=()):
    """Small independent ELF64 fixture with a real dynamic symbol/string table."""
    data = bytearray(2048)
    data[:7] = b'\x7fELF\x02\x01\x01'
    struct.pack_into('<HHIQQQIHHHHHH', data, 16, 3, 62, 1, 0, 64, 0x700, 0,
                     64, 56, 4 if executable else 3, 64, 4, 0)
    strings = bytearray(b'\0')
    library_offsets = []
    for name in needed:
        library_offsets.append(len(strings))
        strings.extend(name.encode('ascii') + b'\0')
    symbol_offset = len(strings)
    strings.extend(symbol.encode('ascii') + b'\0')
    data[0x300:0x300 + len(strings)] = strings
    dynamic = [(5, 0x300), (10, len(strings)), (6, 0x400), (11, 24)]
    dynamic += [(1, index) for index in library_offsets] + list(extra_dynamic) + [(0, 0)]
    for index, pair in enumerate(dynamic):
        struct.pack_into('<qQ', data, 0x200 + index * 16, *pair)
    struct.pack_into('<IBBHQQ', data, 0x418, symbol_offset, symbol_info,
                     symbol_visibility, symbol_section, symbol_value, 4)
    data[0x500:0x504] = b'\x31\xc0\xc3\x90'
    struct.pack_into('<III8sI', data, 0x600, 8, 4, 1, b'Android\0', api)
    segments = [(1, 5, 0, 0, 0, len(data), len(data), 0x1000),
                (2, 4, 0x200, 0x200, 0x200, len(dynamic) * 16, len(dynamic) * 16, 8),
                (4, 4, 0x600, 0x600, 0x600, 24, 24, 4)]
    if executable:
        interp = b'/system/bin/linker64\0'
        data[0x640:0x640 + len(interp)] = interp
        segments.append((3, 4, 0x640, 0x640, 0x640, len(interp), len(interp), 1))
    for index, segment in enumerate(segments):
        struct.pack_into('<IIQQQQQQ', data, 64 + 56 * index, *segment)
    sections = [(0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
                (0, 3, 2, 0x300, 0x300, len(strings), 0, 0, 1, 0),
                (0, 11, 2, 0x400, 0x400, 48, 1, 1, 8, 24),
                (0, 1, 6, 0x500, 0x500, 4, 0, 0, 16, 0)]
    for index, section in enumerate(sections):
        struct.pack_into('<IIQQQQIIQQ', data, 0x700 + 64 * index, *section)
    return data


class ElfArtifactTests(unittest.TestCase):
    def rejects(self, data, **options):
        with self.assertRaises(A.BuildError):
            A.inspect_elf(data, **options)

    def test_valid_shared_executable_runtime_and_metadata(self):
        value = A.inspect_elf(elf(needed=('libSDL3.so', 'libc.so')), required_exports=('SDL_main',))
        self.assertEqual(value, {'elf_class': 64, 'machine': 'x86_64', 'android_api_notes': [23],
                                'interpreter': None, 'needed': ['libSDL3.so', 'libc.so'],
                                'host_rpath': False, 'exports': ['SDL_main']})
        self.assertEqual(A.inspect_elf(elf(executable=True), executable=True)['interpreter'],
                         '/system/bin/linker64')
        self.assertEqual(A.inspect_elf(elf(api=21), runtime=True)['android_api_notes'], [21])
        self.rejects(elf(api=21))
        self.rejects(elf(), runtime=True)
        self.rejects(elf(api=21), runtime=True, required_exports=('SDL_main',))

    def test_header_and_table_boundaries(self):
        for source in (b'', bytes(63), b'ELF' * 100):
            self.rejects(source)
        for offset, fmt, value in ((4, 'B', 1), (5, 'B', 2), (16, 'H', 2), (18, 'H', 183),
                                   (20, 'I', 2), (32, 'Q', 2040), (40, 'Q', 2030),
                                   (52, 'H', 63), (54, 'H', 55), (56, 'H', 0),
                                   (56, 'H', 257), (58, 'H', 63), (62, 'H', 4)):
            with self.subTest(offset=offset, value=value):
                data = elf()
                struct.pack_into('<' + fmt, data, offset, value)
                self.rejects(data)

    def test_program_mapping_and_note_boundaries(self):
        for offset, value in ((64 + 8, 2040), (64 + 32, 2049), (64 + 40, 1),
                              (64 + 48, 3), (64 + 16, 1), (120 + 16, 2040),
                              (120 + 32, 95), (176 + 32, 23)):
            with self.subTest(offset=offset):
                data = elf()
                struct.pack_into('<Q', data, offset, value)
                self.rejects(data)
        for offset, value in ((0x600, 0xffffffff), (0x604, 0xffffffff), (0x614, 24)):
            with self.subTest(offset=offset):
                data = elf()
                struct.pack_into('<I', data, offset, value)
                self.rejects(data)

    def test_dynamic_termination_strings_and_dependencies(self):
        for entries in (((29, 0),), ((15, 0),), ((10, 20),)):
            self.rejects(elf(extra_dynamic=entries))
        for names in (('libhost.so',), ('libc.so', 'libc.so'), ('/usr/lib/libc.so',)):
            self.rejects(elf(needed=names))
        for offset, value in ((0x200 + 8, 2047), (0x210 + 8, 0), (0x240 + 8, 2048)):
            data = elf()
            struct.pack_into('<Q', data, offset, value)
            self.rejects(data)
        data = elf()
        struct.pack_into('<qQ', data, 0x250, 1, 1)
        self.rejects(data)  # No DT_NULL in the bounded table.
        data = elf(extra_dynamic=((0, 0), (1, 1)))
        self.rejects(data)  # Unexpected meaningful records after DT_NULL.
        data = elf()
        data[0x300] = 65
        self.rejects(data)

    def test_interpreter_profile(self):
        self.rejects(elf(executable=True))
        self.rejects(elf(), executable=True)
        data = elf(executable=True)
        data[0x640] = ord('X')
        self.rejects(data, executable=True)
        data = elf(executable=True)
        data[0x642] = 0
        self.rejects(data, executable=True)

    def test_exports_require_defined_global_default_function(self):
        for info, visibility, section, value in ((0x02, 0, 3, 0x500), (0x22, 0, 3, 0x500),
                                                (0x11, 0, 3, 0x500), (0x12, 2, 3, 0x500),
                                                (0x12, 3, 3, 0x500), (0x12, 0, 0, 0),
                                                (0x12, 0, 4, 0x500), (0x12, 0, 3, 2050)):
            with self.subTest(info=info, visibility=visibility, section=section, value=value):
                self.rejects(elf(symbol_info=info, symbol_visibility=visibility,
                                 symbol_section=section, symbol_value=value), required_exports=('SDL_main',))
        data = elf()
        struct.pack_into('<I', data, 68, 4)  # Function bytes in a non-executable segment.
        self.rejects(data, required_exports=('SDL_main',))

    def test_export_string_decoy_missing_sections_and_inconsistent_tables(self):
        data = elf(symbol='unrelated')
        data[0x580:0x589] = b'SDL_main\0'
        self.rejects(data, required_exports=('SDL_main',))
        for offset, fmt, value in ((40, 'Q', 0), (0x200 + 56, 'Q', 16),
                                   (0x780 + 32, 'Q', 47), (0x780 + 32, 'Q', 2048),
                                   (0x780 + 24, 'Q', 0x410), (0x780 + 40, 'I', 0),
                                   (0x418, 'I', 2048)):
            data = elf()
            struct.pack_into('<' + fmt, data, offset, value)
            self.rejects(data, required_exports=('SDL_main',))
        data = elf()
        struct.pack_into('<Q', data, 40, 0)
        struct.pack_into('<H', data, 60, 0)
        self.assertEqual(A.inspect_elf(data)['exports'], [])
        self.rejects(data, required_exports=('SDL_main',))

    def test_export_argument_names(self):
        for names in ('SDL_main', b'SDL_main', ('SDL-main',), (12,), ('main\0',)):
            self.rejects(elf(), required_exports=names)


@unittest.skipUnless((REPO / 'build/android-media/receipt.json').is_file(), 'Requires completed native Media fixture.')
class ExistingMediaArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='wesnoth engine artifacts ')
        cls.repo = Path(cls.temp.name) / 'repo'
        cls.repo.mkdir()
        for name in A.M.PROJECT_INPUTS:
            target = cls.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / name, target)
        cls.root = cls.repo / 'build/android-media'
        cls.root.mkdir(parents=True)
        shutil.copytree(REPO / 'build/android-media/prefix', cls.root / 'prefix')
        shutil.copy2(REPO / 'build/android-media/receipt.json', cls.root / 'receipt.json')
        cls.original_inventory = A.M.inventory(REPO / 'build/android-media')
        cls.original_receipt = (REPO / 'build/android-media/receipt.json').read_bytes()

    @classmethod
    def tearDownClass(cls):
        try:
            assert A.M.inventory(REPO / 'build/android-media') == cls.original_inventory
            assert (REPO / 'build/android-media/receipt.json').read_bytes() == cls.original_receipt
        finally:
            cls.temp.cleanup()

    @contextlib.contextmanager
    def changed(self, path, data):
        path = Path(path)
        original = path.read_bytes()
        try:
            path.write_bytes(data)
            yield
        finally:
            path.write_bytes(original)

    def checked(self, repo=None):
        with patch.object(A.M, 'run', side_effect=AssertionError('Must not start build tools')), \
                patch.object(A.M, 'archive_data', side_effect=AssertionError('Must not read source caches')), \
                patch.object(A.B, 'ndk_paths', side_effect=AssertionError('Must not need the NDK')), \
                patch.object(subprocess, 'run', side_effect=AssertionError('Must not launch a process')), \
                contextlib.redirect_stdout(io.StringIO()):
            return A.verify_media(repo or self.repo)

    def test_actual_existing_media_verifies_without_tools_cache_or_mutation(self):
        self.assertEqual(self.checked(REPO), json.loads(self.original_receipt))
        self.assertEqual(len(self.original_inventory), 188)

    def test_actual_sdl_export_and_runtime_match_saved_receipt(self):
        result = A.inspect_elf((self.root / 'prefix/lib/libSDL3.so').read_bytes(),
                               required_exports=('SDL_GetVersion',))
        self.assertEqual(result['exports'], ['SDL_GetVersion'])
        self.assertIn('libandroid.so', result['needed'])
        with self.assertRaises(A.BuildError):
            A.inspect_elf((self.root / 'prefix/lib/libSDL3.so').read_bytes(), required_exports=('SDL_main',))
        runtime = A.inspect_elf((self.root / 'prefix/lib/libc++_shared.so').read_bytes(), runtime=True)
        self.assertEqual(runtime['android_api_notes'], [21])

    def test_actual_network_executable(self):
        path = REPO / 'build/android-network/prefix/bin/wesnoth-network-check'
        if not path.is_file():
            self.skipTest('Requires completed native Network fixture.')
        before = A.B.fingerprint(path)
        result = A.inspect_elf(path.read_bytes(), executable=True)
        self.assertEqual(result['needed'], ['libz.so', 'libdl.so', 'libm.so', 'libc++_shared.so', 'libc.so'])
        self.assertEqual(before, A.B.fingerprint(path))

    def test_copied_media_receipt_binds_project_bytes_and_all_prefix_files(self):
        path = self.repo / A.M.PROJECT_INPUTS[0]
        with self.changed(path, path.read_bytes() + b'\n# Changed input\n'), self.assertRaises(A.BuildError):
            self.checked()
        path = self.root / 'prefix/include/SDL3/SDL.h'
        with self.changed(path, path.read_bytes() + b'\n/* Changed header */\n'), self.assertRaises(A.BuildError):
            self.checked()
        path = self.root / 'prefix/share/extra.txt'
        path.write_text('extra')
        try:
            with self.assertRaises(A.BuildError):
                self.checked()
        finally:
            path.unlink()

    def test_copied_receipt_cannot_replace_actual_elf_or_static_validation(self):
        path = self.root / 'receipt.json'
        receipt = json.loads(path.read_bytes())
        receipt['validation']['runtime']['prefix/lib/libSDL3.so']['android_api'] = 24
        with self.changed(path, json.dumps(receipt).encode()), self.assertRaises(A.BuildError):
            self.checked()
        path = self.root / 'prefix/lib/libSDL3.so'
        bad = bytearray(path.read_bytes())
        struct.pack_into('<H', bad, 18, 183)
        with self.changed(path, bad), self.assertRaises(A.BuildError):
            self.checked()
        path = self.root / 'prefix/lib/libpng16.a'
        with self.changed(path, b'not an archive'), self.assertRaises(A.BuildError):
            self.checked()

    def test_unexpected_shared_libraries_empty_files_and_absolute_metadata_rejected(self):
        extra = self.root / 'prefix/lib/libunexpected.so'
        extra.write_bytes(elf())
        try:
            with self.assertRaises(A.BuildError):
                self.checked()
        finally:
            extra.unlink()
        header = self.root / 'prefix/include/SDL3/SDL.h'
        with self.changed(header, b''), self.assertRaises(A.BuildError):
            self.checked()
        metadata = next((self.root / 'prefix').rglob('*.pc'))
        with self.changed(metadata, metadata.read_bytes() + b'\nhost=/home/fixture\n'), self.assertRaises(A.BuildError):
            self.checked()

    @unittest.skipIf(sys.platform == 'win32', 'Symlink creation requires extra Windows privileges.')
    def test_symlink_paths_are_rejected_and_outside_file_preserved(self):
        outside = self.repo / 'outside'
        outside.write_bytes(b'preserve')
        link = self.root / 'prefix/include/outside-link'
        link.symlink_to(outside)
        try:
            with self.assertRaises(A.BuildError):
                self.checked()
            self.assertEqual(outside.read_bytes(), b'preserve')
        finally:
            link.unlink()
        alias = self.repo.parent / 'repo-alias'
        alias.symlink_to(self.repo, target_is_directory=True)
        try:
            with self.assertRaises(A.BuildError):
                self.checked(alias)
        finally:
            alias.unlink()

    def test_helper_imports_under_isolated_native_python(self):
        source = ('import importlib.util; '
                  's=importlib.util.spec_from_file_location("engine_artifacts",' + repr(str(REPO / 'tools/android_engine_artifacts.py')) + '); '
                  'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
                  'assert m.BuildError is m.B.BuildError; '
                  'print(m.inspect_elf(open(' + repr(str(self.root / 'prefix/lib/libSDL3.so')) + ',"rb").read())["machine"])')
        completed = subprocess.run([sys.executable, '-I', '-B', '-c', source], check=True,
                                   capture_output=True, text=True)
        self.assertEqual(completed.stdout.strip(), 'x86_64')


if __name__ == '__main__':
    unittest.main()
