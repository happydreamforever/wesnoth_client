# Windows PowerShell 5.1. Build only the Alliance Android transport check offline.
[CmdletBinding()]
param(
    [string]$SdkRoot = (Join-Path $env:LOCALAPPDATA 'Android\Sdk'),
    [string]$PythonPath = (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\python.exe'),
    [string]$CMakePath = 'C:\Program Files\CMake\bin\cmake.exe',
    [string]$NinjaPath = 'D:\Test\wesnoth-setup\msys64\ucrt64\bin\ninja.exe',
    [ValidateRange(1, 32)][int]$Jobs = 2
)
$ErrorActionPreference = 'Stop'
$buildExit = 2
$transcribing = $false
$log = $null
try {
    if ($env:OS -ne 'Windows_NT') { throw 'Run this helper on the Windows computer.' }
    $repo = Split-Path $PSScriptRoot -Parent
    $diagnostics = Join-Path $repo 'diagnostics'
    $null = New-Item -ItemType Directory -Path $diagnostics -Force
    $log = Join-Path $diagnostics ('android-alliance-build-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' + [guid]::NewGuid().ToString('N') + '.txt')
    Start-Transcript -LiteralPath $log -NoClobber | Out-Null
    $transcribing = $true
    $builder = Join-Path $PSScriptRoot 'build_android_alliance.py'
    $ndk = Join-Path $SdkRoot 'ndk\26.3.11579264'
    foreach ($path in @($PythonPath, $CMakePath, $NinjaPath, $builder)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-Item -LiteralPath $path -Force).Length -eq 0) {
            throw "Required build tool is missing or empty: $path"
        }
    }
    if (-not (Test-Path -LiteralPath $ndk -PathType Container)) { throw "Selected NDK is missing: $ndk" }
    $arguments = @('-I', '-B', '-u', $builder, '--ndk', (Get-Item -LiteralPath $ndk -Force).FullName,
        '--cmake', (Get-Item -LiteralPath $CMakePath -Force).FullName,
        '--ninja', (Get-Item -LiteralPath $NinjaPath -Force).FullName, '--jobs', [string]$Jobs)
    Write-Host 'Building the Android Alliance transport check offline; x86_64/API23, NDK 26.3.11579264.'
    Write-Host 'Existing Boost, OpenSSL and C++ runtime are reused. The installed game and database are unchanged.'
    $confirmed = $false
    # Windows PowerShell wraps native stderr as ErrorRecords. Treat it as logged
    # tool output here; use the native exit code and exact completion marker.
    $savedPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $PythonPath @arguments 2>&1 | ForEach-Object {
            $line = [string]$_
            Write-Host $line
            if ($_ -isnot [System.Management.Automation.ErrorRecord] -and
                $line -ceq 'ANDROID ALLIANCE BUILD PASS') { $confirmed = $true }
        }
        $nativeExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $savedPreference }
    if ($nativeExit -ne 0) { throw "Android Alliance build failed with exit $nativeExit. Send the saved log." }
    if (-not $confirmed) { throw 'Build exited without its required completion marker. Send the saved log.' }
    $receipt = Join-Path $repo 'build\android-alliance\receipt.json'
    if (-not (Test-Path -LiteralPath $receipt -PathType Leaf) -or (Get-Item -LiteralPath $receipt -Force).Length -eq 0) {
        throw 'Build completed without its artifact receipt. Send the saved log.'
    }
    Write-Host "ANDROID ALLIANCE BUILD COMPLETE: $receipt"
    Write-Host 'Next: tools\Test-AndroidAlliance.ps1 runs the standalone check in LDPlayer.'
    $buildExit = 0
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
} finally {
    if ($transcribing) { Stop-Transcript | Out-Null }
    if ($null -ne $log) { Write-Host "Saved log: $log" }
}
exit $buildExit
