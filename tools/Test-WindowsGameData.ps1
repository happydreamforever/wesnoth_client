# Windows PowerShell 5.1. Read-only verification of the copied game data.
[CmdletBinding()]
param(
    [string]$DataRoot = (Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'gamedata-1.19.26'),
    [string]$PythonPath = (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe')
)
$ErrorActionPreference = 'Stop'
$testExit = 2
try {
    if ($env:OS -ne 'Windows_NT') { throw 'Run this helper on the Windows computer.' }
    $verifier = Join-Path $PSScriptRoot 'verify_game_data.py'
    foreach ($path in @($PythonPath, $verifier)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-Item -LiteralPath $path -Force).Length -eq 0) {
            throw "Required verification tool is missing or empty: $path"
        }
    }
    & $PythonPath '-I' '-B' '-u' $verifier '--data-root' $DataRoot
    $testExit = $LASTEXITCODE
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
} finally { exit $testExit }
