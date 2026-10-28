# Windows PowerShell 5.1. Verify GLib and its prerequisite receipts, then run its C probe.
[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._:-]*$')][string]$Serial = '127.0.0.1:5555',
    [string]$AdbPath = (Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'),
    [string]$PythonPath = (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe')
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$testExit = 2

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
    $verifier = Join-Path $PSScriptRoot 'build_android_glib.py'
    foreach ($file in @($PythonPath, $AdbPath, $verifier)) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf) -or (Get-Item -LiteralPath $file -Force).Length -eq 0) {
            throw "Required verification tool is missing or empty: $file"
        }
    }
    # The builder owns this shared, read-only receipt/profile/source/prefix/ELF
    # verification, including both completed prerequisite prefixes. It runs
    # with native Python and needs no MSYS, NDK or cache.
    $verified = Invoke-Captured $PythonPath @('-I', '-B', '-u', $verifier, '--verify-only')
    if ($verified.Code -ne 0 -or $verified.Stdout -notmatch '(?m)^ANDROID GLIB ARTIFACTS VERIFIED\r?$') {
        throw 'GLib artifact verification failed or did not confirm completion. Nothing was sent to Android.'
    }
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
    $remote = '/data/local/tmp/wesnoth-glib-check/' + [guid]::NewGuid().ToString('N')
    $artifact = Join-Path $repoRoot 'build\android-glib\prefix\bin\wesnoth-glib-check'
    Write-Host "GLib test directory: $remote"
    $null = Invoke-SelectedAdb @('shell', 'mkdir', '-p', $remote)
    $null = Invoke-SelectedAdb @('push', $artifact, "$remote/wesnoth-glib-check")
    $null = Invoke-SelectedAdb @('shell', 'chmod', '700', "$remote/wesnoth-glib-check")
    $run = Invoke-SelectedAdb @('shell', "$remote/wesnoth-glib-check")
    foreach ($line in @('WESNOTH_ANDROID_GLIB version=1.19.26 abi=x86_64 min_api=23',
        'PASS GLib Unicode and regex', 'PASS GLib variants', 'PASS GObject signals and lifetime',
        'PASS GIO streams and compression', 'PASS GLib threads and main context',
        'PASS GModule process symbols', 'PASS wesnoth-android-glib')) {
        if ($run.Stdout -notmatch ('(?m)^' + [regex]::Escape($line) + '\r?$')) { throw "The glib probe did not confirm: $line" }
    }
    if ($run.Text -match '(?m)^FAIL(?:\s|:)') { throw 'The glib probe reported a failure despite its exit code.' }
    Write-Host 'ANDROID GLIB TEST PASSED'
    Write-Host 'Verified GLib, GObject, GIO and GModule execution only. No APK, game data or LAN play was tested.'
    Write-Host "Test file retained for diagnosis: $remote"
    $testExit = 0
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
} finally { exit $testExit }
