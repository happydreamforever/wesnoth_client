# Windows PowerShell 5.1. Update the existing app and stage only the PUBLIC server profile.
[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._:-]*$')][string]$Serial = '127.0.0.1:5555',
    [string]$SdkRoot = (Join-Path $env:LOCALAPPDATA 'Android\Sdk'),
    [string]$AdbPath = (Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'),
    [string]$JavaHome = 'C:\Program Files\Android\Android Studio\jbr',
    [string]$PythonPath = (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\python.exe'),
    [string]$ProfilePath = '',
    [switch]$KeepSavedProfile
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$package = 'org.wesnoth.offline'
$testExit = 2
$started = $false
$log = $null
$taskkill = $null
$runId = [guid]::NewGuid().ToString('N')
function Stop-CapturedProcessTree([System.Diagnostics.Process]$OwnedProcess) {
    # Use the live Process handle before selecting its PID. Never kill by an
    # image name or a PID whose owned process has already exited.
    if ($OwnedProcess.HasExited) { return }
    Write-Host 'Stopping only the timed-out or interrupted command process tree.'
    $stopper = New-Object System.Diagnostics.Process
    try {
        $stopper.StartInfo.FileName = $taskkill
        $stopper.StartInfo.Arguments = '/PID ' + $OwnedProcess.Id + ' /T /F'
        $stopper.StartInfo.UseShellExecute = $false
        $stopper.StartInfo.CreateNoWindow = $true
        $stopper.StartInfo.RedirectStandardOutput = $true
        $stopper.StartInfo.RedirectStandardError = $true
        $stopper.StartInfo.RedirectStandardInput = $true
        $null = $stopper.Start(); $stopper.StandardInput.Close()
        $stopOut = $stopper.StandardOutput.ReadToEndAsync()
        $stopErr = $stopper.StandardError.ReadToEndAsync()
        if (-not $stopper.WaitForExit(5000)) {
            $stopper.Kill()
            $null = $stopper.WaitForExit(2000)
            throw 'Taskkill timed out; command descendants could not be confirmed stopped. Send this log.'
        }
        if (-not $stopOut.Wait(2000) -or -not $stopErr.Wait(2000)) {
            throw 'Taskkill output did not close; command descendants could not be confirmed stopped.'
        }
        $message = ($stopOut.Result + "`n" + $stopErr.Result).Trim()
        if ($message) { Write-Host $message }
        if ($stopper.ExitCode -ne 0) {
            throw "Taskkill failed with exit $($stopper.ExitCode); command descendants could not be confirmed stopped."
        }
        if (-not $OwnedProcess.WaitForExit(5000)) {
            throw 'The owned command did not stop after taskkill. Send this log.'
        }
    } finally { $stopper.Dispose() }
}
function Invoke-Captured([string]$Executable, [string[]]$Arguments, [int]$TimeoutMilliseconds = 60000, [bool]$Quiet = $false) {
    $process = New-Object System.Diagnostics.Process
    $commandStarted = $false
    $completed = $false
    $stdout = $null; $stderr = $null
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
        foreach ($name in @('BASH_ENV', 'ENV', 'PYTHONHOME', 'PYTHONPATH')) { $process.StartInfo.EnvironmentVariables.Remove($name) }
        $process.StartInfo.EnvironmentVariables['PYTHONDONTWRITEBYTECODE'] = '1'
        $null = $process.Start(); $commandStarted = $true; $process.StandardInput.Close()
        $stdout = $process.StandardOutput.ReadToEndAsync(); $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutMilliseconds)) {
            throw 'The selected verification/ADB command timed out. Its owned process tree will be stopped.'
        }
        if (-not $stdout.Wait(2000) -or -not $stderr.Wait(2000)) { throw 'Command output did not close after exit.' }
        $text = ($stdout.Result + "`n" + $stderr.Result).Trim()
        if ($text -and -not $Quiet) { Write-Host $text }
        $completed = $true
        return @{ Code = $process.ExitCode; Text = $text; Stdout = $stdout.Result.Trim() }
    } finally {
        try {
            if ($commandStarted -and -not $completed) {
                if (-not $process.HasExited) { Stop-CapturedProcessTree $process }
                # A parent that already exited must not be targeted by PID: the
                # ID may be reused. Unclosed inherited pipes are not a success.
                if (($null -ne $stdout -and -not $stdout.Wait(2000)) -or
                    ($null -ne $stderr -and -not $stderr.Wait(2000))) {
                    throw 'The command exited but inherited output stayed open; remaining descendants could not be confirmed stopped.'
                }
                if ($null -ne $stdout -and $stdout.IsCompleted -and $null -ne $stderr -and $stderr.IsCompleted) {
                    $partial = ($stdout.Result + "`n" + $stderr.Result).Trim()
                    if ($partial -and -not $Quiet) { Write-Host $partial }
                }
            }
        } finally { $process.Dispose() }
    }
}
function Invoke-Adb([string[]]$Arguments, [int]$TimeoutMilliseconds = 60000, [bool]$Quiet = $false) {
    return Invoke-Captured $AdbPath (@('-H', 'localhost', '-P', '5037') + $Arguments) $TimeoutMilliseconds $Quiet
}
function Selected-Adb([string[]]$Arguments, [int]$TimeoutMilliseconds = 60000, [bool]$Quiet = $false) {
    $result = Invoke-Adb (@('-s', $Serial) + $Arguments) $TimeoutMilliseconds $Quiet
    if ($result.Code -ne 0) { throw "ADB operation failed with exit $($result.Code). Send this log." }
    return $result
}
try {
    if ($env:OS -ne 'Windows_NT') { throw 'Run this helper on Windows with the existing LDPlayer instance open.' }
    if ($KeepSavedProfile -and $ProfilePath) { throw 'Use KeepSavedProfile without ProfilePath. The existing trusted server will not be replaced.' }
    $diagnostics = Join-Path $repoRoot 'diagnostics'
    $null = New-Item -ItemType Directory -Path $diagnostics -Force
    $log = Join-Path $diagnostics ('android-account-install-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' + $runId + '.txt')
    Start-Transcript -LiteralPath $log -NoClobber | Out-Null; $started = $true
    $verifier = Join-Path $PSScriptRoot 'build_android_account_game.py'
    $java = Join-Path $JavaHome 'bin\java.exe'
    $taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
    foreach ($file in @($PythonPath, $AdbPath, $verifier, $java, $taskkill)) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf) -or (Get-Item -LiteralPath $file -Force).Length -eq 0) { throw "Required tool missing or empty: $file" }
    }
    Write-Host 'Verifying account APK, completed engine and existing data index before any Android changes.'
    $verified = Invoke-Captured $PythonPath @('-I', '-B', '-u', $verifier, '--verify-only', '--sdk', $SdkRoot, '--java', $java) 600000
    if ($verified.Code -ne 0 -or $verified.Stdout -notmatch '(?m)^ANDROID ACCOUNT GAME ARTIFACTS VERIFIED\r?$') { throw 'Account APK verification did not pass; no Android change made.' }
    $apk = Join-Path $repoRoot 'build\android-account-game\app\build\outputs\apk\debug\app-debug.apk'
    $receipt = Get-Content -LiteralPath (Join-Path $repoRoot 'build\android-account-game\receipt.json') -Raw | ConvertFrom-Json
    $apkRecord = $receipt.artifacts.'app/build/outputs/apk/debug/app-debug.apk'
    if ((Get-FileHash -LiteralPath $apk -Algorithm SHA256).Hash.ToLowerInvariant() -cne $apkRecord.sha256 -or
        (Get-Item -LiteralPath $apk).Length -ne $apkRecord.bytes) { throw 'APK changed after verification.' }
    if (-not $KeepSavedProfile) {
        if (-not $ProfilePath) { $ProfilePath = Join-Path (Split-Path $repoRoot -Parent) 'wesnoth_server\runtime\alliance-transport\public-profile.json' }
        # A separate validator checks exact public fields and the certificate pin without printing its contents.
        $publicCopy = Join-Path $diagnostics ('public-alliance-profile-' + $runId + '.json')
        $profileCheck = Invoke-Captured $PythonPath @('-I', '-B', (Join-Path $PSScriptRoot 'validate_android_account_profile.py'), $ProfilePath, '--copy-to', $publicCopy) 30000
        if ($profileCheck.Code -ne 0 -or $profileCheck.Stdout -notmatch '(?m)^PUBLIC ALLIANCE PROFILE VERIFIED\r?$') { throw 'Select the public-profile.json from the Windows identity helper; no private runtime file is accepted.' }
        $profileHash = (Get-FileHash -LiteralPath $publicCopy -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    if ($Serial -match '^127\.0\.0\.1:[0-9]+$') {
        $connected = Invoke-Adb @('connect', $Serial)
        if ($connected.Code -ne 0) { throw 'ADB connection failed.' }
    }
    if ((Selected-Adb @('get-state')).Stdout -cne 'device') { throw 'LDPlayer is not ready.' }
    foreach ($pair in @(@('sys.boot_completed', '1'), @('ro.build.version.sdk', '28'), @('ro.product.cpu.abi', 'x86_64'))) {
        if ((Selected-Adb @('shell', 'getprop', $pair[0])).Stdout -cne $pair[1]) { throw "Expected the verified LDPlayer property: $($pair[0])=$($pair[1])" }
    }
    $installed = Selected-Adb @('shell', 'pm', 'path', $package)
    if ($installed.Stdout -notmatch '^package:/') { throw 'The existing tested Wesnoth app is not installed. Stop and send the log.' }
    if ($KeepSavedProfile) {
        $packageDetails = Selected-Adb @('shell', 'dumpsys', 'package', $package) 60000 $true
        $versions = [regex]::Matches($packageDetails.Stdout, '(?m)^\s*versionCode=([0-9]+)(?:\s|$)')
        if ($versions.Count -ne 1 -or $versions[0].Groups[1].Value -notin @('1192608', '1192609', '1192610', '1192611')) {
            throw 'Expected the installed Alliance account game version 1192608, 1192609, 1192610 or 1192611. No app update was attempted.'
        }
    }
    $install = Selected-Adb @('install', '-r', $apk) 300000
    if ($install.Stdout -notmatch '(?m)^Success\r?$') { throw 'Android did not confirm the app update. No uninstall or data reset was attempted.' }
    if ($KeepSavedProfile) {
        $packageDetails = Selected-Adb @('shell', 'dumpsys', 'package', $package) 60000 $true
        $versions = [regex]::Matches($packageDetails.Stdout, '(?m)^\s*versionCode=([0-9]+)(?:\s|$)')
        if ($versions.Count -ne 1 -or $versions[0].Groups[1].Value -cne '1192611') {
            throw 'Android did not report the expected updated version 1192611. Send this log.'
        }
    } else {
        $remote = '/sdcard/Download/wesnoth-alliance-public-profile-' + $runId + '.json'
        $null = Selected-Adb @('shell', 'mkdir', '-p', '/sdcard/Download')
        $null = Selected-Adb @('push', $publicCopy, $remote)
        $uploaded = Selected-Adb @('shell', 'sha256sum', $remote)
        if ($uploaded.Stdout -cnotmatch ('^' + $profileHash + '\s+' + [regex]::Escape($remote) + '$')) { throw 'The transferred PUBLIC profile hash differs. Do not import it.' }
    }
    $null = Selected-Adb @('shell', 'am', 'force-stop', $package)
    $launch = Selected-Adb @('shell', 'am', 'start', '-W', '-n', ($package + '/.AccountSetupActivity'), '--es', 'wesnoth_run_id', $runId)
    if ($launch.Stdout -notmatch '(?m)^Status:\s*ok\r?$' -or $launch.Text -match '(?m)^Error:') { throw 'Android did not confirm setup activity startup.' }
    $processId = (Selected-Adb @('shell', 'pidof', $package)).Stdout
    if ($processId -notmatch '^[1-9][0-9]*$') { throw 'The updated app must have exactly one running process.' }
    $ready = $false
    for ($attempt = 0; $attempt -lt 15; $attempt++) {
        $logs = Selected-Adb @('logcat', '-d', "--pid=$processId", '-v', 'brief', 'WesnothOffline:I', '*:S') 30000 $true
        if ($logs.Stdout -match ('WESNOTH_ACCOUNT_SETUP_READY run_id=' + $runId + '(?:\s|$)')) { $ready = $true; break }
        Start-Sleep -Milliseconds 1000
    }
    if (-not $ready) { throw 'No fresh account setup marker was observed. Send the saved log.' }
    Write-Host 'ANDROID ACCOUNT GAME INSTALL COMPLETE'
    if ($KeepSavedProfile) {
        Write-Host 'Version 1192611 and setup activity startup are verified. No profile was imported or changed by this helper.'
        Write-Host 'Confirm the existing Trusted server is displayed, then choose Start game -> Alliance -> Log in.'
        Write-Host 'If setup says no saved profile or reports an unusable profile, stop and send that message; do not reimport or reset the app.'
    } else {
        Write-Host 'The APK update and setup activity are verified. Account registration, login, leader and logout need the manual test.'
        Write-Host ('In LDPlayer choose Import server profile, then Downloads: ' + (Split-Path $remote -Leaf))
        Write-Host 'Compare the displayed address and SHA-256 with the Windows server, choose Trust server, then Start game -> Alliance.'
    }
    Write-Host 'No OBB transfer, app-data deletion, account credential collection or database initialization was performed.'
    $testExit = 0
} catch { Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red }
finally {
    if ($started) { Stop-Transcript | Out-Null }
    if ($log) { Write-Host "Saved log: $log" }
    exit $testExit
}
