#!/usr/bin/env bash
# Generation only: native Python/CMake/Ninja compile all target objects later.
set -euo pipefail
export PATH=/usr/bin:/bin
unset BASH_ENV ENV PERL5LIB PERL5OPT PERLLIB MAKEFLAGS MFLAGS
ndk=$1
work=$2
recipe=$3
jobs=$4
if [[ $ndk == ?:* ]]; then ndk=$(/usr/bin/cygpath -u "$ndk"); fi
if [[ $work == ?:* ]]; then work=$(/usr/bin/cygpath -u "$work"); fi
if [[ $recipe == ?:* ]]; then recipe=$(/usr/bin/cygpath -u "$recipe"); fi
host=linux-x86_64
case "$(uname -s)" in MSYS*|MINGW*|CYGWIN*) host=windows-x86_64;; esac
export ANDROID_NDK_ROOT=$ndk
export PATH="$ndk/toolchains/llvm/prebuilt/$host/bin:$PATH"
export SOURCE_DATE_EPOCH=1738886400
cd "$work"
/usr/bin/perl --version
/usr/bin/make --version
/usr/bin/perl ./Configure android-x86_64 -D__ANDROID_API__=23 no-shared no-module no-tests no-apps no-asm no-engine no-fips no-autoload-config no-dso --prefix=/wesnoth-android-network --openssldir=/wesnoth-unused-ssl --libdir=lib
/usr/bin/perl -I. "$recipe/export-openssl.pl" --generated > generated-targets.txt
mapfile -t generated_targets < generated-targets.txt
/usr/bin/make -j"$jobs" build_generated "${generated_targets[@]}"
/usr/bin/perl -I. "$recipe/export-openssl.pl" > portable-library-graph.json
printf '%s\n' 'OPENSSL GENERATION COMPLETE (no target compilation)'
