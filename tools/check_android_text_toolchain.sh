#!/usr/bin/env bash
# Read-only tool inspection plus an owned, temporary MSYS/native-NDK build.
set -u
set -o pipefail

blocked=0
core_ready=1
capture=''
capture_status=0
declare -A commands

# Inspect the installed tools, without importing modules from the caller or
# writing bytecode into their installation directories.
unset PYTHONPATH PYTHONHOME
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1

info() { printf '[INFO] %s\n' "$*"; }
fail() { printf '[BLOCKED] %s\n' "$*"; blocked=$((blocked + 1)); }
first_line() { printf '%s' "${1%%$'\n'*}"; }
details() {
    local line count=0
    while IFS= read -r line; do
        printf '[INFO] %.400s\n' "$line"
        count=$((count + 1))
        if ((count == 12)); then break; fi
    done <<< "${capture:0:2000}"
}

if [[ $# -ne 2 ]]; then
    printf 'Usage: %s NDK_ROOT EMPTY_SCRATCH_ROOT\n' "$0" >&2
    exit 2
fi
if ! timeout_command=$(command -v timeout); then
    printf '[BLOCKED] GNU timeout is required for bounded tool inspection.\n'
    exit 2
fi

bounded() {
    capture=$("$timeout_command" --signal=TERM --kill-after=2s "$1" "${@:2}" 2>&1)
    capture_status=$?
    # Native Windows tools may emit CRLF while MSYS tools emit LF.
    capture=${capture//$'\r\n'/$'\n'}
    capture=${capture%$'\r'}
    return "$capture_status"
}

host=$(uname -s)
case "$host" in
    MSYS*|MINGW*) host_tag=windows-x86_64; suffix=.exe; msys=1 ;;
    Linux*) host_tag=linux-x86_64; suffix=''; msys=0 ;;
    *) printf '[BLOCKED] Unsupported diagnostic host: %s\n' "$host"; exit 2 ;;
esac
info "Host=$host; Bash=$BASH_VERSION; MSYSTEM=${MSYSTEM-unset}; NDK host=$host_tag"

to_posix() {
    if ((msys)); then cygpath -u -- "$1"; else printf '%s\n' "$1"; fi
}
to_native() {
    if ((msys)); then cygpath -m -- "$1"; else printf '%s\n' "$1"; fi
}
if ((msys)) && ! command -v cygpath >/dev/null; then
    fail 'MSYS cygpath is required for native Windows tool paths.'
    exit 2
fi
ndk=$(to_posix "$1") || exit 2
scratch=$(to_posix "$2") || exit 2
# Make receives generated recipes. Reject its metacharacters instead of
# treating a supplied path as Make or shell program text. Spaces are supported.
check_paths() {
    local path
    for path in "$ndk" "$scratch"; do
        if [[ "$path" == *[$'\n\r\t\x24\x60\x23\x22\x27;&<>|*?!']* ]]; then
            fail 'Diagnostic paths contain unsupported Make/shell metacharacters.'
            exit 2
        fi
    done
}
check_paths
if [[ ! -d "$scratch" || -L "$scratch" ]]; then
    fail 'Scratch root must be an existing empty directory, not a link.'
    exit 2
