# Windows PowerShell 5.1. Package the verified native engine and asset manifest into the offline game APK.
[CmdletBinding()]
param(
    [string]$SdkRoot = (Join-Path $env:LOCALAPPDATA 'Android\Sdk'),
    [string]$PythonPath = (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\python.exe'),
    [string]$JavaHome = 'C:\Program Files\Android\Android Studio\jbr',
    [string]$GradleUserHome = (Join-Path $env:USERPROFILE '.gradle'),
    [string]$ArchiveDirectory = ''
)
$ErrorActionPreference = 'Stop'
$buildExit = 2
$process = $null
$started = $false
$timedOut = $false
$taskkill = $null
$timeoutMilliseconds = 7200000

function ConvertTo-ProcessArgument([string]$Value) {
    $escaped = $Value -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1'
    return '"' + $escaped + '"'
}
function Stop-OwnedProcessTree {
    if ($started -and $null -ne $process -and -not $process.HasExited) {
        Write-Host 'Stopping only the timed-out or interrupted build process tree.'
        $stopper = New-Object System.Diagnostics.Process
        try {
            $stopper.StartInfo.FileName = $taskkill
            $stopper.StartInfo.Arguments = '/PID ' + $process.Id + ' /T /F'
            $stopper.StartInfo.UseShellExecute = $false
            $stopper.StartInfo.CreateNoWindow = $true
            $stopper.StartInfo.RedirectStandardOutput = $true
            $stopper.StartInfo.RedirectStandardError = $true
            $null = $stopper.Start()
            $stopOut = $stopper.StandardOutput.ReadToEndAsync()
            $stopErr = $stopper.StandardError.ReadToEndAsync()
            if (-not $stopper.WaitForExit(5000)) {
                $stopper.Kill()
                throw 'Taskkill timed out; the build process tree could not be confirmed stopped.'
            }
            if (-not $stopOut.Wait(2000) -or -not $stopErr.Wait(2000)) { throw 'Taskkill output did not close.' }
            $message = ($stopOut.Result + "`n" + $stopErr.Result).Trim()
            if ($message) { Write-Host $message }
            if ($stopper.ExitCode -ne 0) { throw "Taskkill failed with exit $($stopper.ExitCode); the build process tree may still be running." }
        } finally { $stopper.Dispose() }
        if (-not $process.WaitForExit(5000)) {
            throw 'The build process did not stop after taskkill. Build outputs are preserved.'
        }
    } elseif ($timedOut -and $started) {
        Write-Host 'The build exited but its output stayed open. Remaining child processes could not be confirmed stopped.'
    }
}
try {
    if ($env:OS -ne 'Windows_NT') { throw 'Run this helper on the Windows computer.' }
    $builder = Join-Path $PSScriptRoot 'build_android_game.py'
    $repoRoot = Split-Path $PSScriptRoot -Parent
    $ndk = Join-Path $SdkRoot 'ndk\26.3.11579264'
    $taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
    $java = Join-Path $JavaHome 'bin\java.exe'
    foreach ($file in @($PythonPath, $java, $builder, $taskkill)) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf) -or (Get-Item -LiteralPath $file -Force).Length -eq 0) {
            throw "Required build tool is missing or empty: $file"
        }
    }
    if (-not (Test-Path -LiteralPath $ndk -PathType Container)) { throw "Selected NDK directory is missing: $ndk" }
    $ndk = (Get-Item -LiteralPath $ndk -Force).FullName
    $arguments = @('-I', '-B', '-u', $builder, '--ndk', $ndk,
        '--sdk', (Get-Item -LiteralPath $SdkRoot -Force).FullName,
        '--java', (Get-Item -LiteralPath $java -Force).FullName, '--gradle-user-home', $GradleUserHome)
    if ($ArchiveDirectory) { $arguments += @('--archive-directory', $ArchiveDirectory) }
    Write-Host 'Packaging Wesnoth Offline 1.19.26 with the completed engine and verified game-data manifest; Gradle offline mode, x86_64/API23.'
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo.FileName = (Get-Item -LiteralPath $PythonPath -Force).FullName
    $process.StartInfo.Arguments = (($arguments | ForEach-Object { ConvertTo-ProcessArgument $_ }) -join ' ')
    $process.StartInfo.WorkingDirectory = $repoRoot
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.CreateNoWindow = $true
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    $process.StartInfo.RedirectStandardInput = $true
    # Keep these changes in this one child process, not the user's PowerShell session.
    $process.StartInfo.EnvironmentVariables.Remove('BASH_ENV')
    $process.StartInfo.EnvironmentVariables.Remove('ENV')
    $process.StartInfo.EnvironmentVariables.Remove('PYTHONHOME')
    $process.StartInfo.EnvironmentVariables.Remove('PYTHONPATH')
    $process.StartInfo.EnvironmentVariables['PYTHONDONTWRITEBYTECODE'] = '1'
    $process.StartInfo.EnvironmentVariables['PYTHONNOUSERSITE'] = '1'
    $null = $process.Start()
    $started = $true
    $process.StandardInput.Close()
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $stdout = $process.StandardOutput.ReadLineAsync()
    $stderr = $process.StandardError.ReadLineAsync()
    $confirmed = $false
    while ($null -ne $stdout -or $null -ne $stderr -or -not $process.HasExited) {
        if ($watch.ElapsedMilliseconds -ge $timeoutMilliseconds) {
            $timedOut = $true
            throw 'The build exceeded its two-hour limit. Send the output already printed.'
        }
        $madeProgress = $false
        if ($null -ne $stdout -and $stdout.IsCompleted) {
            $madeProgress = $true
            $line = $stdout.GetAwaiter().GetResult()
            if ($null -eq $line) { $stdout = $null }
            else {
                Write-Host $line
                if ($line -ceq 'ANDROID GAME BUILD PASS') { $confirmed = $true }
                $stdout = $process.StandardOutput.ReadLineAsync()
            }
        }
        if ($null -ne $stderr -and $stderr.IsCompleted) {
            $madeProgress = $true
            $line = $stderr.GetAwaiter().GetResult()
            if ($null -eq $line) { $stderr = $null }
            else {
                Write-Host $line
                $stderr = $process.StandardError.ReadLineAsync()
            }
        }
        # Drain ready stdout and stderr immediately; only idle waits need a sleep.
        if (-not $madeProgress) { Start-Sleep -Milliseconds 25 }
    }
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) { throw "Android game APK build failed with exit $($process.ExitCode). Send all output." }
    if (-not $confirmed) { throw 'The build returned without its required completion marker. Send all output.' }
    $receipt = Join-Path $repoRoot 'build\android-game\receipt.json'
    if (-not (Test-Path -LiteralPath $receipt -PathType Leaf) -or (Get-Item -LiteralPath $receipt -Force).Length -eq 0) {
        throw 'The builder returned without its game APK receipt. Do not run the device test.'
    }
    Write-Host "ANDROID GAME BUILD COMPLETE: $receipt"
    Write-Host 'Next: tools/Test-AndroidGame.ps1 verifies and installs the APK/OBB, waits for asset import, and leaves the game open for visual testing.'
    $buildExit = 0
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
} finally {
    try { Stop-OwnedProcessTree }
    catch { Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red; $buildExit = 2 }
    if ($null -ne $process) { $process.Dispose() }
    exit $buildExit
}
