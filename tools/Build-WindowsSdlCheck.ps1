# Windows PowerShell 5.1. Build the isolated SDL test APK using prepared offline tools.
[CmdletBinding()]
param(
    [string]$SdkRoot = (Join-Path $env:LOCALAPPDATA 'Android\Sdk'),
    [string]$JavaHome = 'C:\Program Files\Android\Android Studio\jbr',
    [string]$PythonPath = (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\python.exe'),
    [string]$CMakePath = 'C:\Program Files\CMake\bin\cmake.exe',
    [string]$NinjaPath = 'D:\Test\msys64\ucrt64\bin\ninja.exe',
    [string]$GradleUserHome = (Join-Path $env:USERPROFILE '.gradle'),
    [ValidateRange(1, 64)][int]$Jobs = 2,
    [string]$ArchiveDirectory = ''
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$buildExit = 2
try {
    if ($env:OS -ne 'Windows_NT') { throw 'Run this helper on the Windows build computer.' }
    $builder = Join-Path $PSScriptRoot 'build_android_sdl_check.py'
    $java = Join-Path $JavaHome 'bin\java.exe'
    foreach ($path in @($PythonPath, $CMakePath, $NinjaPath, $java, $builder)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-Item -LiteralPath $path -Force).Length -eq 0) {
            throw "Required build tool is missing or empty: $path"
        }
    }
    foreach ($relative in @('platforms\android-35\source.properties', 'build-tools\34.0.0\source.properties')) {
        $properties = Join-Path $SdkRoot $relative
        if (-not (Test-Path -LiteralPath $properties -PathType Leaf)) { throw "SDK metadata is missing: $properties" }
        $revision = @(Get-Content -LiteralPath $properties | Where-Object { $_ -match '^Pkg\.Revision\s*=' })
        if ($revision.Count -ne 1) { throw "SDK metadata does not identify one revision: $properties" }
        Write-Host "SDK $relative : $($revision[0])"
    }
    $arguments = @('-I', $builder, '--ndk', (Join-Path $SdkRoot 'ndk\26.3.11579264'),
        '--cmake', $CMakePath, '--ninja', $NinjaPath, '--java', $java, '--sdk', $SdkRoot,
        '--gradle-user-home', $GradleUserHome, '--jobs', [string]$Jobs)
    if ($ArchiveDirectory) { $arguments += @('--archive-directory', $ArchiveDirectory) }
    Write-Host 'Building Wesnoth SDL Check offline: SDL 3.4.2, Android x86_64/API23, Gradle 8.9/AGP 8.7.3.'
    & $PythonPath @arguments
    if ($LASTEXITCODE -ne 0) { throw "SDL APK build failed with exit $LASTEXITCODE. Send the complete output." }
    $receipt = Join-Path $repoRoot 'build\android-sdl-apk\receipt.json'
    if (-not (Test-Path -LiteralPath $receipt -PathType Leaf) -or (Get-Item -LiteralPath $receipt -Force).Length -eq 0) {
        throw 'The builder returned without its APK receipt. Do not install the test app.'
    }
    Write-Host "SDL CHECK APK BUILD COMPLETE: $receipt"
    Write-Host 'Next: tools/Test-AndroidSdlCheck.ps1 installs and exercises the SDL test app. This is not the game client.'
    $buildExit = 0
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
} finally { exit $buildExit }