fi
shopt -s nullglob dotglob
entries=("$scratch"/*)
shopt -u nullglob dotglob
if ((${#entries[@]})); then
    fail 'Scratch root contains existing files. Nothing changed.'
    exit 2
fi
scratch=$(cd -- "$scratch" && pwd -P) || exit 2
if [[ -d "$ndk" ]]; then ndk=$(cd -- "$ndk" && pwd -P) || exit 2; fi
check_paths

check_tool() {
    local name=$1 requirement=$2 selected output
    shift 2
    if ((msys)); then
        selected=/usr/bin/$name
        if [[ ! -f "$selected" && ! -f "$selected.exe" ]]; then selected=''; fi
    else
        selected=$(command -v "$name" || true)
    fi
    if [[ -z "$selected" ]]; then
        if [[ "$requirement" == core ]]; then
            fail "Missing core tool: $name"
            core_ready=0
        else
            printf '[OPTIONAL] %s is missing; release-source builds do not require regeneration.\n' "$name"
        fi
        return
    fi
    commands[$name]=$selected
    if bounded 15s "$selected" "$@"; then
        output=$(first_line "$capture")
        if [[ -n "$output" ]]; then
            printf '[PASS] %s: %s; %s\n' "$name" "$selected" "$output"
            return
        fi
        capture_status=2
        capture='tool returned no version output'
    fi
    if [[ "$requirement" == core ]]; then
        fail "$name probe failed (exit $capture_status): $(first_line "$capture")"
        core_ready=0
    else
        printf '[OPTIONAL] %s probe failed (exit %s): %s\n' "$name" "$capture_status" "$(first_line "$capture")"
    fi
}

check_tool make core --version
check_tool pkg-config core --version
check_tool python3 core --version
check_tool meson core --version
meson_version=$(first_line "$capture")
check_tool ninja core --version
check_tool gperf core --version
check_tool patch core --version
check_tool perl core -e 'printf "Perl %vd; platform=%s; executable=%s\n", $^V, $^O, $^X;'
for name in autoconf automake aclocal m4 libtoolize; do check_tool "$name" optional --version; done

if [[ -n "${commands[python3]-}" && -n "${commands[meson]-}" ]]; then
    if bounded 15s "${commands[python3]}" -I -B -c '
import os, pathlib, shlex, shutil, sys
import mesonbuild, mesonbuild.coredata
launcher = pathlib.Path(sys.argv[1])
first = launcher.read_text(encoding="utf-8").splitlines()[0]
if not first.startswith("#!"):
    raise SystemExit("Meson launcher has no inspectable Python shebang")
parts = shlex.split(first[2:])
if pathlib.Path(parts[0]).name == "env":
    if len(parts) != 2 or parts[1].startswith("-"):
        raise SystemExit("Unsupported Meson env shebang")
    interpreter = shutil.which(parts[1])
else:
    interpreter = parts[0]
if not interpreter or os.path.realpath(interpreter) != os.path.realpath(sys.executable):
    raise SystemExit("Meson shebang and selected Python resolve to different interpreters")
if mesonbuild.coredata.version != sys.argv[2]:
    raise SystemExit("Meson executable and selected Python module report different versions")
if sys.argv[3] == "1" and sys.platform not in ("cygwin", "msys"):
    raise SystemExit("Selected Python is not the MSYS interpreter")
print("Python=" + sys.executable + "; platform=" + sys.platform)
print("Meson module=" + str(pathlib.Path(mesonbuild.__file__).resolve()) + "; version=" + mesonbuild.coredata.version)
print("Meson launcher/interpreter binding verified")
' "${commands[meson]}" "$meson_version" "$msys"; then
        while IFS= read -r line; do printf '[PASS] %s\n' "$line"; done <<< "$capture"
    else
        fail "Meson/Python binding failed (exit $capture_status): $(first_line "$capture")"
        details
        core_ready=0
    fi
fi

revision=''
revision_count=0
if [[ -f "$ndk/source.properties" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        line=${line%$'\r'}
        if [[ "$line" =~ ^[[:space:]]*Pkg\.Revision[[:space:]]*=[[:space:]]*([^[:space:]]+)[[:space:]]*$ ]]; then
            revision=${BASH_REMATCH[1]}
            revision_count=$((revision_count + 1))
        fi
    done < "$ndk/source.properties"
fi
if [[ "$revision" != 26.3.11579264 || $revision_count -ne 1 ]]; then
    fail "Expected NDK revision 26.3.11579264; found '${revision:-missing or malformed}'."
    core_ready=0
else
    printf '[PASS] NDK revision=%s; root=%s\n' "$revision" "$ndk"
fi
ndk_bin=$ndk/toolchains/llvm/prebuilt/$host_tag/bin
clang=$ndk_bin/clang$suffix
archiver=$ndk_bin/llvm-ar$suffix
readelf=$ndk_bin/llvm-readelf$suffix
for path in "$clang" "$archiver" "$readelf"; do
    if [[ ! -f "$path" || ! -s "$path" ]]; then fail "Missing NDK tool: $path"; core_ready=0; fi
done
if [[ -f "$clang" ]] && bounded 15s "$clang" --version && [[ -n "$capture" ]]; then
    printf '[PASS] NDK compiler: %s\n' "$(first_line "$capture")"
else
    fail "NDK compiler could not run (exit $capture_status): $(first_line "$capture")"
    core_ready=0
fi

if ((core_ready)); then
    work=$scratch/'ndk bridge'
    dependency=$work/'dependency with spaces'
    if ! mkdir -p -- "$dependency/include" "$dependency/lib/pkgconfig"; then
        fail 'Could not create owned compiler-boundary scratch files.'
        core_ready=0
    else
        if ! dependency_native=$(to_native "$dependency") ||
           ! source_native=$(to_native "$work/probe.c") ||
           ! object_native=$(to_native "$work/probe.o") ||
           ! archive_native=$(to_native "$work/probe.a"); then
            fail 'Could not convert owned scratch paths for the native compiler.'
            exit 2
        fi
        if ! cat > "$dependency/include/wesnoth_text_bridge.h" <<'HEADER'
#define WESNOTH_TEXT_BRIDGE_VALUE 73
HEADER
        then fail 'Could not write the owned probe header.'; exit 2; fi
        if ! cat > "$work/probe.c" <<'SOURCE'
#include <wesnoth_text_bridge.h>
#if !defined(__ANDROID__) || !defined(__x86_64__) || __ANDROID_API__ != 23
#error Expected the selected Android x86_64/API23 target
#endif
int wesnoth_text_bridge(void) { return WESNOTH_TEXT_BRIDGE_VALUE; }
SOURCE
        then fail 'Could not write the owned probe source.'; exit 2; fi
        if ! cat > "$dependency/lib/pkgconfig/wesnoth-text-bridge.pc" <<PC
prefix=$dependency_native
includedir=\${prefix}/include
Name: wesnoth-text-bridge
Description: Owned temporary compiler-boundary input
Version: 1.0
Cflags: -I"\${includedir}"
PC
        then fail 'Could not write the owned pkg-config input.'; exit 2; fi
        # Isolate lookup and compiler flags from any Windows/MSYS host packages.
        unset CFLAGS CXXFLAGS CPPFLAGS LDFLAGS CPATH C_INCLUDE_PATH CPLUS_INCLUDE_PATH LIBRARY_PATH INCLUDE LIB
        unset MAKEFLAGS GNUMAKEFLAGS MFLAGS MAKEFILES MAKEOVERRIDES MSYS2_ARG_CONV_EXCL MSYS_NO_PATHCONV
        export PKG_CONFIG_PATH='' PKG_CONFIG_SYSROOT_DIR=''
        export PKG_CONFIG_LIBDIR="$dependency/lib/pkgconfig"
        if bounded 15s "${commands[pkg-config]}" --cflags wesnoth-text-bridge && [[ -n "$capture" ]]; then
            info "Isolated pkg-config flags: $capture"
            # Keep pkg-config's shell escaping intact inside the Make recipe.
            # Never split its output into a shell array or evaluate it with eval.
            if ! cat > "$work/Makefile" <<MAKEFILE
SHELL := /usr/bin/bash
.PHONY: all
all:
	"$clang" --target=x86_64-linux-android23 $capture -c "$source_native" -o "$object_native"
	"$archiver" rcs "$archive_native" "$object_native"
MAKEFILE
            then fail 'Could not write the owned Makefile.'; exit 2; fi
            if bounded 30s "${commands[make]}" --no-print-directory -C "$work" -f Makefile all; then
                if [[ ! -s "$work/probe.o" || ! -s "$work/probe.a" ]]; then
                    fail 'Make returned success without both native output files.'
                elif ! bounded 15s "$archiver" t "$archive_native" || [[ "$capture" != probe.o ]]; then
                    fail "NDK archive does not contain exactly probe.o (exit $capture_status): $(first_line "$capture")"
                elif bounded 15s "$readelf" --file-header "$archive_native"; then
                    if [[ "$capture" == *'Class:'*ELF64* && "$capture" == *'Type:'*REL* && "$capture" == *'Machine:'*'X86-64'* ]]; then
                        printf '[PASS] MSYS/Make/pkg-config -> NDK compile/archive: Android x86_64 ELF64 REL; include path contains spaces.\n'
                    else
                        fail "Unexpected compiler-boundary ELF identity: $(first_line "$capture")"
                    fi
                else
                    fail "NDK llvm-readelf failed (exit $capture_status): $(first_line "$capture")"
                fi
            else
                fail "Make/native NDK compiler boundary failed (exit $capture_status): $(first_line "$capture")"
                details
            fi
        else
            fail "Isolated pkg-config lookup failed (exit $capture_status): $(first_line "$capture")"
        fi
    fi
else
    info 'Compiler-boundary build skipped because a required tool or interpreter check failed.'
fi

info 'This checks tool availability and a temporary compile boundary; no Autoconf/Meson package build, APK or Android runtime is proven.'
if ((blocked)); then
    printf 'ANDROID TEXT TOOLCHAIN CHECK BLOCKED: %s required checks failed.\n' "$blocked"
    exit 2
fi
printf 'ANDROID TEXT TOOLCHAIN CHECK PASSED\n'
