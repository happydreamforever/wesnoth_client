# Windows PowerShell 5.1. Executes only the verified native probe on the selected device.
[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._:-]*$')][string]$Serial = '127.0.0.1:5555',
    [string]$AdbPath = (Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe')
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$buildRoot = Join-Path $repoRoot 'build\android-native-bootstrap'
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
            throw "Native test input differs from its receipt: $relative. Rebuild; do not push these files."
        }
    }
}
function Invoke-Adb([string[]]$Arguments) {
    $process = New-Object System.Diagnostics.Process
    try {
        $process.StartInfo.FileName = $AdbPath
        # Explicit quoting works on Windows PowerShell 5.1 and preserves paths with spaces.
        $process.StartInfo.Arguments = ((@('-H', 'localhost', '-P', '5037') + $Arguments | ForEach-Object {
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
        if (-not $process.WaitForExit(60000)) {
            $process.Kill()
            throw 'The selected ADB command timed out after 60 seconds.'
        }
        if (-not $stdout.Wait(2000) -or -not $stderr.Wait(2000)) { throw 'ADB output did not close after command exit.' }
        $output = ($stdout.Result + "`n" + $stderr.Result).Trim()
        if ($output) { Write-Host $output }
        return @{ Code = $process.ExitCode; Text = $output; Stdout = $stdout.Result.Trim() }
    } finally { $process.Dispose() }
}
function Invoke-SelectedAdb([string[]]$Arguments) {
    $result = Invoke-Adb (@('-s', $Serial) + $Arguments)
    if ($result.Code -ne 0) { throw "ADB command failed for $Serial with exit $($result.Code). Send all output." }
    return $result
}
try {
    if ($env:OS -ne 'Windows_NT') { throw 'Run this helper on the Windows computer with LDPlayer open.' }
    if (-not (Test-Path -LiteralPath $AdbPath -PathType Leaf) -or (Get-Item -LiteralPath $AdbPath -Force).Length -eq 0) { throw "ADB is missing or empty: $AdbPath" }
    $receiptPath = Require-RegularFile $buildRoot 'receipt.json'
    $receipt = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
    if ($receipt.schema_version -ne 1 -or $receipt.kind -ne 'wesnoth-android-native-bootstrap' -or
        $receipt.profile.wesnoth_version -ne '1.19.26' -or $receipt.profile.abi -ne 'x86_64' -or
        $receipt.profile.min_sdk -ne 23 -or $receipt.profile.ndk_version -ne '26.3.11579264') {
        throw 'Native receipt does not match the selected Wesnoth/NDK/x86_64/API23 profile.'
    }
    $inputs = @('tools/build_android_bootstrap.py', 'tools/android-build-profile.json', 'tools/android-native-archives.json',
        'tools/native-bootstrap/CMakeLists.txt', 'tools/native-bootstrap/probe.cpp')
    Test-RecordedFiles $repoRoot $receipt.inputs.project_files $inputs
    Test-RecordedFiles $buildRoot $receipt.artifacts @('out/bin/wesnoth-native-check', 'out/bin/libc++_shared.so', 'out/lib/libbz2.a')
    Write-Host 'PASS: native receipt, tracked inputs and all three local artifacts match their recorded hashes.'

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
        $result = Invoke-SelectedAdb @('shell', 'getprop', $property)
        $properties[$property] = $result.Stdout
    }
    $api = 0
    if ($properties['sys.boot_completed'] -ne '1' -or
        -not [int]::TryParse($properties['ro.build.version.sdk'], [ref]$api) -or $api -lt 23 -or
        $properties['ro.product.cpu.abi'] -ne 'x86_64') {
        throw 'The selected device must finish booting, run Android API23 or later, and have primary ABI x86_64.'
    }
    # Every remote shell argument below comes from fixed ASCII names and this GUID.
    # No user-provided strings are interpolated into a remote shell command.
    $remote = '/data/local/tmp/wesnoth-native-bootstrap/' + [guid]::NewGuid().ToString('N')
    Write-Host "Native test directory: $remote"
    $null = Invoke-SelectedAdb @('shell', 'mkdir', '-p', $remote)
    foreach ($name in @('wesnoth-native-check', 'libc++_shared.so')) {
        $null = Invoke-SelectedAdb @('push', (Join-Path $buildRoot "out\bin\$name"), "$remote/$name")
    }
    $null = Invoke-SelectedAdb @('shell', 'chmod', '700', "$remote/wesnoth-native-check")
    $run = Invoke-SelectedAdb @('shell', 'env', "LD_LIBRARY_PATH=$remote", "$remote/wesnoth-native-check")
    $expectedLines = @('WESNOTH_NATIVE_BOOTSTRAP version=1.19.26 abi=x86_64 min_api=23',
        'PASS bzip2 roundtrip', 'PASS zlib roundtrip', 'PASS C++ STL', 'PASS C++ exceptions', 'PASS wesnoth-native-bootstrap')
    foreach ($line in $expectedLines) {
        if ($run.Stdout -notmatch ('(?m)^' + [regex]::Escape($line) + '\r?$')) { throw "The native probe did not confirm: $line" }
    }
    if ($run.Text -match '(?m)^FAIL(?:\s|:)') { throw 'The native probe reported a failure despite its exit code.' }
    Write-Host 'ANDROID NATIVE BOOTSTRAP TEST PASSED'
    Write-Host 'Verified the native bootstrap only. This did not build an APK or test game UI, LAN play or MongoDB.'
    Write-Host "Test files retained for diagnosis: $remote"
    $testExit = 0
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
} finally { exit $testExit }
