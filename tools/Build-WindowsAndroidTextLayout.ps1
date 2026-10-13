# Windows PowerShell 5.1. Build the FriBidi/Pango Android prefix offline through MSYS Python.
[CmdletBinding()]
param(
    [string]$MsysRoot = 'D:\Test\wesnoth-setup\msys64',
    [string]$SdkRoot = (Join-Path $env:LOCALAPPDATA 'Android\Sdk'),
    [ValidateRange(1, 32)][int]$Jobs = 2,
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
    $bash = Join-Path $MsysRoot 'usr\bin\bash.exe'
    $builder = Join-Path $PSScriptRoot 'build_android_text_layout.py'
    $python = Join-Path $MsysRoot 'usr\bin\python3.exe'
    $cygpath = Join-Path $MsysRoot 'usr\bin\cygpath.exe'
    $repoRoot = Split-Path $PSScriptRoot -Parent
    $ndk = Join-Path $SdkRoot 'ndk\26.3.11579264'
    $taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
    foreach ($file in @($bash, $python, $cygpath, $builder, $taskkill)) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf) -or (Get-Item -LiteralPath $file -Force).Length -eq 0) {
            throw "Required build tool is missing or empty: $file"
        }
    }
    if (-not (Test-Path -LiteralPath $ndk -PathType Container)) { throw "Selected NDK directory is missing: $ndk" }
    $ndk = (Get-Item -LiteralPath $ndk -Force).FullName
    $launch = 'builder=$(/usr/bin/cygpath -u -- "$1") || exit; shift; exec /usr/bin/python3 -I -B -u "$builder" "$@"'
    $arguments = @('--noprofile', '--norc', '-c', $launch, 'wesnoth-text-layout-build',
        $builder.Replace('\', '/'), '--ndk', $ndk.Replace('\', '/'), '--jobs', [string]$Jobs)
    if ($ArchiveDirectory) {
        if (-not (Test-Path -LiteralPath $ArchiveDirectory -PathType Container)) { throw "Native archive folder is missing: $ArchiveDirectory" }
        $archive = (Get-Item -LiteralPath $ArchiveDirectory -Force).FullName.Replace('\', '/')
        $arguments += @('--archive-directory', $archive)
    }
    Write-Host 'Building Android FriBidi/Pango offline with MSYS Python and NDK 26.3.11579264; x86_64/API23.'
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo.FileName = (Get-Item -LiteralPath $bash -Force).FullName
    $process.StartInfo.Arguments = (($arguments | ForEach-Object { ConvertTo-ProcessArgument $_ }) -join ' ')
    $process.StartInfo.WorkingDirectory = $repoRoot
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.CreateNoWindow = $true
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    # Keep these changes in this one child process, not the user's PowerShell session.
    $process.StartInfo.EnvironmentVariables['PATH'] = '/usr/bin:/bin'
    $process.StartInfo.EnvironmentVariables['MSYSTEM'] = 'MSYS'
    $process.StartInfo.EnvironmentVariables.Remove('BASH_ENV')
    $process.StartInfo.EnvironmentVariables.Remove('ENV')
    $process.StartInfo.EnvironmentVariables.Remove('PYTHONHOME')
    $process.StartInfo.EnvironmentVariables.Remove('PYTHONPATH')
    $process.StartInfo.EnvironmentVariables['PYTHONDONTWRITEBYTECODE'] = '1'
    $process.StartInfo.EnvironmentVariables['PYTHONNOUSERSITE'] = '1'
    $null = $process.Start()
    $started = $true
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $stdout = $process.StandardOutput.ReadLineAsync()
    $stderr = $process.StandardError.ReadLineAsync()
    $confirmed = $false
    while ($null -ne $stdout -or $null -ne $stderr -or -not $process.HasExited) {
        if ($watch.ElapsedMilliseconds -ge $timeoutMilliseconds) {
            $timedOut = $true
            throw 'The build exceeded its two-hour limit. Send the output already printed.'
        }
        if ($null -ne $stdout -and $stdout.IsCompleted) {
            $line = $stdout.GetAwaiter().GetResult()
            if ($null -eq $line) { $stdout = $null }
            else {
                Write-Host $line
                if ($line -ceq 'ANDROID TEXT LAYOUT BUILD PASS') { $confirmed = $true }
                $stdout = $process.StandardOutput.ReadLineAsync()
            }
        }
        if ($null -ne $stderr -and $stderr.IsCompleted) {
            $line = $stderr.GetAwaiter().GetResult()
            if ($null -eq $line) { $stderr = $null }
            else {
                Write-Host $line
                $stderr = $process.StandardError.ReadLineAsync()
            }
        }
        Start-Sleep -Milliseconds 25
    }
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) { throw "Android text_layout build failed with exit $($process.ExitCode). Send all output." }
    if (-not $confirmed) { throw 'The build returned without its required completion marker. Send all output.' }
    $receipt = Join-Path $repoRoot 'build\android-text-layout\receipt.json'
    if (-not (Test-Path -LiteralPath $receipt -PathType Leaf) -or (Get-Item -LiteralPath $receipt -Force).Length -eq 0) {
        throw 'The builder returned without its text_layout artifact receipt. Do not run the device test.'
    }
    Write-Host "ANDROID TEXT LAYOUT BUILD COMPLETE: $receipt"
    Write-Host 'Next: tools/Test-AndroidTextLayout.ps1 verifies and runs the probe in LDPlayer. No APK is installed or changed.'
    $buildExit = 0
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
} finally {
    try { Stop-OwnedProcessTree }
    catch { Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red; $buildExit = 2 }
    if ($null -ne $process) { $process.Dispose() }
    exit $buildExit
}
