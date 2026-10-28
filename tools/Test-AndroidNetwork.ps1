# Windows PowerShell 5.1. Verify OpenSSL/curl artifacts, then run the native network probe.
[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._:-]*$')][string]$Serial = '127.0.0.1:5555',
    [string]$AdbPath = (Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'),
    [string]$PythonPath = (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe')
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
function Invoke-Captured([string]$Executable, [string[]]$Arguments, [int]$TimeoutMilliseconds = 60000) {
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
        $process.StartInfo.RedirectStandardInput = $true
        foreach ($name in @('BASH_ENV', 'ENV', 'PYTHONHOME', 'PYTHONPATH')) {
            $process.StartInfo.EnvironmentVariables.Remove($name)
        }
        $process.StartInfo.EnvironmentVariables['PYTHONDONTWRITEBYTECODE'] = '1'
        $process.StartInfo.EnvironmentVariables['PYTHONNOUSERSITE'] = '1'
        $null = $process.Start()
        $process.StandardInput.Close()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutMilliseconds)) { $process.Kill(); throw "The selected verification/ADB command timed out after $($TimeoutMilliseconds / 1000) seconds." }
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
    $verifier = Join-Path $PSScriptRoot 'build_android_network.py'
    foreach ($file in @($PythonPath, $AdbPath, $verifier)) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf) -or (Get-Item -LiteralPath $file -Force).Length -eq 0) {
            throw "Required verification tool is missing or empty: $file"
        }
    }
    # The builder owns this shared, read-only receipt/profile/source/prefix/ELF
    # verification, including its recorded source and prerequisite inputs. It runs
    # with native Python and needs no MSYS, NDK or cache.
    # Receipt verification has a separate limit from a single ADB command.
    Write-Host 'Verifying OpenSSL/curl and prerequisite artifacts; this may take a few minutes.'
    $verified = Invoke-Captured $PythonPath @('-I', '-B', '-u', $verifier, '--verify') 600000
    if ($verified.Code -ne 0 -or $verified.Stdout -notmatch '(?m)^ANDROID NETWORK ARTIFACTS VERIFIED\r?$') {
        throw 'Network artifact verification failed or did not confirm completion. Nothing was sent to Android.'
    }
    $artifact = Require-RegularFile $repoRoot 'build/android-network/prefix/bin/wesnoth-network-check'
    $runtime = Require-RegularFile $repoRoot 'build/android-network/prefix/lib/libc++_shared.so'
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
    $remote = '/data/local/tmp/wesnoth-network-check/' + [guid]::NewGuid().ToString('N')
    Write-Host "Network test directory: $remote"
    $null = Invoke-SelectedAdb @('shell', 'mkdir', '-p', $remote)
    $null = Invoke-SelectedAdb @('push', $artifact, "$remote/wesnoth-network-check")
    $null = Invoke-SelectedAdb @('push', $runtime, "$remote/libc++_shared.so")
    $null = Invoke-SelectedAdb @('shell', 'chmod', '700', "$remote/wesnoth-network-check")
    # Fixed names/GUID contain no shell metacharacters. The assignment applies
    # only to this probe process. The temporary HTTPS listener uses the selected
    # Android device's loopback interface, with no external server connection.
    $run = Invoke-SelectedAdb @('shell', "cd $remote && LD_LIBRARY_PATH=$remote $remote/wesnoth-network-check")
    foreach ($line in @('WESNOTH_ANDROID_NETWORK version=1.19.26 abi=x86_64 min_api=23',
        'PASS OpenSSL SHA256 and authenticated AES-GCM',
        'PASS OpenSSL ephemeral certificate chain and rejection',
        'PASS curl URL parsing and pinned TLS backend',
        'PASS curl verified loopback HTTPS TLS1.2 and TLS1.3',
        'PASS curl certificate hostname and HTTP failure rejection',
        'PASS wesnoth-android-network')) {
        if ($run.Stdout -notmatch ('(?m)^' + [regex]::Escape($line) + '\r?$')) { throw "The network probe did not confirm: $line" }
    }
    if ($run.Text -match '(?m)^FAIL(?:\s|:)') { throw 'The network probe reported a failure despite its exit code.' }
    Write-Host 'ANDROID NETWORK TEST PASSED'
    Write-Host 'Verified OpenSSL cryptography and curl HTTPS against a temporary server inside the selected Android device. No external network, APK, gameplay or Windows server connection was tested.'
    Write-Host "Test files retained for diagnosis: $remote"
    $testExit = 0
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
} finally { exit $testExit }
