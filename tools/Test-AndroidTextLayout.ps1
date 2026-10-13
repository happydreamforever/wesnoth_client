# Windows PowerShell 5.1. Verify paragraph layout prerequisites and selected fonts, then run its native paragraph probe.
[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._:-]*$')][string]$Serial = '127.0.0.1:5555',
    [string]$AdbPath = (Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'),
    [string]$PythonPath = (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\python.exe'),
    [string]$DataRoot = ''
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$testExit = 2

function Require-RegularFile([string]$Root, [string]$Relative) {
    $file = Join-Path $Root $Relative
    if (-not (Test-Path -LiteralPath $file -PathType Leaf) -or (Get-Item -LiteralPath $file -Force).Length -eq 0) {
        throw "Required input is missing or empty: $file"
    }
    # Do not follow an artifact or input redirected outside its recorded tree.
    $cursor = $file
    while ($cursor.Length -ge $Root.Length) {
        if ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Symbolic links/reparse points are not accepted for native test inputs: $cursor"
        }
        if ($cursor -eq $Root) { break }
        $cursor = Split-Path $cursor -Parent
    }
    return $file
}
function Test-RecordedFiles([string]$Root, $Records, [string[]]$Names) {
    $actual = @($Records.PSObject.Properties.Name | Sort-Object)
    if (($actual -join '|') -cne (($Names | Sort-Object) -join '|')) { throw 'Native receipt has unexpected or missing file entries.' }
    foreach ($relative in $Names) {
        $entry = $Records.$relative
        if ($entry.bytes -le 0 -or $entry.sha256 -notmatch '^[0-9a-f]{64}$') { throw "Malformed native receipt entry: $relative" }
        $file = Require-RegularFile $Root $relative
        if ((Get-Item -LiteralPath $file -Force).Length -ne $entry.bytes -or
            (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $entry.sha256) {
            throw "Selected font input differs from its recorded hash: $relative. Keep the files and send the output."
        }
    }
}
function Invoke-Captured([string]$Executable, [string[]]$Arguments) {
    $process = New-Object System.Diagnostics.Process
    try {
        $process.StartInfo.FileName = $Executable
        $process.StartInfo.Arguments = (($Arguments | ForEach-Object {
            $escaped = $_ -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1'
            '"' + $escaped + '"'
        }) -join ' ')
        $process.StartInfo.WorkingDirectory = $repoRoot
        $process.StartInfo.UseShellExecute = $false
        $process.StartInfo.CreateNoWindow = $true
        $process.StartInfo.RedirectStandardOutput = $true
        $process.StartInfo.RedirectStandardError = $true
        $null = $process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(60000)) { $process.Kill(); throw 'The selected verification/ADB command timed out after 60 seconds.' }
        if (-not $stdout.Wait(2000) -or -not $stderr.Wait(2000)) { throw 'Command output did not close after exit.' }
        $output = ($stdout.Result + "`n" + $stderr.Result).Trim()
        if ($output) { Write-Host $output }
        return @{ Code = $process.ExitCode; Text = $output; Stdout = $stdout.Result.Trim() }
    } finally { $process.Dispose() }
}
function Invoke-Adb([string[]]$Arguments) {
    return Invoke-Captured $AdbPath (@('-H', 'localhost', '-P', '5037') + $Arguments)
}
function Invoke-SelectedAdb([string[]]$Arguments) {
    $result = Invoke-Adb (@('-s', $Serial) + $Arguments)
    if ($result.Code -ne 0) { throw "ADB command failed for $Serial with exit $($result.Code). Send all output." }
    return $result
}
try {
    if ($env:OS -ne 'Windows_NT') { throw 'Run this helper on the Windows computer with LDPlayer open.' }
    # Windows PowerShell can bind defaults before PSScriptRoot is available.
    # Resolve the optional sibling path in the script body and keep overrides.
    if ([string]::IsNullOrEmpty($DataRoot)) {
        $DataRoot = Join-Path (Split-Path $repoRoot -Parent) 'gamedata-1.19.26'
    }
    $verifier = Join-Path $PSScriptRoot 'build_android_text_layout.py'
    foreach ($file in @($PythonPath, $AdbPath, $verifier)) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf) -or (Get-Item -LiteralPath $file -Force).Length -eq 0) {
            throw "Required verification tool is missing or empty: $file"
        }
    }
    # The builder owns this shared, read-only receipt/profile/source/prefix/ELF
    # verification, including all completed prerequisite prefixes. It runs
    # with native Python and needs no MSYS, NDK or cache.
    $verified = Invoke-Captured $PythonPath @('-I', '-B', '-u', $verifier, '--verify')
    if ($verified.Code -ne 0 -or $verified.Stdout -notmatch '(?m)^ANDROID TEXT LAYOUT ARTIFACTS VERIFIED\r?$') {
        throw 'Paragraph layout artifact verification failed or did not confirm completion. Nothing was sent to Android.'
    }
    $artifact = Require-RegularFile $repoRoot 'build/android-text-layout/prefix/bin/wesnoth-text-layout-check'
    $runtime = Require-RegularFile $repoRoot 'build/android-text-layout/prefix/lib/libc++_shared.so'
    $assetsPath = Require-RegularFile $repoRoot 'tools/native-fonts/assets.json'
    $assets = Get-Content -LiteralPath $assetsPath -Raw | ConvertFrom-Json
    if ($assets.schema_version -ne 1 -or $assets.game_data_version -ne '1.19.26') {
        throw 'Font assets do not match the selected game-data version.'
    }
    if (-not (Test-Path -LiteralPath $DataRoot -PathType Container)) { throw "Game-data folder is missing: $DataRoot" }
    $DataRoot = (Get-Item -LiteralPath $DataRoot -Force).FullName
    $fontFiles = @('fonts/Lato-Regular.ttf', 'fonts/DejaVuSans.ttf', 'fonts/COPYING')
    Test-RecordedFiles $DataRoot $assets.files $fontFiles
    Write-Host 'PASS: both selected fonts and their COPYING notice match the recorded game-data hashes.'
    if ($Serial -match '^127\.0\.0\.1:[0-9]+$') {
        Write-Host "Connecting SDK ADB to the selected local endpoint: $Serial"
        $connection = Invoke-Adb @('connect', $Serial)
        if ($connection.Code -ne 0) { throw 'ADB connect failed. Keep LDPlayer running and send the output.' }
    }
    $state = Invoke-SelectedAdb @('get-state')
    if ($state.Stdout -ne 'device') { throw "Selected device is not ready: $Serial" }
    $properties = @{}
    foreach ($property in @('sys.boot_completed', 'ro.build.version.sdk', 'ro.product.cpu.abi')) {
        Write-Host "DEVICE PROPERTY: $property"
        $properties[$property] = (Invoke-SelectedAdb @('shell', 'getprop', $property)).Stdout
    }
    $api = 0
    if ($properties['sys.boot_completed'] -ne '1' -or
        -not [int]::TryParse($properties['ro.build.version.sdk'], [ref]$api) -or $api -lt 23 -or
        $properties['ro.product.cpu.abi'] -ne 'x86_64') {
        throw 'The selected device must finish booting, run Android API23 or later, and have primary ABI x86_64.'
    }
    # Fixed remote names and this GUID are the only remote-shell argument values.
    $remote = '/data/local/tmp/wesnoth-text-layout-check/' + [guid]::NewGuid().ToString('N')
    Write-Host "Paragraph layout test directory: $remote"
    $null = Invoke-SelectedAdb @('shell', 'mkdir', '-p', $remote)
    $null = Invoke-SelectedAdb @('push', $artifact, "$remote/wesnoth-text-layout-check")
    $null = Invoke-SelectedAdb @('push', $runtime, "$remote/libc++_shared.so")
    foreach ($relative in $fontFiles) {
        $null = Invoke-SelectedAdb @('push', (Join-Path $DataRoot $relative), ($remote + '/' + [IO.Path]::GetFileName($relative)))
    }
    $null = Invoke-SelectedAdb @('shell', 'chmod', '700', "$remote/wesnoth-text-layout-check")
    # Fixed names/GUID contain no shell metacharacters. The assignment applies
    # only to this probe process; Android settings and other apps stay unchanged.
    $run = Invoke-SelectedAdb @('shell', "LD_LIBRARY_PATH=$remote", "$remote/wesnoth-text-layout-check", "$remote/Lato-Regular.ttf", "$remote/DejaVuSans.ttf")
    foreach ($line in @('WESNOTH_ANDROID_TEXT_LAYOUT version=1.19.26 abi=x86_64 min_api=23',
        'PASS FriBidi mixed-direction ordering', 'PASS Pango private fonts and Arabic shaping',
        'PASS Pango paragraph wrapping', 'PASS PangoCairo layout rendering', 'PASS wesnoth-android-text-layout')) {
        if ($run.Stdout -notmatch ('(?m)^' + [regex]::Escape($line) + '\r?$')) { throw "The paragraph layout probe did not confirm: $line" }
    }
    if ($run.Text -match '(?m)^FAIL(?:\s|:)') { throw 'The paragraph layout probe reported a failure despite its exit code.' }
    Write-Host 'ANDROID TEXT LAYOUT TEST PASSED'
    Write-Host 'Verified mixed-direction paragraphs, private font shaping, wrapping and PangoCairo rendering only. No APK, full game-data installation or LAN play was tested.'
    Write-Host "Test files retained for diagnosis: $remote"
    $testExit = 0
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
} finally { exit $testExit }
