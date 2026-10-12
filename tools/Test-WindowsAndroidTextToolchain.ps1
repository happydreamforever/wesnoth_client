# Windows PowerShell 5.1. Check the selected MSYS/NDK bridge in owned temporary space.
[CmdletBinding()]
param(
    [string]$MsysRoot = 'D:\Test\msys64',
    [string]$SdkRoot = (Join-Path $env:LOCALAPPDATA 'Android\Sdk')
)
$ErrorActionPreference = 'Stop'
$testExit = 2
$scratch = $null
$process = $null
$started = $false
$timedOut = $false
$scratchCreated = $false
$taskkill = $null
$timeoutMilliseconds = 600000

function ConvertTo-ProcessArgument([string]$Value) {
    $escaped = $Value -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1'
    return '"' + $escaped + '"'
}
function Stop-OwnedProcessTree {
    if ($started -and $null -ne $process -and -not $process.HasExited) {
        Write-Host 'Stopping only the timed-out or interrupted diagnostic process tree.'
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
                throw 'Taskkill timed out; the diagnostic process tree could not be confirmed stopped.'
            }
            if (-not $stopOut.Wait(2000) -or -not $stopErr.Wait(2000)) { throw 'Taskkill output did not close.' }
            $message = ($stopOut.Result + "`n" + $stopErr.Result).Trim()
            if ($message) { Write-Host $message }
            if ($stopper.ExitCode -ne 0) { throw "Taskkill failed with exit $($stopper.ExitCode); the diagnostic process tree may still be running." }
        } finally { $stopper.Dispose() }
        if (-not $process.WaitForExit(5000)) {
            throw 'The diagnostic process did not stop after taskkill. Its scratch folder is preserved.'
        }
    } elseif ($timedOut -and $started) {
        Write-Host 'The diagnostic exited but its output stayed open. Remaining child processes could not be confirmed stopped.'
    }
}
try {
    if ($env:OS -ne 'Windows_NT') { throw 'Run this helper on the Windows computer.' }
    $bash = Join-Path $MsysRoot 'usr\bin\bash.exe'
    $helper = Join-Path $PSScriptRoot 'check_android_text_toolchain.sh'
    $ndk = Join-Path $SdkRoot 'ndk\26.3.11579264'
    $taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
    foreach ($file in @($bash, $helper, $taskkill)) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf) -or (Get-Item -LiteralPath $file -Force).Length -eq 0) {
            throw "Required diagnostic tool is missing or empty: $file"
        }
    }
    if (-not (Test-Path -LiteralPath $ndk -PathType Container)) { throw "Selected NDK directory is missing: $ndk" }
    $ndk = (Get-Item -LiteralPath $ndk -Force).FullName
    $scratch = Join-Path ([IO.Path]::GetTempPath()) ('wesnoth text toolchain ' + [guid]::NewGuid().ToString('N'))
    if (Test-Path -LiteralPath $scratch) { throw 'The new scratch name already exists. Nothing overwritten.' }
    $null = New-Item -ItemType Directory -Path $scratch
    $scratchCreated = $true
    Write-Host 'Checking the MSYS/Android NDK text-toolchain bridge; no dependencies will be installed.'
    Write-Host "Diagnostic scratch directory: $scratch"
    $arguments = @('--noprofile', '--norc', $helper.Replace('\', '/'), $ndk.Replace('\', '/'), $scratch.Replace('\', '/'))
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo.FileName = (Get-Item -LiteralPath $bash -Force).FullName
    $process.StartInfo.Arguments = (($arguments | ForEach-Object { ConvertTo-ProcessArgument $_ }) -join ' ')
    $process.StartInfo.WorkingDirectory = $scratch
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.CreateNoWindow = $true
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    # Keep these changes in this one child process, not the user's PowerShell session.
    $process.StartInfo.EnvironmentVariables['PATH'] = '/usr/bin:/bin'
    $process.StartInfo.EnvironmentVariables['MSYSTEM'] = 'MSYS'
    $process.StartInfo.EnvironmentVariables.Remove('BASH_ENV')
    $process.StartInfo.EnvironmentVariables.Remove('ENV')
    $null = $process.Start()
    $started = $true
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $stdout = $process.StandardOutput.ReadLineAsync()
    $stderr = $process.StandardError.ReadLineAsync()
    $confirmed = $false
    while ($null -ne $stdout -or $null -ne $stderr -or -not $process.HasExited) {
        if ($watch.ElapsedMilliseconds -ge $timeoutMilliseconds) {
            $timedOut = $true
            throw 'The diagnostic exceeded its 600-second limit. Send the output already printed.'
        }
        if ($null -ne $stdout -and $stdout.IsCompleted) {
            $line = $stdout.GetAwaiter().GetResult()
            if ($null -eq $line) { $stdout = $null }
            else {
                Write-Host $line
                if ($line -ceq 'ANDROID TEXT TOOLCHAIN CHECK PASSED') { $confirmed = $true }
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
    if ($process.ExitCode -ne 0) { throw "Text-toolchain diagnostic failed with exit $($process.ExitCode). Send all output." }
    if (-not $confirmed) { throw 'The diagnostic returned without its required completion marker. Send all output.' }
    if ((Get-Item -LiteralPath $scratch -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'The owned scratch directory was redirected; it has been preserved.'
    }
    Remove-Item -LiteralPath $scratch -Recurse -Force
    Write-Host 'Diagnostic scratch removed. Completed build stages were not changed.'
    $scratch = $null
    $testExit = 0
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
} finally {
    try { Stop-OwnedProcessTree }
    catch { Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red; $testExit = 2 }
    if ($null -ne $process) { $process.Dispose() }
    if ($scratchCreated -and $null -ne $scratch -and (Test-Path -LiteralPath $scratch)) {
        Write-Host "Diagnostic scratch preserved: $scratch"
    }
    exit $testExit
}
