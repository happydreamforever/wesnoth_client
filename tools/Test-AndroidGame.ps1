# Windows PowerShell 5.1. Verify/install the game APK and OBB, then leave its UI open for manual testing.
[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._:-]*$')][string]$Serial = '127.0.0.1:5555',
    [string]$SdkRoot = (Join-Path $env:LOCALAPPDATA 'Android\Sdk'),
    [string]$AdbPath = (Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'),
    [string]$JavaHome = 'C:\Program Files\Android\Android Studio\jbr',
    [string]$PythonPath = (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe')
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$package = 'org.wesnoth.offline'
$component = $package + '/.OfflineInitActivity'
$obbName = 'main.1192600.org.wesnoth.offline.obb'
$testExit = 2
$launchAttempted = $false
$appProcess = $null
$importTimeoutMilliseconds = 600000
$runId = [guid]::NewGuid().ToString('N')

function Require-RegularFile([string]$Root, [string]$Relative) {
    $file = Join-Path $Root $Relative
    if (-not (Test-Path -LiteralPath $file -PathType Leaf) -or (Get-Item -LiteralPath $file -Force).Length -eq 0) {
        throw "Required input is missing or empty: $file"
    }
    $cursor = $file
    while ($cursor.Length -ge $Root.Length) {
        if ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Symbolic links/reparse points are not accepted for game test inputs: $cursor"
        }
        if ($cursor -eq $Root) { break }
        $cursor = Split-Path $cursor -Parent
    }
    return $file
}
function Invoke-Captured([string]$Executable, [string[]]$Arguments, [int]$TimeoutMilliseconds = 60000, [bool]$Quiet = $false) {
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
        if (-not $process.WaitForExit($TimeoutMilliseconds)) {
            $process.Kill()
            throw "The selected verification/ADB command timed out after $($TimeoutMilliseconds / 1000) seconds."
        }
        if (-not $stdout.Wait(2000) -or -not $stderr.Wait(2000)) { throw 'Command output did not close after exit.' }
        $output = ($stdout.Result + "`n" + $stderr.Result).Trim()
        if ($output -and -not $Quiet) { Write-Host $output }
        return @{ Code = $process.ExitCode; Text = $output; Stdout = $stdout.Result.Trim() }
    } finally { $process.Dispose() }
}
function Invoke-Adb([string[]]$Arguments, [int]$TimeoutMilliseconds = 60000, [bool]$Quiet = $false) {
    return Invoke-Captured $AdbPath (@('-H', 'localhost', '-P', '5037') + $Arguments) $TimeoutMilliseconds $Quiet
}
function Invoke-SelectedAdb([string[]]$Arguments, [int]$TimeoutMilliseconds = 60000, [bool]$Quiet = $false) {
    $result = Invoke-Adb (@('-s', $Serial) + $Arguments) $TimeoutMilliseconds $Quiet
    if ($result.Code -ne 0) { throw "ADB command failed for $Serial with exit $($result.Code). Send all output." }
    return $result
}
function Save-AppLogs {
    $diagnostics = Join-Path $repoRoot 'diagnostics'
    $null = New-Item -ItemType Directory -Path $diagnostics -Force
    $path = Join-Path $diagnostics ('game-logcat-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' + $runId + '.txt')
    # Prefer the exact app PID; do not clear or collect the whole device log.
    if ($appProcess) {
        $logs = Invoke-Adb @('-s', $Serial, 'logcat', '-d', "--pid=$appProcess", '-v', 'threadtime') 30000 $true
    } else {
        $logs = Invoke-Adb @('-s', $Serial, 'logcat', '-d', '-v', 'threadtime',
            'WesnothOffline:V', 'SDL:V', 'SDL/APP:V', 'AndroidRuntime:E', 'DEBUG:E', 'libc:E', '*:S') 30000 $true
    }
    if ($logs.Code -ne 0) { throw 'Could not collect the selected game logcat output.' }
    $content = "Package=$package; serial=$Serial; PID=$appProcess; run_id=$runId`r`n" + $logs.Text
    [IO.File]::WriteAllText($path, $content, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "Game logcat saved: $path"
    if ($testExit -ne 0) {
        Write-Host 'Last game log lines:'
        $logs.Text -split '\r?\n' | Select-Object -Last 60 | ForEach-Object { Write-Host $_ }
    }
    # SDL Android does not forward native stdout/stderr to logcat. The game
    # keeps its normal file logging, confined to this package's userdata/logs.
    $nativeLogs = '/sdcard/Android/data/' + $package + '/files/userdata/logs'
    $present = Invoke-Adb @('-s', $Serial, 'shell', 'test', '-d', $nativeLogs) 30000 $true
    if ($present.Code -eq 1) {
        Write-Host 'The game has not created its native userdata/logs directory yet.'
    } elseif ($present.Code -ne 0) {
        throw 'Could not check the game native log directory.'
    } else {
        $destination = Join-Path $diagnostics ('game-userdata-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' + $runId)
        $null = Invoke-SelectedAdb @('pull', $nativeLogs, $destination) 60000
        Write-Host "Game native logs saved: $destination"
    }
}
function Wait-GameImport {
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $nextProgress = 0
    $dataPattern = 'WESNOTH_GAME_DATA_READY run_id=' + $runId + ' files=26748 bytes=726207542(?:\s|$)'
    $activityPattern = 'WESNOTH_GAME_ACTIVITY_READY run_id=' + $runId + '(?:\s|$)'
    $launchPattern = 'WESNOTH_GAME_LAUNCH run_id=' + $runId + ' version=1\.19\.26(?:\s|$)'
    do {
        $logs = Invoke-SelectedAdb @('logcat', '-d', "--pid=$appProcess", '-v', 'brief') 30000 $true
        if ($logs.Stdout -match ('WESNOTH_GAME FAIL run_id=' + $runId + '(?:\s|$)')) {
            throw 'The game reported an asset-import or activity failure. Its log will be saved.'
        }
        $state = Invoke-Adb @('-s', $Serial, 'shell', 'pidof', $package) 10000 $true
        if ($state.Code -ne 0 -or $state.Stdout -ne $appProcess) {
            throw 'The game process exited or changed before the import/activity check completed.'
        }
        if ($logs.Stdout -match $dataPattern -and $logs.Stdout -match $launchPattern -and $logs.Stdout -match $activityPattern) {
            foreach ($pattern in @($dataPattern, $launchPattern, $activityPattern)) {
                Write-Host ([regex]::Match($logs.Stdout, $pattern).Value.Trim())
            }
            return
        }
        if ($timer.ElapsedMilliseconds -ge $importTimeoutMilliseconds) { break }
        if ($timer.Elapsed.TotalSeconds -ge $nextProgress) {
            Write-Host ("Waiting for verified game-data import and activity startup: {0}s elapsed. Keep LDPlayer open." -f [int]$timer.Elapsed.TotalSeconds)
            $nextProgress = $timer.Elapsed.TotalSeconds + 15
        }
        Start-Sleep -Milliseconds 1000
    } while ($true)
    throw 'Timed out after ten minutes waiting for this run to finish asset import and activity startup. Existing files and app state are preserved.'
}
try {
    if ($env:OS -ne 'Windows_NT') { throw 'Run this helper on the Windows computer with LDPlayer open.' }
    $verifier = Join-Path $PSScriptRoot 'build_android_game.py'
    $java = Join-Path $JavaHome 'bin\java.exe'
    foreach ($file in @($PythonPath, $AdbPath, $verifier, $java)) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf) -or (Get-Item -LiteralPath $file -Force).Length -eq 0) {
            throw "Required verification tool is missing or empty: $file"
        }
    }
    Write-Host 'Verifying the signed game APK, engine prerequisites, full game-data OBB and manifest before installation.'
    $verified = Invoke-Captured $PythonPath @('-I', '-B', '-u', $verifier, '--verify-only', '--sdk', $SdkRoot, '--java', $java) 600000
    if ($verified.Code -ne 0 -or $verified.Stdout -notmatch '(?m)^ANDROID GAME ARTIFACTS VERIFIED\r?$') {
        throw 'Game APK/OBB verification failed or did not confirm completion. Nothing was sent to Android.'
    }
    $apk = Require-RegularFile $repoRoot 'build/android-game/app/build/outputs/apk/debug/app-debug.apk'
    $obb = Require-RegularFile $repoRoot ('build/android-game-data/' + $obbName)
    $obbSha = (Get-FileHash -LiteralPath $obb -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Host "Verified local OBB: bytes=$((Get-Item -LiteralPath $obb).Length); SHA256=$obbSha"
    if ($Serial -match '^127\.0\.0\.1:[0-9]+$') {
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
    $install = Invoke-SelectedAdb @('install', '-r', $apk) 300000
    if ($install.Stdout -notmatch '(?m)^Success\r?$') { throw 'ADB did not confirm successful game APK installation.' }
    $null = Invoke-SelectedAdb @('shell', 'am', 'force-stop', $package)
    $remoteDirectory = '/sdcard/Android/obb/' + $package
    $remoteObb = $remoteDirectory + '/' + $obbName
    $incoming = $remoteObb + '.incoming-' + $runId
    $backup = $remoteObb + '.before-' + $runId
    $null = Invoke-SelectedAdb @('shell', 'mkdir', '-p', $remoteDirectory)
    $existing = Invoke-SelectedAdb @('shell', "if [ -f $remoteObb ]; then sha256sum $remoteObb; elif [ -e $remoteObb ]; then exit 2; else echo MISSING; fi") 300000
    if ($existing.Stdout -cmatch ('^' + $obbSha + '\s+' + [regex]::Escape($remoteObb) + '$')) {
        Write-Host 'The Android OBB already has the verified hash; transfer is unnecessary.'
    } else {
        if ($existing.Stdout -cne 'MISSING' -and $existing.Stdout -cnotmatch ('^[0-9a-f]{64}\s+' + [regex]::Escape($remoteObb) + '$')) {
            throw 'Could not determine the existing Android OBB hash. No remote file was replaced.'
        }
        Write-Host "Transferring OBB to $incoming (up to five minutes)."
        $null = Invoke-SelectedAdb @('push', $obb, $incoming) 300000
        $uploaded = Invoke-SelectedAdb @('shell', 'sha256sum', $incoming) 300000
        if ($uploaded.Stdout -cnotmatch ('^' + $obbSha + '\s+' + [regex]::Escape($incoming) + '$')) {
            throw "Uploaded OBB hash differs. Existing OBB preserved; incomplete transfer retained at $incoming"
        }
        if ($existing.Stdout -cne 'MISSING') {
            $null = Invoke-SelectedAdb @('shell', "[ ! -e $backup ] && mv $remoteObb $backup")
            Write-Host "Previous Android OBB preserved: $backup"
        }
        $null = Invoke-SelectedAdb @('shell', "[ ! -e $remoteObb ] && mv $incoming $remoteObb")
    }
    $launchAttempted = $true
    $launch = Invoke-SelectedAdb @('shell', 'am', 'start', '-W', '-n', $component, '--es', 'wesnoth_run_id', $runId)
    if ($launch.Stdout -notmatch '(?m)^Status:\s*ok\r?$' -or $launch.Text -match '(?m)^Error:') {
        throw 'Android did not confirm a successful game importer activity launch.'
    }
    $processResult = Invoke-SelectedAdb @('shell', 'pidof', $package)
    if ($processResult.Stdout -notmatch '^[1-9][0-9]*$') { throw 'The game must have exactly one identifiable running process.' }
    $appProcess = $processResult.Stdout
    Write-Host "Game PID=$appProcess; run_id=$runId. Waiting up to ten minutes for import and activity startup."
    Wait-GameImport
    $testExit = 0
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
} finally {
    if ($launchAttempted) {
        try { Save-AppLogs }
        catch { Write-Host ('ERROR saving game logs: ' + $_.Exception.Message) -ForegroundColor Red; $testExit = 2 }
    }
    if ($testExit -eq 0) {
        Write-Host 'ANDROID GAME LAUNCH CHECK PASSED'
        Write-Host 'Game data import and the Android game activity reported readiness. Rendering, menu input and gameplay still need your visual test.'
        Write-Host 'Leave Wesnoth Offline open. Report whether its title menu appears and responds; send the saved log and any visible error.'
    }
    exit $testExit
}
