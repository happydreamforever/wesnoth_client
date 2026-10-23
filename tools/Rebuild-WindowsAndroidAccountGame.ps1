# Windows PowerShell 5.1. Preserve the completed account stages, then build/resume the session and input update.
[CmdletBinding()]
param(
    [string]$SdkRoot = (Join-Path $env:LOCALAPPDATA 'Android\Sdk'),
    [string]$PythonPath = (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\python.exe'),
    [string]$JavaHome = 'C:\Program Files\Android\Android Studio\jbr',
    [string]$GradleUserHome = (Join-Path $env:USERPROFILE '.gradle'),
    [string]$ArchiveDirectory = '',
    [string]$CMakePath = 'C:\Program Files\CMake\bin\cmake.exe',
    [string]$NinjaPath = 'D:\Test\wesnoth-setup\msys64\ucrt64\bin\ninja.exe'
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$started = $false
$result = 2
$log = $null
try {
    if ($env:OS -ne 'Windows_NT') { throw 'Run this helper on the Windows computer.' }
    $builder = Join-Path $PSScriptRoot 'build_android_account_game.py'
    foreach ($file in @($PythonPath, $builder)) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf) -or (Get-Item -LiteralPath $file -Force).Length -eq 0) {
            throw "Required update tool is missing or empty: $file"
        }
    }
    $diagnostics = Join-Path $repo 'diagnostics'
    $null = New-Item -ItemType Directory -Path $diagnostics -Force
    $log = Join-Path $diagnostics ('android-account-rebuild-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' + [guid]::NewGuid().ToString('N') + '.txt')
    Start-Transcript -LiteralPath $log -NoClobber | Out-Null
    $started = $true
    Write-Host 'Verifying and preserving both completed 1192608, 1192609, 1192610, 1192611, 1192612, 1192613, 1192614, 1192615, 1192616, 1192617 or 1192618 account stages before the 1192619 individual battle update.'
    Write-Host 'Recorded preparation reuses its retained backups. If an operation lock remains, stop and send the log.'
    & $PythonPath -I -B -u $builder --prepare-update
    if ($LASTEXITCODE -ne 0) { throw 'Account update preparation stopped. No build was started. Preserve all stages/backups and send this log.' }
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $PSScriptRoot 'Build-WindowsAndroidAccountGame.ps1'),
        '-SdkRoot', $SdkRoot, '-PythonPath', $PythonPath, '-JavaHome', $JavaHome,
        '-GradleUserHome', $GradleUserHome, '-CMakePath', $CMakePath, '-NinjaPath', $NinjaPath)
    if ($ArchiveDirectory) { $arguments += @('-ArchiveDirectory', $ArchiveDirectory) }
    & powershell.exe @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Account update build stopped. Original stages remain in their recorded backups. Send the rebuild/build logs.' }
    Write-Host 'ANDROID ACCOUNT GAME REBUILD COMPLETE'
    Write-Host 'Next: tools/Install-AndroidAccountGame.ps1 -KeepSavedProfile updates the existing app without reimporting its trusted server.'
    $result = 0
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
} finally {
    if ($started) { Stop-Transcript | Out-Null }
    if ($log) { Write-Host "Saved rebuild log: $log" }
}
exit $result
