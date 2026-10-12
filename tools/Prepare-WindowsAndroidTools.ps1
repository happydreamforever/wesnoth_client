# Windows PowerShell 5.1. Explicitly connected setup; no APK/native compilation.
[CmdletBinding()]
param(
    [switch]$Download,
    [string]$SdkRoot = (Join-Path $env:LOCALAPPDATA 'Android\Sdk'),
    [string]$JavaHome = 'C:\Program Files\Android\Android Studio\jbr',
    [string]$PythonPath = (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\python.exe'),
    [string]$GradleUserHome = (Join-Path $env:USERPROFILE '.gradle')
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$previousEnvironment = @{}
$setupExit = 2

function Require-SetupFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf) -or (Get-Item -LiteralPath $Path -Force).Length -eq 0) {
        throw "Required setup input is missing or empty: $Path"
    }
}
function Invoke-SetupCommand([string]$Label, [string]$Executable, [string[]]$Arguments) {
    Write-Host "SETUP: $Label"
    # Leave stdin/stdout/stderr attached so sdkmanager can display license prompts.
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Label failed with exit $LASTEXITCODE. Send the complete console output." }
}
try {
    if ($env:OS -ne 'Windows_NT') { throw 'Run this helper on the Windows build computer.' }
    if (-not $Download) { throw 'Connected dependency setup requires -Download. Use Test-WindowsAndroidPrerequisites.ps1 for an offline check.' }
    $profile = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'android-build-profile.json') -Raw | ConvertFrom-Json
    if ($profile.schema_version -ne 1 -or $profile.ndk_version -ne '26.3.11579264' -or $profile.gradle_version -ne '8.9') {
        throw 'Unexpected Android build profile.'
    }
    $stage = Join-Path $repoRoot 'build\android-source'
    $project = Join-Path $stage 'packaging\android'
    $wrapper = Join-Path $project 'gradle\wrapper\gradle-wrapper.jar'
    $java = Join-Path $JavaHome 'bin\java.exe'
    $manager = Join-Path $SdkRoot 'cmdline-tools\latest\bin\sdkmanager.bat'
    $scons = Join-Path $PSScriptRoot 'prepare_scons.py'
    $generator = Join-Path $PSScriptRoot 'prepare_android_source.py'
    $init = Join-Path $PSScriptRoot 'android-cache.init.gradle'
    foreach ($path in @($java, $manager, $PythonPath, $wrapper, $scons, $generator, $init,
            (Join-Path $stage '.wesnoth-android-stage.json'))) { Require-SetupFile $path }
    # The required existing marker prevents this verification from creating a new stage.
    # Reuse the generator's exact-byte/provenance/extra-file checks; no files are overwritten.
    Invoke-SetupCommand 'Verify existing Android source preparation' $PythonPath @('-I', $generator)
    foreach ($name in @('JAVA_HOME', 'ANDROID_HOME', 'ANDROID_SDK_ROOT', 'GRADLE_USER_HOME')) {
        $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
    $env:JAVA_HOME = $JavaHome
    $env:ANDROID_HOME = $SdkRoot
    $env:ANDROID_SDK_ROOT = $SdkRoot
    $env:GRADLE_USER_HOME = $GradleUserHome
    Write-Host 'Android tools setup 2026-10-12.1. Internet access is required for missing packages.'
    Write-Host 'Review any SDK license prompt and answer it yourself. Existing NDK versions are retained.'
    Invoke-SetupCommand 'Install NDK 26.3.11579264' $manager @("--sdk_root=$SdkRoot", '--install', 'ndk;26.3.11579264')
    $ndkProperties = Join-Path $SdkRoot 'ndk\26.3.11579264\source.properties'
    Require-SetupFile $ndkProperties
    if ((Get-Content -LiteralPath $ndkProperties -Raw) -notmatch '(?m)^Pkg\.Revision\s*=\s*26\.3\.11579264\s*$') {
        throw 'The selected NDK was not installed with the expected revision.'
    }
    Invoke-SetupCommand 'Prepare private SCons 4.11.0' $PythonPath @('-I', $scons, 'prepare', '--download')
    $gradleArguments = @('-classpath', $wrapper, 'org.gradle.wrapper.GradleWrapperMain',
        '--no-daemon', '--console=plain', '--project-dir', $project,
        '--project-cache-dir', (Join-Path $repoRoot 'build\android-gradle-cache'),
        '--init-script', $init, ':wesnothResolveAndroidDependencies')
    Invoke-SetupCommand 'Download selected Gradle and Android Java artifacts' $java $gradleArguments
    Invoke-SetupCommand 'Repeat dependency resolution with Gradle --offline' $java ($gradleArguments + @('--offline'))
    Write-Host 'SETUP COMPLETE: selected tools and Android Java artifact resolution prepared.'
    Write-Host 'Next: Test-WindowsAndroidPrerequisites.ps1 checks the installed inputs through MSYS2.'
    Write-Host 'Native Android compilation and APK packaging remain pending; this did not build or run an APK.'
    $setupExit = 0
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
} finally {
    foreach ($name in $previousEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], 'Process')
    }
    exit $setupExit
}
