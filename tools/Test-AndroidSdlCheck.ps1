# Windows PowerShell 5.1. Install only the recorded SDL check APK; leave its window open.
[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._:-]*$')][string]$Serial = '127.0.0.1:5555',
    [string]$AdbPath = (Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe')
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$buildRoot = Join-Path $repoRoot 'build\android-sdl-apk'
$package = 'org.wesnoth.offlinecheck'
$component = $package + '/.CheckActivity'
$testExit = 2

function Require-RegularFile([string]$Root, [string]$Relative) {
    $file = Join-Path $Root $Relative
    if (-not (Test-Path -LiteralPath $file -PathType Leaf) -or (Get-Item -LiteralPath $file -Force).Length -eq 0) {
        throw "Required input is missing or empty: $file"
    }
    $cursor = $file
    while ($cursor.Length -ge $Root.Length) {
        if ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Symbolic links/reparse points are not accepted for APK test inputs: $cursor"
        }
        if ($cursor -eq $Root) { break }
        $cursor = Split-Path $cursor -Parent
    }
    return $file
}
function Test-RecordedFiles([string]$Root, $Records, [string[]]$Names) {
    $actual = @($Records.PSObject.Properties.Name | Sort-Object)
    if (($actual -join '|') -cne (($Names | Sort-Object) -join '|')) { throw 'SDL receipt has unexpected or missing file entries.' }
    foreach ($relative in $Names) {
        $entry = $Records.$relative
        if ($entry.bytes -le 0 -or $entry.sha256 -notmatch '^[0-9a-f]{64}$') { throw "Malformed SDL receipt entry: $relative" }
        $file = Require-RegularFile $Root $relative
        if ((Get-Item -LiteralPath $file -Force).Length -ne $entry.bytes -or
            (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $entry.sha256) {
            throw "SDL test input differs from its receipt: $relative. Rebuild before installing."
        }
    }
}
function Invoke-Adb([string[]]$Arguments, [int]$TimeoutSeconds = 30, [bool]$Quiet = $false) {
    $process = New-Object System.Diagnostics.Process
    try {
        $process.StartInfo.FileName = $AdbPath
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
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $process.Kill()
            throw "The selected ADB command timed out after $TimeoutSeconds seconds."
        }
        if (-not $stdout.Wait(2000) -or -not $stderr.Wait(2000)) { throw 'ADB output did not close after command exit.' }
        $output = ($stdout.Result + "`n" + $stderr.Result).Trim()
        if ($output -and -not $Quiet) { Write-Host $output }
        return @{ Code = $process.ExitCode; Text = $output; Stdout = $stdout.Result.Trim() }
    } finally { $process.Dispose() }
}
function Invoke-SelectedAdb([string[]]$Arguments, [int]$TimeoutSeconds = 30) {
    $result = Invoke-Adb (@('-s', $Serial) + $Arguments) $TimeoutSeconds
    if ($result.Code -ne 0) { throw "ADB command failed for $Serial with exit $($result.Code). Send all output." }
    return $result
}
function Wait-AppMessage([string]$Pattern, [string]$Label) {
    $timer = [Diagnostics.Stopwatch]::StartNew()
    do {
        $logs = Invoke-Adb @('-s', $Serial, 'logcat', '-d', "--pid=$appProcess", '-v', 'brief') 10 $true
        if ($logs.Code -ne 0) { Write-Host $logs.Text; throw 'Could not read logs for the SDL test process.' }
        if ($logs.Stdout -match ('WESNOTH_SDL_CHECK FAIL run_id=' + $runId + '(?:\s|$)')) {
            Write-Host $logs.Text
            throw "The SDL app reported a native failure during $Label."
        }
        $match = [regex]::Match($logs.Stdout, $Pattern)
        if ($match.Success) { Write-Host $match.Value; return $match }
        $state = Invoke-Adb @('-s', $Serial, 'shell', 'pidof', $package) 10 $true
        if ($state.Code -ne 0 -or $state.Stdout -ne $appProcess) {
            Write-Host $logs.Text
            throw "The SDL app exited or changed process before confirming $Label."
        }
        if ($timer.Elapsed.TotalSeconds -ge 30) { break }
        Start-Sleep -Milliseconds 500
    } while ($true)
    Write-Host $logs.Text
    throw "Timed out waiting for $Label from this SDL test run."
}
try {
    if ($env:OS -ne 'Windows_NT') { throw 'Run this helper on the Windows computer with LDPlayer open.' }
    if (-not (Test-Path -LiteralPath $AdbPath -PathType Leaf) -or (Get-Item -LiteralPath $AdbPath -Force).Length -eq 0) {
        throw "ADB is missing or empty: $AdbPath"
    }
    $receiptPath = Require-RegularFile $repoRoot 'build\android-sdl-apk\receipt.json'
    $receipt = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
    if ($receipt.schema_version -ne 1 -or $receipt.kind -ne 'wesnoth-android-sdl-check' -or
        $receipt.package_name -ne $package -or $receipt.activity -ne "$package.CheckActivity" -or
        $receipt.sdl_version -ne '3.4.2' -or $receipt.profile.wesnoth_version -ne '1.19.26' -or
        $receipt.profile.abi -ne 'x86_64' -or $receipt.profile.min_sdk -ne 23 -or
        $receipt.profile.ndk_version -ne '26.3.11579264') { throw 'SDL receipt does not match the selected app and native profile.' }
    $inputs = @('tools/build_android_sdl_check.py', 'tools/build_android_bootstrap.py',
        'tools/android-build-profile.json', 'tools/android-native-archives.json',
        'tools/sdl-check/settings.gradle', 'tools/sdl-check/build.gradle', 'tools/sdl-check/app.build.gradle',
        'tools/sdl-check/gradle.properties', 'tools/sdl-check/AndroidManifest.xml', 'tools/sdl-check/CheckActivity.java',
        'tools/sdl-check/native/CMakeLists.txt', 'tools/sdl-check/native/main.cpp')
    Test-RecordedFiles $repoRoot $receipt.inputs.project_files $inputs
    $apkRelative = 'app/build/outputs/apk/debug/app-debug.apk'
    $artifacts = @($apkRelative, 'app/src/main/jniLibs/x86_64/libSDL3.so',
        'app/src/main/jniLibs/x86_64/libmain.so', 'app/src/main/jniLibs/x86_64/libc++_shared.so')
    Test-RecordedFiles $buildRoot $receipt.artifacts $artifacts
    Write-Host 'PASS: SDL APK receipt, source inputs and all four build artifacts match their recorded hashes.'

    if ($Serial -match '^127\.0\.0\.1:[0-9]+$') {
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
        $properties['ro.product.cpu.abi'] -ne 'x86_64') { throw 'The selected device must be booted, API23 or later, and primary ABI x86_64.' }
    $install = Invoke-SelectedAdb @('install', '-r', (Join-Path $buildRoot $apkRelative)) 180
    if ($install.Stdout -notmatch '(?m)^Success\r?$') { throw 'ADB did not confirm successful installation of the SDL test APK.' }
    $null = Invoke-SelectedAdb @('shell', 'am', 'force-stop', $package)
    $runId = [guid]::NewGuid().ToString('N')
    $launch = Invoke-SelectedAdb @('shell', 'am', 'start', '-W', '-n', $component, '--es', 'wesnoth_run_id', $runId)
    if ($launch.Stdout -notmatch '(?m)^Status:\s*ok\r?$' -or $launch.Text -match '(?m)^Error:') {
        throw 'Android did not confirm a successful SDL activity launch.'
    }
    $processResult = Invoke-SelectedAdb @('shell', 'pidof', $package)
    if ($processResult.Stdout -notmatch '^[1-9][0-9]*$') { throw 'The SDL test must have exactly one identifiable running process.' }
    $appProcess = $processResult.Stdout
    Write-Host "SDL app PID=$appProcess; run_id=$runId. Reading only this process; existing device logs are retained."
    $ready = Wait-AppMessage ('WESNOTH_SDL_CHECK READY run_id=' + $runId +
        ' width=([0-9]+) height=([0-9]+) sdl=3\.4\.2 renderer=opengles2 abi=x86_64 min_api=23') 'first presented frame'
    $width = [int]$ready.Groups[1].Value
    $height = [int]$ready.Groups[2].Value
    if ($width -lt 2 -or $height -lt 2 -or $width -gt 16384 -or $height -gt 16384) { throw 'SDL reported invalid drawable dimensions.' }
    $x = [int][Math]::Floor($width / 2)
    $y = [int][Math]::Floor($height / 2)
    $null = Invoke-SelectedAdb @('shell', 'input', 'tap', [string]$x, [string]$y)
    $null = Wait-AppMessage ('WESNOTH_SDL_CHECK INPUT run_id=' + $runId + ' count=[1-9][0-9]*(?![0-9])') 'a presented input response'
    Write-Host 'ANDROID SDL CHECK AUTOMATED TEST PASSED'
    Write-Host 'Leave Wesnoth SDL Check open. Click the colored target and confirm that the counter and color change.'
    Write-Host 'Send the full output and your visual result. This app does not load Wesnoth game data or test LAN play or MongoDB.'
    $testExit = 0
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
} finally { exit $testExit }
