# Windows PowerShell 5.1. One explicitly authorized computer-controlled seat.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9_]{3,24}$')][string]$Username,
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$')][string]$BattleId,
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._:-]*$')][string]$Serial = '127.0.0.1:5555',
    [string]$SdkRoot = (Join-Path $env:LOCALAPPDATA 'Android\Sdk'),
    [string]$AdbPath = (Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'),
    [string]$JavaHome = 'C:\Program Files\Android\Android Studio\jbr',
    [string]$PythonPath = (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\python.exe'),
    [switch]$RetryResult,
    [ValidateSet('Surrender', 'OfferDraw', 'WithdrawDraw')][string]$CaptainAction,
    [ValidateRange(1,100)][int]$AfterTurn
)
$ErrorActionPreference = 'Stop'
try {
    if ($env:OS -ne 'Windows_NT') { throw 'Run on Windows with the existing LDPlayer instance open.' }
    $hasAction = $PSBoundParameters.ContainsKey('CaptainAction')
    $hasTurn = $PSBoundParameters.ContainsKey('AfterTurn')
    if (($hasAction -ne $hasTurn) -or ($RetryResult -and $hasAction)) {
        throw 'CaptainAction and AfterTurn must be supplied together, without RetryResult.'
    }
    $script = Join-Path $PSScriptRoot 'run_android_alliance_companion.py'
    $java = Join-Path $JavaHome 'bin\java.exe'
    foreach ($path in @($PythonPath, $AdbPath, $java, $script)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-Item -LiteralPath $path).Length -eq 0) {
            throw 'A required existing Python, Android or companion tool is missing.'
        }
    }
    # Deliberately no transcript or pipeline: Python owns the hidden password
    # prompt, memory-only session and sanitized report. No credential arguments.
    $arguments = @('-I', '-B', '-u', $script, '--username', $Username, '--battle-id', $BattleId, '--serial', $Serial, '--adb', $AdbPath, '--sdk', $SdkRoot, '--java', $java)
    if ($RetryResult) { $arguments += '--retry-result' }
    if ($hasAction) {
        $actions = @{ Surrender = 'surrender'; OfferDraw = 'offer_draw'; WithdrawDraw = 'withdraw_draw' }
        $arguments += @('--captain-action', $actions[$CaptainAction], '--after-turn', [string]$AfterTurn)
    }
    & $PythonPath @arguments
    exit $LASTEXITCODE
} catch {
    Write-Host 'The companion launcher could not start. Check the selected existing tools and public account/battle identifiers.'
    exit 1
}
