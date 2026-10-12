# Windows PowerShell 5.1. Read-only prerequisite checks; writes only a diagnostics report.
[CmdletBinding()]
param(
    [string]$MsysRoot = 'D:\Test\msys64',
    [string]$SdkRoot = (Join-Path $env:LOCALAPPDATA 'Android\Sdk'),
    [string]$JavaHome = 'C:\Program Files\Android\Android Studio\jbr',
    [string]$GradleUserHome = (Join-Path $env:USERPROFILE '.gradle'),
    [string[]]$ArchiveDirectories = @()
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$engine = Join-Path $repoRoot 'engine'
$blocked = 0
$finished = $false
$report = $null

function Write-Result([string]$Level, [string]$Message) {
    if ($Level -eq 'BLOCKED') { $script:blocked++ }
    $line = '[{0}] {1}' -f $Level, $Message
    Write-Host $line
    if ($script:report) { Add-Content -LiteralPath $script:report -Value $line -Encoding UTF8 }
}
function Test-Nonempty([string]$Path) {
    return ((Test-Path -LiteralPath $Path -PathType Leaf) -and (Get-Item -LiteralPath $Path -Force).Length -gt 0)
}
function Require-File([string]$Label, [string]$Path) {
    if (Test-Nonempty $Path) { Write-Result PASS "$Label exists: $Path"; return $true }
    Write-Result BLOCKED "$Label missing or empty: $Path"
    return $false
}
function Invoke-Probe([string]$Path, [string[]]$Arguments) {
    # Explicit argv quoting avoids PowerShell 5.1 stripping Bash's embedded quotes.
    # Capture stderr separately because java -version normally writes there.
    $process = New-Object System.Diagnostics.Process
    try {
        $process.StartInfo.FileName = $Path
        $process.StartInfo.Arguments = (($Arguments | ForEach-Object {
            $escaped = $_ -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1'
            '"' + $escaped + '"'
        }) -join ' ')
        $process.StartInfo.UseShellExecute = $false
        $process.StartInfo.CreateNoWindow = $true
        $process.StartInfo.RedirectStandardOutput = $true
        $process.StartInfo.RedirectStandardError = $true
        $null = $process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(15000)) {
            $process.Kill()
            return @{ Code = -1; Text = 'Version probe timed out after 15 seconds.' }
        }
        if (-not $stdout.Wait(2000) -or -not $stderr.Wait(2000)) { return @{ Code = -1; Text = 'Version probe output did not close.' } }
        return @{ Code = $process.ExitCode; Text = ($stdout.Result + "`n" + $stderr.Result).Trim() }
    } catch { return @{ Code = -1; Text = $_.Exception.Message } }
    finally { $process.Dispose() }
}
function Invoke-Section([string]$Name, [scriptblock]$Action) {
    Write-Result INFO $Name
    try { & $Action } catch { Write-Result BLOCKED ($Name + ': ' + $_.Exception.Message) }
}
try {
    $logRoot = Join-Path $repoRoot 'diagnostics'
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    $report = Join-Path $logRoot ('android-prerequisites-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.txt')
    $profile = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'android-build-profile.json') -Raw | ConvertFrom-Json
    if ($profile.schema_version -ne 1 -or $profile.abi -ne 'x86_64' -or $profile.min_sdk -ne 23) { throw 'Unexpected Android build profile.' }
    $NdkVersion = [string]$profile.ndk_version; $GradleVersion = [string]$profile.gradle_version
    $AgpVersion = [string]$profile.agp_version; $SconsVersion = [string]$profile.scons_version
    Write-Result INFO ('Android prerequisites 2026-10-12.1; collected ' + (Get-Date -Format o))
    Write-Result INFO "Client=$repoRoot; MSYS2=$MsysRoot; SDK=$SdkRoot"
    Write-Result INFO "Target=x86_64; minimum Android API=23; device verified separately as API28."
    Write-Result INFO "Selected NDK=$NdkVersion; Gradle=$GradleVersion; AGP=$AgpVersion; SCons=$SconsVersion."
    Write-Result INFO 'No downloads, builds, ADB calls, Git changes, .env reads, database or game launches.'
    Write-Result INFO 'PASS means this named check passed; it does not establish offline APK build readiness.'
    if ($env:OS -ne 'Windows_NT') { throw 'Run this helper in Windows PowerShell on the Windows build computer.' }

    Invoke-Section 'Restored engine content (SHA256; executable mode ignored)' {
        $manifestPath = Join-Path $engine 'source-manifest.json'
        if ((Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash -ne 'fa2a240b93b994c756ff96d444c0a93416557c17d57ff60af1bd85b5c88080d1') {
            throw 'Source manifest SHA256 differs from the pinned restoration; preserve it and report this result.'
        }
        $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        if ($manifest.schema_version -ne 1 -or @($manifest.files).Count -ne 1591 -or
            $manifest.upstream.commit -ne '75c84d2ed344b954d1fd308e588dfe13aef53b20') {
            throw 'Unexpected source manifest schema/count/revision; preserve source and report this result.'
        }
        $rootPrefix = [IO.Path]::GetFullPath($engine) + [IO.Path]::DirectorySeparatorChar
        $verifiedSources = 0; $differences = 0
        foreach ($entry in $manifest.files) {
            $relative = [string]$entry.path
            $full = [IO.Path]::GetFullPath((Join-Path $engine $relative))
            if (-not $full.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase) -or
                $relative -match '(^|[/\\])\.env($|\.)' -or $entry.sha256 -notmatch '^[0-9a-f]{64}$') {
                throw 'Unsafe or malformed source manifest entry; scan stopped.'
            }
            $reason = $null
            if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { $reason = 'missing' }
            elseif ((Get-Item -LiteralPath $full -Force).Length -ne $entry.bytes) { $reason = 'size differs' }
            elseif ((Get-FileHash -LiteralPath $full -Algorithm SHA256).Hash -ne $entry.sha256) { $reason = 'SHA256 differs' }
            if ($reason) {
                $differences++
                if ($differences -le 30) { Write-Result INFO "SOURCE DIFFERENCE: $relative ($reason)" }
            } else { $verifiedSources++ }
        }
        if ($differences) { Write-Result BLOCKED "Source matches=$verifiedSources; differences=$differences (first 30 listed). Preserve local edits; do not reset or restore files." }
        else { Write-Result PASS "All $verifiedSources restored source files match their manifest bytes and SHA256. This does not inspect Git mode/staged changes." }
    }

    Invoke-Section 'JDK selected for Gradle' {
        foreach ($tool in @('java', 'javac')) {
            $exe = Join-Path $JavaHome "bin\$tool.exe"
            if (Require-File $tool $exe) {
                $result = Invoke-Probe $exe @('-version')
                $versionPattern = '(?:version\s+"?|javac\s+)' + [regex]::Escape([string]$profile.java_major) + '\.'
                if ($result.Code -eq 0 -and $result.Text -match $versionPattern) {
                    Write-Result PASS ($tool + ': ' + ($result.Text -split "`n")[0])
                } else { Write-Result BLOCKED "$tool did not report usable JDK$($profile.java_major); exit=$($result.Code); $($result.Text)" }
            }
        }
    }

    Invoke-Section 'Android SDK and selected Windows NDK' {
        foreach ($item in @("platforms\android-$($profile.compile_sdk)\android.jar", "build-tools\$($profile.build_tools)\aapt2.exe",
                "build-tools\$($profile.build_tools)\zipalign.exe", "build-tools\$($profile.build_tools)\apksigner.bat")) {
            $null = Require-File 'SDK input' (Join-Path $SdkRoot $item)
        }
        $ndk = Join-Path $SdkRoot "ndk\$NdkVersion"
        $properties = Join-Path $ndk 'source.properties'
        if (Require-File 'NDK metadata' $properties) {
            $revision = [regex]::Match((Get-Content -LiteralPath $properties -Raw), '(?m)^Pkg\.Revision\s*=\s*(\S+)').Groups[1].Value
            if ($revision -eq $NdkVersion) { Write-Result PASS "NDK package revision=$revision" }
            else { Write-Result BLOCKED "NDK revision mismatch: selected=$NdkVersion; metadata=$revision" }
        }
        $bin = Join-Path $ndk 'toolchains\llvm\prebuilt\windows-x86_64\bin'
        foreach ($tool in @('clang.exe', 'clang++.exe', 'llvm-ar.exe', 'llvm-strip.exe',
                'x86_64-linux-android23-clang.cmd', 'x86_64-linux-android23-clang++.cmd')) {
            $null = Require-File 'NDK tool' (Join-Path $bin $tool)
        }
        $clang = Join-Path $bin 'clang.exe'
        if (Test-Nonempty $clang) {
            $result = Invoke-Probe $clang @('--version')
            if ($result.Code -eq 0 -and $result.Text -match 'clang version') { Write-Result PASS (($result.Text -split "`n")[0]) }
            else { Write-Result BLOCKED "NDK compiler cannot run; exit=$($result.Code); $($result.Text)" }
        }
        $null = Require-File 'x86_64 Android C++ runtime' (Join-Path $ndk 'toolchains\llvm\prebuilt\windows-x86_64\sysroot\usr\lib\x86_64-linux-android\libc++_shared.so')
    }

    Invoke-Section 'MSYS2 native dependency build tools and Python modules' {
        $bash = Join-Path $MsysRoot 'usr\bin\bash.exe'
        if (Require-File 'MSYS2 Bash' $bash) {
            # Fixed command text only: no parameter, path or version is interpolated into shell code.
            $script = @'
export PATH=/usr/bin:/ucrt64/bin:/mingw64/bin
for tool in make patch tar gzip bzip2 xz autoconf automake libtoolize pkg-config perl meson ninja gcc g++; do
  if command -v "$tool" >/dev/null 2>&1; then printf 'TOOL_PRESENT %s %s\n' "$tool" "$(command -v "$tool")"; else printf 'TOOL_MISSING %s\n' "$tool"; fi
done
if test -x /usr/bin/python3; then
  /usr/bin/python3 -I -c 'import sys; print("MSYS_PYTHON " + sys.version.split()[0])'
  /usr/bin/python3 -I -c 'import SCons; print("SCONS_VERSION " + SCons.__version__)' 2>/dev/null || printf 'SCONS_MISSING\n'
else printf 'MSYS_PYTHON_MISSING\n'; fi
'@
            $result = Invoke-Probe $bash @('--noprofile', '--norc', '-c', $script)
            if ($result.Code -ne 0) { Write-Result BLOCKED "MSYS2 probe failed: exit=$($result.Code)" }
            if ([regex]::Matches($result.Text, '(?m)^TOOL_(?:PRESENT|MISSING) ').Count -ne 15 -or
                $result.Text -notmatch '(?m)^MSYS_PYTHON[ _]' -or
                $result.Text -notmatch '(?m)^(?:SCONS_VERSION |SCONS_MISSING|MSYS_PYTHON_MISSING)') {
                Write-Result BLOCKED 'MSYS2 probe output is incomplete; exit zero alone does not prove success.'
            }
            foreach ($line in ($result.Text -split "`r?`n")) {
                if ($line -match '^(TOOL_MISSING|SCONS_MISSING|MSYS_PYTHON_MISSING)') { Write-Result BLOCKED $line }
                elseif ($line -match '^SCONS_VERSION (.+)$') {
                    if ($Matches[1] -eq $SconsVersion) { Write-Result PASS $line }
                    else { Write-Result BLOCKED "$line; selected=$SconsVersion" }
                } elseif ($line) { Write-Result INFO $line }
            }
            Write-Result INFO 'Tool paths are presence checks; native dependency cross-compilation is not tested.'
        }
    }

    Invoke-Section 'Gradle distribution and selected cached artifacts (presence only)' {
        $distribution = Join-Path $GradleUserHome "wrapper\dists\gradle-$GradleVersion-bin"
        $launchers = @()
        if (Test-Path -LiteralPath $distribution -PathType Container) {
            $launchers = @(Get-ChildItem -LiteralPath $distribution -Directory | ForEach-Object {
                $candidate = Join-Path $_.FullName "gradle-$GradleVersion\lib\gradle-launcher-$GradleVersion.jar"
                if (Test-Nonempty $candidate) { $candidate }
            })
        }
        if ($launchers.Count) { Write-Result PASS "Gradle $GradleVersion extracted launcher exists: $($launchers -join ', ')" }
        else { Write-Result BLOCKED "No nonempty Gradle $GradleVersion extracted launcher under $distribution" }
        $artifacts = @(
            @{ Group='com.android.tools.build'; Name='gradle'; Version=$AgpVersion; Extension='jar' },
            @{ Group='androidx.core'; Name='core'; Version='1.16.0'; Extension='aar' },
            @{ Group='androidx.documentfile'; Name='documentfile'; Version='1.1.0'; Extension='aar' }
        )
        foreach ($artifact in $artifacts) {
            $relative = 'caches\modules-2\files-2.1\{0}\{1}\{2}' -f $artifact.Group, $artifact.Name, $artifact.Version
            $cache = Join-Path $GradleUserHome $relative
            $filename = '{0}-{1}.{2}' -f $artifact.Name, $artifact.Version, $artifact.Extension
            $found = @()
            if (Test-Path -LiteralPath $cache -PathType Container) {
                $found = @(Get-ChildItem -LiteralPath $cache -Directory | Where-Object { Test-Nonempty (Join-Path $_.FullName $filename) })
            }
            if ($found.Count) { Write-Result PASS "Cached artifact file exists: $filename" }
            else { Write-Result BLOCKED "Cached artifact missing/empty: $filename in $cache" }
        }
        Write-Result INFO 'No Gradle execution: transitive dependencies, cache checksums and offline resolution remain unverified.'
    }

    Invoke-Section 'Native archive integrity against the recorded VPS copies' {
        $roots = @($ArchiveDirectories) + @(
            (Join-Path $repoRoot 'packaging\android\download'),
            (Join-Path $repoRoot 'packaging\android\downloads'),
            (Join-Path $engine 'packaging\android\download'),
            (Join-Path $engine 'packaging\android\downloads'),
            (Join-Path $MsysRoot 'tmp\android-dl')
        )
        $roots = @($roots | Where-Object { $_ } | Select-Object -Unique)
        $sourceScript = Join-Path $engine 'packaging\android\build-android-dependencies\build-android-deps.sh'
        $urls = @([regex]::Matches((Get-Content -LiteralPath $sourceScript -Raw), '(?m)^https://[^\s]+$') | ForEach-Object { $_.Value })
        if ($urls.Count -ne 23) { throw 'Unexpected dependency archive list; expected 23 upstream URLs.' }
        $archiveManifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'android-native-archives.json') -Raw | ConvertFrom-Json
        if ($archiveManifest.schema_version -ne 1 -or @($archiveManifest.archives).Count -ne 23) { throw 'Unexpected native archive manifest.' }
        Write-Result INFO ('Archive search directories: ' + ($roots -join '; '))
        $verified = 0
        foreach ($archive in $archiveManifest.archives) {
            $name = [string]$archive.name
            if ($name -match '[/\\:]' -or $name -match '^\.' -or -not $name -or
                $archive.sha256 -notmatch '^[0-9a-f]{64}$' -or $archive.bytes -le 0 -or $urls -notcontains $archive.url) {
                throw 'Unsafe or unexpected native archive manifest entry.'
            }
            $match = $false; $seen = $false
            foreach ($root in $roots) {
                $candidate = Join-Path $root $name
                if (-not (Test-Nonempty $candidate)) { continue }
                $seen = $true
                if ((Get-Item -LiteralPath $candidate -Force).Length -eq $archive.bytes -and
                    (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash -eq $archive.sha256) { $match = $true; break }
                Write-Result INFO "ARCHIVE DIFFERENCE: $candidate"
            }
            if ($match) { $verified++ }
            elseif ($seen) { Write-Result BLOCKED "ARCHIVE SIZE/SHA256 MISMATCH: $name; preserve the file and report this result." }
            else { Write-Result BLOCKED "ARCHIVE MISSING/EMPTY: $name" }
        }
        $level = 'INFO'; if ($verified -eq 23) { $level = 'PASS' }
        Write-Result $level "Native archives with matching size/SHA256=$verified/23."
        Write-Result INFO 'These hashes identify the recorded VPS copies; they are not publisher signature verification or an Android dependency build test.'
    }
    $finished = $true
} catch {
    Write-Result BLOCKED ('Check interrupted or failed: ' + $_.Exception.Message)
} finally {
    Write-Result INFO "Check complete=$finished; BLOCKED entries=$blocked."
    Write-Result INFO 'Windows Android build integration is still pending. This helper does not produce or validate an APK.'
    if ($report) { Write-Host "Report saved to: $report" }
}
if (-not $finished -or $blocked -gt 0) { exit 2 }
exit 0
