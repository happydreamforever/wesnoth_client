# Windows PowerShell 5.1. Build the reusable Android font prefix and font raster/shaping probe offline.
[CmdletBinding()]
param(
    [string]$SdkRoot = (Join-Path $env:LOCALAPPDATA 'Android\Sdk'),
    [string]$PythonPath = (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\python.exe'),
    [string]$CMakePath = 'C:\Program Files\CMake\bin\cmake.exe',
    [string]$NinjaPath = 'D:\Test\msys64\ucrt64\bin\ninja.exe',
    [ValidateRange(1, 64)][int]$Jobs = 2,
    [string]$ArchiveDirectory = ''
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$buildExit = 2
try {
    if ($env:OS -ne 'Windows_NT') { throw 'Run this helper on the Windows build computer.' }
    $builder = Join-Path $PSScriptRoot 'build_android_fonts.py'
    foreach ($path in @($PythonPath, $CMakePath, $NinjaPath, $builder)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-Item -LiteralPath $path -Force).Length -eq 0) {
            throw "Required build tool is missing or empty: $path"
        }
    }
    $arguments = @('-I', $builder, '--ndk', (Join-Path $SdkRoot 'ndk\26.3.11579264'),
        '--cmake', $CMakePath, '--ninja', $NinjaPath, '--jobs', [string]$Jobs)
    if ($ArchiveDirectory) { $arguments += @('--archive-directory', $ArchiveDirectory) }
    Write-Host 'Building Android x86_64/API23 FreeType/HarfBuzz libraries and the font raster/shaping probe offline with NDK 26.3.11579264.'
    & $PythonPath @arguments
    if ($LASTEXITCODE -ne 0) { throw "Android fonts build failed with exit $LASTEXITCODE. Send the complete output." }
    $receipt = Join-Path $repoRoot 'build\android-fonts\receipt.json'
    if (-not (Test-Path -LiteralPath $receipt -PathType Leaf) -or (Get-Item -LiteralPath $receipt -Force).Length -eq 0) {
        throw 'The builder returned without its font artifact receipt. Do not run the Android test.'
    }
    Write-Host "ANDROID FONTS BUILD COMPLETE: $receipt"
    Write-Host 'Next: tools/Test-AndroidFonts.ps1 runs the font raster/shaping probe in LDPlayer. No APK is installed or changed.'
    $buildExit = 0
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
} finally { exit $buildExit }
