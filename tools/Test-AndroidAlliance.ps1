# Windows PowerShell 5.1. Verify Alliance transport artifacts, then run the standalone LDPlayer probe.
[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._:-]*$')][string]$Serial = '127.0.0.1:5555',
    [string]$AdbPath = (Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'),
    [string]$PythonPath = (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\python.exe')
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$testExit = 2
$transcriptStarted = $false
$log = $null

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
    $diagnostics = Join-Path $repoRoot 'diagnostics'
    if (Test-Path -LiteralPath $diagnostics) {
        if (-not (Test-Path -LiteralPath $diagnostics -PathType Container) -or
            ((Get-Item -LiteralPath $diagnostics -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Diagnostics must be a regular directory.'
        }
    } else { $null = New-Item -ItemType Directory -Path $diagnostics }
    $log = Join-Path $diagnostics ('android-alliance-device-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' + [guid]::NewGuid().ToString('N') + '.txt')
    Start-Transcript -LiteralPath $log -NoClobber | Out-Null
    $transcriptStarted = $true
    $verifier = Join-Path $PSScriptRoot 'build_android_alliance.py'
    foreach ($file in @($PythonPath, $AdbPath, $verifier)) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf) -or (Get-Item -LiteralPath $file -Force).Length -eq 0) {
            throw "Required verification tool is missing or empty: $file"
        }
    }
    # The builder owns this shared, read-only receipt/profile/source/prefix/ELF
    # verification, including its recorded source and prerequisite inputs. It runs
    # with native Python and needs no MSYS, NDK or cache.
    # Receipt verification has a separate limit from a single ADB command.
    Write-Host 'Verifying Alliance transport and prerequisite artifacts; this may take a few minutes.'
    $verified = Invoke-Captured $PythonPath @('-I', '-B', '-u', $verifier, '--verify') 600000
    if ($verified.Code -ne 0 -or $verified.Stdout -notmatch '(?m)^ANDROID ALLIANCE ARTIFACTS VERIFIED\r?$') {
        throw 'Alliance artifact verification failed or did not confirm completion. Nothing was sent to Android.'
    }
    $artifact = Require-RegularFile $repoRoot 'build/android-alliance/prefix/bin/android-alliance-check'
    $runtime = Require-RegularFile $repoRoot 'build/android-alliance/prefix/lib/libc++_shared.so'
    $receiptFile = Require-RegularFile $repoRoot 'build/android-alliance/receipt.json'
    $receipt = Get-Content -LiteralPath $receiptFile -Raw | ConvertFrom-Json
    $payload = @(
        @{ Path = $artifact; Relative = 'prefix/bin/android-alliance-check'; Name = 'android-alliance-check' }
        @{ Path = $runtime; Relative = 'prefix/lib/libc++_shared.so'; Name = 'libc++_shared.so' }
    )
    foreach ($entry in $payload) {
        $property = $receipt.artifacts.PSObject.Properties[$entry.Relative]
        if ($null -eq $property -or $property.Value.sha256 -cnotmatch '^[0-9a-f]{64}$') {
            throw 'Verified receipt lacks a valid artifact hash. Nothing was sent to Android.'
        }
        $entry.Hash = (Get-FileHash -LiteralPath $entry.Path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($entry.Hash -cne $property.Value.sha256) {
            throw 'Artifact changed after receipt verification. Nothing was sent to Android.'
        }
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
        -not [int]::TryParse($properties['ro.build.version.sdk'], [ref]$api) -or $api -ne 28 -or
        $properties['ro.product.cpu.abi'] -ne 'x86_64') {
        throw 'The selected device must finish booting, run the selected LDPlayer Android API28, and have primary ABI x86_64.'
    }
    # Only fixed names and a generated GUID enter Android shell commands.
    $remote = '/data/local/tmp/wesnoth-alliance-check/' + [guid]::NewGuid().ToString('N')
    Write-Host "Alliance test directory: $remote"
    $null = Invoke-SelectedAdb @('shell', 'mkdir', '-p', $remote)
    foreach ($entry in $payload) {
        $null = Invoke-SelectedAdb @('push', $entry.Path, ($remote + '/' + $entry.Name))
    }
    foreach ($entry in $payload) {
        $target = $remote + '/' + $entry.Name
        $result = Invoke-SelectedAdb @('shell', 'sha256sum', $target)
        if ($result.Stdout -cnotmatch ('^([0-9a-f]{64})[ \t]+\*?' + [regex]::Escape($target) + '$') -or
            $Matches[1] -cne $entry.Hash) {
            throw ('Android transfer hash did not match for ' + $entry.Name + '. Probe was not executed.')
        }
        Write-Host ('PASS transferred SHA256 ' + $entry.Name)
    }
    $null = Invoke-SelectedAdb @('shell', 'chmod', '700', "$remote/android-alliance-check")
    $run = Invoke-SelectedAdb @('shell', "cd $remote && LD_LIBRARY_PATH=$remote $remote/android-alliance-check")
    $required = @(
        'WESNOTH_ANDROID_ALLIANCE abi=x86_64 min_api=23 boost=1_88 openssl=0x30400010',
        'PASS before-connect-guards', 'PASS wrong-ip', 'PASS wrong-pin',
        'PASS hostname-refused', 'PASS broadcast-refused', 'PASS expired',
        'PASS TLS1.2', 'PASS TLS1.3', 'PASS server-push', 'PASS untrusted',
        'PASS downgrade', 'PASS plaintext', 'PASS preamble-eof', 'PASS preamble-reset',
        'PASS preamble-stall', 'PASS tls-stall', 'PASS oversized', 'PASS empty-frame',
        'PASS partial-header', 'PASS partial-body', 'PASS receive-stall', 'PASS cancel',
        'ALLIANCE ANDROID TRANSPORT CHECK PASSED'
    )
    foreach ($line in $required) {
        if ([regex]::Matches($run.Stdout, ('(?m)^' + [regex]::Escape($line) + '\r?$')).Count -ne 1) {
            throw "The Alliance probe did not confirm exactly once: $line"
        }
    }
    if ($run.Text -match '(?m)^(?:FAIL|ERROR)(?:\s|:)') { throw 'The Alliance probe reported a failure despite its exit code.' }
    Write-Host 'ANDROID ALLIANCE TEST PASSED'
    Write-Host 'Verified native Alliance TLS and bounded frames inside LDPlayer loopback. Windows LAN reachability, account login, MongoDB and game UI integration are not tested.'
    Write-Host "Test files retained for diagnosis: $remote"
    $testExit = 0
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
} finally {
    if ($transcriptStarted) {
        try { Stop-Transcript | Out-Null }
        catch { Write-Host ('ERROR saving transcript: ' + $_.Exception.Message); $testExit = 2 }
    }
    if ($null -ne $log) { Write-Host "Saved log: $log" }
    exit $testExit
}
