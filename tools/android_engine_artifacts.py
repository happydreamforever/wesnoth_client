#!/usr/bin/env python3
"""Read-only Android engine artifact checks, without build tools or source caches.

ELF layouts follow the System V ABI. Required entrypoints are checked in the
dynamic symbol table, not by searching arbitrary binary strings. The selected
NDK produces section tables; export validation rejects a sectionless artifact
rather than guessing the dynamic symbol count.
"""
import importlib.util
import os
from pathlib import Path
import re
import stat
import struct
import sys

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('wesnoth_engine_media', REPO / 'tools/build_android_media.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
B = M.B
BuildError = B.BuildError

ALLOWED_NEEDED = frozenset({
    'libmain.so', 'libSDL3.so', 'libSDL3_image.so', 'libSDL3_mixer.so',
    'libc++_shared.so', 'libc.so', 'libm.so', 'libdl.so', 'liblog.so',
    'libandroid.so', 'libGLESv1_CM.so', 'libGLESv2.so', 'libEGL.so',
    'libOpenSLES.so', 'libz.so',
})


def inspect_elf(data, executable=False, required_exports=(), runtime=False):
    """Validate the selected Android ELF profile and requested function exports.

    Runtime mode is reserved for the existing NDK libc++_shared.so (API21).
    Every other artifact must carry exactly one Android API23 note. Metadata
    retains dependency order and reports only the explicitly required exports.
    """
    if not isinstance(data, (bytes, bytearray)) or len(data) < 64:
        raise BuildError('Android artifact must contain a complete ELF64 header.')
    if data[:7] != b'\x7fELF\x02\x01\x01':
        raise BuildError('Android artifact must be little-endian ELF64 version 1.')
    header = struct.unpack_from('<HHIQQQIHHHHHH', data, 16)
    kind, machine, version, entry, phoff, shoff, _, ehsize, phsize, phcount, shsize, shcount, shstrings = header
    if (kind, machine, version, ehsize) != (3, 62, 1, 64):
        raise BuildError('Android artifact must be x86_64 ELF64 ET_DYN.')
    if runtime and (executable or required_exports):
        raise BuildError('Runtime verification cannot request an executable or entrypoints.')
    if (isinstance(required_exports, (str, bytes))
            or any(not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name)
                   for name in required_exports)):
        raise BuildError('Required ELF exports must be function names.')
    required_exports = set(required_exports)

    def bounds(offset, size, label):
        if offset < 0 or size < 0 or offset > len(data) or size > len(data) - offset:
            raise BuildError('ELF ' + label + ' extends beyond the artifact.')

    if phoff < 64 or phsize != 56 or not 1 <= phcount <= 256:
        raise BuildError('Invalid ELF program-header table.')
    bounds(phoff, phsize * phcount, 'program-header table')
    segments = []
    for index in range(phcount):
        record = struct.unpack_from('<IIQQQQQQ', data, phoff + index * phsize)
        tag, flags, offset, virtual, _, size, memory, alignment = record
        bounds(offset, size, 'segment')
        if virtual + memory > 2 ** 64 or (tag == 1 and memory < size):
            raise BuildError('Invalid ELF loadable segment size.')
        if alignment not in (0, 1) and (alignment & (alignment - 1)):
            raise BuildError('Invalid ELF segment alignment.')
        if tag == 1 and alignment > 1 and virtual % alignment != offset % alignment:
            raise BuildError('ELF loadable segment alignment differs from its offset.')
        segments.append((tag, flags, offset, virtual, size, memory))
    loads = [record for record in segments if record[0] == 1]
    if not loads:
        raise BuildError('ELF has no loadable segments.')

    def address(virtual, size, label):
        matches = [offset + virtual - start for tag, flags, offset, start, length, memory in loads
                   if start <= virtual and size <= length and virtual - start <= length - size]
        if len(matches) != 1:
            raise BuildError('ELF ' + label + ' is outside an unambiguous file-backed loadable segment.')
        bounds(matches[0], size, label)
        return matches[0]

    interpreters = []
    notes = []
    dynamics = []
    for tag, flags, offset, virtual, size, memory in segments:
        if tag == 3:
            value = data[offset:offset + size]
            if not value or not value.endswith(b'\0') or b'\0' in value[:-1]:
                raise BuildError('Invalid ELF interpreter string.')
            try:
                interpreters.append(value[:-1].decode('ascii'))
            except UnicodeDecodeError as error:
                raise BuildError('Invalid ELF interpreter encoding.') from error
        elif tag == 2:
            if not size or size % 16 or address(virtual, size, 'dynamic table') != offset:
                raise BuildError('Invalid ELF dynamic table size or mapping.')
            table = []
            terminated = False
            for position in range(offset, offset + size, 16):
                key, value = struct.unpack_from('<qQ', data, position)
                if terminated:
                    if key or value:
                        raise BuildError('ELF dynamic table has data after its terminator.')
                elif key == 0:
                    terminated = True
                else:
                    table.append((key, value))
            if not terminated:
                raise BuildError('ELF dynamic table has no terminator.')
            dynamics.append(table)
        elif tag == 4:
            position, end = offset, offset + size
            while position < end:
                if end - position < 12:
                    raise BuildError('Truncated ELF note header.')
                namesz, descsz, note_type = struct.unpack_from('<III', data, position)
                position += 12
                name_size, description_size = (namesz + 3) & ~3, (descsz + 3) & ~3
                if name_size + description_size > end - position:
                    raise BuildError('Invalid ELF note length.')
                name = data[position:position + namesz]
                position += name_size
                if name == b'Android\0' and note_type == 1:
                    if descsz < 4:
                        raise BuildError('Android ELF note lacks its API level.')
                    notes.append(struct.unpack_from('<I', data, position)[0])
                position += description_size
    if interpreters != (['/system/bin/linker64'] if executable else []):
        raise BuildError('ELF interpreter does not match the executable/shared-library profile.')
    if notes != ([21] if runtime else [23]):
        raise BuildError('ELF Android API note does not match the selected profile: ' + str(notes))
    if len(dynamics) != 1:
        raise BuildError('ELF must contain one dynamic table.')
    dynamic = dynamics[0]
    if any(tag in (15, 29) for tag, value in dynamic):
        raise BuildError('Android artifact contains RPATH/RUNPATH.')

    def unique(tag, label):
        values = [value for key, value in dynamic if key == tag]
        if len(values) != 1:
            raise BuildError('ELF must have one ' + label + '.')
        return values[0]

    strings_size = unique(10, 'dynamic string-table size')
    if not strings_size:
        raise BuildError('ELF dynamic string table is empty.')
    strings_offset = address(unique(5, 'dynamic string table'), strings_size, 'dynamic strings')
    strings = data[strings_offset:strings_offset + strings_size]
    if strings[0] != 0 or strings[-1] != 0:
        raise BuildError('ELF dynamic string table lacks its NUL boundaries.')

    def string(index):
        if index >= len(strings):
            raise BuildError('ELF string index is outside the dynamic string table.')
        end = strings.find(b'\0', index)
        if end < 0:
            raise BuildError('ELF dynamic string is not terminated.')
        return bytes(strings[index:end])

    needed = []
    for tag, value in dynamic:
        if tag == 1:
            try:
                needed.append(string(value).decode('ascii'))
            except UnicodeDecodeError as error:
                raise BuildError('ELF needed-library name is not ASCII.') from error
    if set(needed) - ALLOWED_NEEDED or len(set(needed)) != len(needed):
        raise BuildError('Unexpected or duplicate Android dynamic dependencies: ' + repr(needed))

    sections = []
    if shoff or shcount:
        if shoff < 64 or shsize != 64 or not shcount or shstrings >= shcount:
            raise BuildError('Invalid ELF section-header table.')
        bounds(shoff, shsize * shcount, 'section-header table')
        for index in range(shcount):
            section = struct.unpack_from('<IIQQQQIIQQ', data, shoff + index * shsize)
            _, section_type, _, virtual, offset, size, link, _, alignment, entsize = section
            if section_type != 8:  # SHT_NOBITS has no bytes in the file.
                bounds(offset, size, 'section')
            if alignment not in (0, 1) and alignment & (alignment - 1):
                raise BuildError('Invalid ELF section alignment.')
            sections.append(section)
    found = set()
    if required_exports:
        tables = [section for section in sections if section[1] == 11]  # SHT_DYNSYM
        if len(tables) != 1 or unique(11, 'dynamic symbol entry size') != 24:
            raise BuildError('Required exports need one ELF dynamic symbol table with 24-byte entries.')
        _, _, _, virtual, offset, size, link, _, _, entsize = tables[0]
        if (entsize != 24 or not size or size % 24 or link >= len(sections)
                or sections[link][1] != 3 or sections[link][4:6] != (strings_offset, strings_size)
                or virtual != unique(6, 'dynamic symbol address')
                or address(virtual, size, 'dynamic symbols') != offset):
            raise BuildError('ELF dynamic symbol table/string mapping is inconsistent.')
        for position in range(offset, offset + size, 24):
            name_index, info, other, section_index, value, symbol_size = struct.unpack_from('<IBBHQQ', data, position)
            name = string(name_index)
            if section_index < 0xff00 and section_index >= len(sections):
                raise BuildError('ELF dynamic symbol references an invalid section.')
            if name not in {item.encode('ascii') for item in required_exports}:
                continue
            if info != 0x12 or other != 0 or not 0 < section_index < 0xff00:
                raise BuildError('Required ELF export is not a defined global/default function: ' + name.decode('ascii'))
            address(value, max(symbol_size, 1), 'exported function')
            if not any(flags & 1 and start <= value and max(symbol_size, 1) <= length
                       and value - start <= length - max(symbol_size, 1)
                       for tag, flags, file_offset, start, length, memory in loads):
                raise BuildError('Required ELF function is outside executable loadable data.')
            decoded = name.decode('ascii')
            if decoded in found:
                raise BuildError('Required ELF function has duplicate dynamic definitions.')
            found.add(decoded)
        if found != required_exports:
            raise BuildError('Missing required ELF function exports: ' + ', '.join(sorted(required_exports - found)))
    return {'elf_class': 64, 'machine': 'x86_64', 'android_api_notes': notes,
            'interpreter': interpreters[0] if interpreters else None,
            'needed': needed, 'host_rpath': False, 'exports': sorted(found)}


