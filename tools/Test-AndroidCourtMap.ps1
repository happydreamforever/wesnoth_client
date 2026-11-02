# Optional two-pointer instrumentation for the existing single LDPlayer.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateSet('world','area')][string]$Screen,
    [Parameter(Mandatory=$true)][switch]$MapIsOpen,
    [string]$Serial = '127.0.0.1:5555',
    [string]$SdkRoot = (Join-Path $env:LOCALAPPDATA 'Android\Sdk'),
    [string]$JavaHome = 'C:\Program Files\Android\Android Studio\jbr',
    [string]$PythonPath = (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe')
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$diagnostics = Join-Path $repo 'diagnostics'
$null = New-Item -ItemType Directory -Path $diagnostics -Force
$checkId = (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' + [guid]::NewGuid().ToString('N')
$report = Join-Path $diagnostics ('court-map-input-' + $checkId)
$log = $report + '.txt'
$started = $false
$code = 2
try {
    if (-not $MapIsOpen) { throw 'Open the World or Castle map first.' }
    Start-Transcript -LiteralPath $log -NoClobber | Out-Null
    $started = $true
    Write-Host 'Keep the map open. This installs a separate test companion and sends a pinch followed by one-finger panning.'
    Write-Host 'Do not touch the game until the check finishes. Game accounts, server configuration and saved data are not accessed.'
    & $PythonPath -I -B (Join-Path $PSScriptRoot 'run_android_court_map.py') `
        --sdk $SdkRoot --java-home $JavaHome --output $report `
        --adb (Join-Path $SdkRoot 'platform-tools\adb.exe') --serial $Serial --screen $Screen --map-is-open
    if ($LASTEXITCODE -ne 0) { throw 'Map input check stopped. Retain the text and diagnostic folder.' }
    $code = 0
} catch { Write-Host ('ERROR: ' + $_.Exception.Message) }
finally {
    if ($started) { Stop-Transcript | Out-Null }
    Write-Host "Saved log: $log"
    Write-Host "Diagnostic folder: $report"
}
exit $code
