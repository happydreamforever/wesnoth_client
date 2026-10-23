# Windows PowerShell 5.1. Additional owned account in the existing LDPlayer.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9_]{3,24}$')][string]$Username,
    [Parameter(Mandatory=$true)][ValidateRange(1,2147483647)][int]$RoomId,
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._:-]*$')][string]$Serial = '127.0.0.1:5555',
    [string]$SdkRoot = (Join-Path $env:LOCALAPPDATA 'Android\Sdk'),
    [string]$AdbPath = (Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'),
    [string]$JavaHome = 'C:\Program Files\Android\Android Studio\jbr',
    [string]$PythonPath = (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\python.exe')
)
$ErrorActionPreference = 'Stop'
try {
    if ($env:OS -ne 'Windows_NT') { throw 'Run on Windows with the existing LDPlayer instance open.' }
    $script = Join-Path $PSScriptRoot 'run_android_individual_companion.py'
    $java = Join-Path $JavaHome 'bin\java.exe'
    foreach ($path in @($PythonPath, $AdbPath, $java, $script)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-Item -LiteralPath $path).Length -eq 0) {
            throw 'A required existing Python, Android or companion tool is missing.'
        }
    }
    # No transcript or output pipeline: passwords are hidden and the helper
    # writes only its sanitized structured report.
    & $PythonPath -I -B -u $script --username $Username --room-id $RoomId --serial $Serial --adb $AdbPath --sdk $SdkRoot --java $java
    exit $LASTEXITCODE
} catch {
    Write-Host 'The individual companion could not start. Check the selected existing tools, username and public Room ID.'
    exit 1
}