def _regular_path(path):
    path = Path(os.path.abspath(path))
    for component in (*reversed(path.parents), path):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise BuildError('Media verification rejects links/reparse points: ' + str(component))
        if component != path and not stat.S_ISDIR(info.st_mode):
            raise BuildError('Media path has a non-directory ancestor: ' + str(component))
    return path


def verify_media(repo):
    """Verify the existing complete Media receipt using bytes and saved inputs."""
    repo = Path(repo)
    for name in M.PROJECT_INPUTS:
        _regular_path(repo / name)
    profile, inputs, _, pins = M.selected_inputs(repo)
    root = _regular_path(repo / 'build/android-media')
    receipt = B.read_json(B.require_file(_regular_path(root / 'receipt.json'), 'Media receipt'))
    prefix = _regular_path(root / 'prefix')
    pending = [prefix]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for child in entries:
                info = child.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                    raise BuildError('Media prefix contains a link/reparse point: ' + child.path)
                if stat.S_ISDIR(info.st_mode):
                    pending.append(Path(child.path))
                elif not stat.S_ISREG(info.st_mode):
                    raise BuildError('Media prefix contains a non-regular file: ' + child.path)
    for name in M.REQUIRED_HEADERS:
        B.require_file(prefix / 'include' / name, 'Media header')
    for name in M.REQUIRED_ARCHIVES:
        B.require_file(prefix / 'lib' / name, 'Media archive')
    static = {path.relative_to(prefix).as_posix(): M.inspect_static_archive(path)
              for path in sorted(prefix.rglob('*.a'))}
    dynamic = {}
    for name in M.ARTIFACTS:
        runtime = name.endswith('/libc++_shared.so')
        artifact = B.require_file(root / name, 'Media runtime artifact')
        result = inspect_elf(artifact.read_bytes(), executable=name == M.ARTIFACTS[0], runtime=runtime)
        if 'libmain.so' in result['needed']:
            raise BuildError('Existing Media library must not depend on the new engine.')
        if name == M.ARTIFACTS[0] and not {'libSDL3.so', 'libSDL3_image.so', 'libSDL3_mixer.so',
                                         'libc++_shared.so', 'libc.so'}.issubset(result['needed']):
            raise BuildError('Media executable lacks its expected runtime dependencies.')
        # Retain the original receipt format produced by Media's readelf checks.
        dynamic[name] = ({key: value for key, value in result.items() if key != 'exports'} if runtime else
                         {'machine': 'x86_64', 'android_api': 23, 'needed': result['needed'], 'host_rpath': False})
    files = M.inventory(root)
    shared = {name for name in files if name.endswith('.so') or '.so.' in name}
    if shared != set(M.ARTIFACTS[1:]) or any(record['bytes'] == 0 for record in files.values()):
        raise BuildError('Media prefix contains unexpected shared libraries or empty files.')
    for name in files:
        if name.endswith(('.pc', '.cmake')):
            text = (root / name).read_text(encoding='utf-8')
            if re.search(r'\b[A-Za-z]:[/\\]|/(?:home|workspace|tmp|opt)/', text):
                raise BuildError('Media metadata contains a build-host path: ' + name)
    if (not isinstance(receipt, dict) or receipt.get('schema_version') != 1 or receipt.get('kind') != M.KIND
            or receipt.get('profile') != profile or receipt.get('inputs') != {'project_files': inputs, 'archives': pins}
            or receipt.get('prefix_files') != files or receipt.get('artifacts') != {name: files[name] for name in M.ARTIFACTS}
            or receipt.get('validation') != {'static_libraries': static, 'runtime': dynamic}):
        raise BuildError('Media receipt/input/artifact hashes or validation differ. Preserve prior files.')
    print('ANDROID MEDIA ARTIFACTS VERIFIED', flush=True)
    return receipt
