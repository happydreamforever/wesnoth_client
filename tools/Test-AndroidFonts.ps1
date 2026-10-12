# Windows PowerShell 5.1. Executes only the verified font raster/shaping probe on the selected device.
[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._:-]*$')][string]$Serial = '127.0.0.1:5555',
    [string]$AdbPath = (Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'),
    [string]$DataRoot = (Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'gamedata-1.19.26')
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$buildRoot = Join-Path $repoRoot 'build\android-fonts'
$testExit = 2

function Require-RegularFile([string]$Root, [string]$Relative) {
    $file = Join-Path $Root $Relative
    if (-not (Test-Path -LiteralPath $file -PathType Leaf) -or (Get-Item -LiteralPath $file -Force).Length -eq 0) {
        throw "Required input is missing or empty: $file"
    }
    # Do not follow an artifact or input redirected outside its recorded tree.
    $cursor = $file
    while ($cursor.Length -ge $Root.Length) {
        if ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Symbolic links/reparse points are not accepted for native test inputs: $cursor"
        }
        if ($cursor -eq $Root) { break }
        $cursor = Split-Path $cursor -Parent
    }
    return $file
}
function Test-RecordedFiles([string]$Root, $Records, [string[]]$Names) {
    $actual = @($Records.PSObject.Properties.Name | Sort-Object)
    if (($actual -join '|') -cne (($Names | Sort-Object) -join '|')) { throw 'Native receipt has unexpected or missing file entries.' }
    foreach ($relative in $Names) {
        $entry = $Records.$relative
        if ($entry.bytes -le 0 -or $entry.sha256 -notmatch '^[0-9a-f]{64}$') { throw "Malformed native receipt entry: $relative" }
        $file = Require-RegularFile $Root $relative
        if ((Get-Item -LiteralPath $file -Force).Length -ne $entry.bytes -or
            (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $entry.sha256) {
            throw "Native test input differs from its receipt: $relative. Rebuild; do not push these files."
        }
    }
}
function Test-FontsPrefix($Records) {
    $names = @($Records.PSObject.Properties.Name)
    if ($names.Count -eq 0) { throw 'The fonts receipt has no installed-prefix file inventory.' }
    foreach ($relative in $names) {
        # Prefix entries are data/build outputs only. Never follow arbitrary paths
        # from receipt JSON, hidden files, parent traversal or drive-qualified names.
        if ($relative -notmatch '^prefix/(?:bin|lib|include|share)/[A-Za-z0-9_+./-]+$' -or
            @($relative.Split('/') | Where-Object { -not $_ -or $_.StartsWith('.') }).Count -ne 0) {
            throw "Unsafe installed-prefix receipt path: $relative"
        }
    }
    $prefix = Join-Path $buildRoot 'prefix'
    $pending = New-Object 'System.Collections.Generic.Queue[string]'
    $pending.Enqueue($prefix)
    $actual = New-Object 'System.Collections.Generic.List[string]'
    while ($pending.Count -gt 0) {
        $directory = $pending.Dequeue()
        if ((Get-Item -LiteralPath $directory -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Redirected installed-prefix directory: $directory"
        }
        foreach ($item in @(Get-ChildItem -LiteralPath $directory -Force)) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Redirected installed-prefix entry: $($item.FullName)"
            }
            if ($item.PSIsContainer) { $pending.Enqueue($item.FullName) }
            else { $actual.Add($item.FullName.Substring($buildRoot.Length + 1).Replace('\', '/')) }
        }
    }
    if ((@($actual | Sort-Object) -join '|') -cne (@($names | Sort-Object) -join '|')) {
        throw 'Installed fonts prefix contains missing or unrecorded files. Rebuild before the device test.'
    }
    Test-RecordedFiles $buildRoot $Records $names
}
function Invoke-Adb([string[]]$Arguments) {
    $process = New-Object System.Diagnostics.Process
    try {
        $process.StartInfo.FileName = $AdbPath
        # Explicit quoting works on Windows PowerShell 5.1 and preserves paths with spaces.
        $process.StartInfo.Arguments = ((@('-H', 'localhost', '-P', '5037') + $Arguments | ForEach-Object {
            $escaped = $_ -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1'
            '"' + $escaped + '"'
        }) -join ' ')
        $process.StartInfo.UseShellExecute = $false
        $process.StartInfo.CreateNoWindow = $true
        $process.StartInfo.RedirectStandardOutput = $true
        $process.StartInfo.RedirectStandardError = $true
        $null = $process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(60000)) {
            $process.Kill()
            throw 'The selected ADB command timed out after 60 seconds.'
        }
        if (-not $stdout.Wait(2000) -or -not $stderr.Wait(2000)) { throw 'ADB output did not close after command exit.' }
        $output = ($stdout.Result + "`n" + $stderr.Result).Trim()
        if ($output) { Write-Host $output }
        return @{ Code = $process.ExitCode; Text = $output; Stdout = $stdout.Result.Trim() }
    } finally { $process.Dispose() }
}
function Invoke-SelectedAdb([string[]]$Arguments) {
    $result = Invoke-Adb (@('-s', $Serial) + $Arguments)
    if ($result.Code -ne 0) { throw "ADB command failed for $Serial with exit $($result.Code). Send all output." }
    return $result
}
try {
    if ($env:OS -ne 'Windows_NT') { throw 'Run this helper on the Windows computer with LDPlayer open.' }
    if (-not (Test-Path -LiteralPath $AdbPath -PathType Leaf) -or (Get-Item -LiteralPath $AdbPath -Force).Length -eq 0) { throw "ADB is missing or empty: $AdbPath" }
    $receiptPath = Require-RegularFile $repoRoot 'build\android-fonts\receipt.json'
    $receipt = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
    if ($receipt.schema_version -ne 1 -or $receipt.kind -ne 'wesnoth-android-fonts' -or
        $receipt.profile.wesnoth_version -ne '1.19.26' -or $receipt.profile.abi -ne 'x86_64' -or
        $receipt.profile.min_sdk -ne 23 -or $receipt.profile.ndk_version -ne '26.3.11579264') {
        throw 'Native receipt does not match the selected Wesnoth/NDK/x86_64/API23 profile.'
    }
    $inputs = @('tools/build_android_fonts.py', 'tools/build_android_media.py', 'tools/build_android_bootstrap.py',
        'tools/android-build-profile.json', 'tools/android-native-archives.json',
        'tools/native-fonts/build-plan.json', 'tools/native-fonts/CMakeLists.txt', 'tools/native-fonts/probe.cpp',
        'tools/native-fonts/assets.json')
    Test-RecordedFiles $repoRoot $receipt.inputs.project_files $inputs
    $artifacts = @('prefix/bin/wesnoth-fonts-check', 'prefix/lib/libc++_shared.so')
    Test-RecordedFiles $buildRoot $receipt.artifacts $artifacts
    Test-FontsPrefix $receipt.prefix_files
    $assetsPath = Require-RegularFile $repoRoot 'tools/native-fonts/assets.json'
    $assets = Get-Content -LiteralPath $assetsPath -Raw | ConvertFrom-Json
    if ($assets.schema_version -ne 1 -or $assets.game_data_version -ne '1.19.26') {
        throw 'Font assets do not match the selected game-data version.'
    }
    if (-not (Test-Path -LiteralPath $DataRoot -PathType Container)) { throw "Game-data folder is missing: $DataRoot" }
    $DataRoot = (Get-Item -LiteralPath $DataRoot -Force).FullName
    $fontFiles = @('fonts/Lato-Regular.ttf', 'fonts/DejaVuSans.ttf', 'fonts/COPYING')
    Test-RecordedFiles $DataRoot $assets.files $fontFiles
    Write-Host 'PASS: font receipt, nine current source inputs, both runtime artifacts, the complete installed prefix and all three selected game-data files match.'

    if ($Serial -match '^127\.0\.0\.1:[0-9]+$') {
        Write-Host "Connecting SDK ADB to the selected local endpoint: $Serial"
        $connection = Invoke-Adb @('connect', $Serial)
        if ($connection.Code -ne 0) { throw 'ADB connect failed. Keep LDPlayer running and send the output.' }
    }
    $state = Invoke-SelectedAdb @('get-state')
    if ($state.Stdout -ne 'device') { throw "Selected device is not ready: $Serial" }
    $properties = @{}
    foreach ($property in @('sys.boot_completed', 'ro.build.version.sdk', 'ro.product.cpu.abi')) {
        Write-Host "DEVICE PROPERTY: $property"
        $result = Invoke-SelectedAdb @('shell', 'getprop', $property)
        $properties[$property] = $result.Stdout
    }
    $api = 0
    if ($properties['sys.boot_completed'] -ne '1' -or
        -not [int]::TryParse($properties['ro.build.version.sdk'], [ref]$api) -or $api -lt 23 -or
        $properties['ro.product.cpu.abi'] -ne 'x86_64') {
        throw 'The selected device must finish booting, run Android API23 or later, and have primary ABI x86_64.'
    }
    # Every remote shell argument below comes from fixed ASCII names and this GUID.
    # No user-provided strings are interpolated into a remote shell command.
    $remote = '/data/local/tmp/wesnoth-fonts-check/' + [guid]::NewGuid().ToString('N')
    Write-Host "Fonts test directory: $remote"
    $null = Invoke-SelectedAdb @('shell', 'mkdir', '-p', "$remote/bin", "$remote/lib", "$remote/fonts")
    foreach ($relative in $artifacts) {
        $destination = $remote + $relative.Substring('prefix'.Length)
        $null = Invoke-SelectedAdb @('push', (Join-Path $buildRoot $relative), $destination)
    }
    foreach ($relative in $fontFiles) {
        $null = Invoke-SelectedAdb @('push', (Join-Path $DataRoot $relative), ($remote + '/' + $relative))
    }
    $null = Invoke-SelectedAdb @('shell', 'chmod', '700', "$remote/bin/wesnoth-fonts-check")
    $run = Invoke-SelectedAdb @('shell', 'env', "LD_LIBRARY_PATH=$remote/lib", "$remote/bin/wesnoth-fonts-check", "$remote/fonts")
    $expectedLines = @('WESNOTH_ANDROID_FONTS version=1.19.26 abi=x86_64 min_api=23',
        'PASS FreeType font loading', 'PASS HarfBuzz Latin ligatures', 'PASS HarfBuzz Arabic shaping',
        'PASS FreeType glyph rasterization', 'PASS wesnoth-android-fonts')
    foreach ($line in $expectedLines) {
        if ($run.Stdout -notmatch ('(?m)^' + [regex]::Escape($line) + '\r?$')) { throw "The font raster/shaping probe did not confirm: $line" }
    }
    if ($run.Text -match '(?m)^FAIL(?:\s|:)') { throw 'The font raster/shaping probe reported a failure despite its exit code.' }
    Write-Host 'ANDROID FONTS TEST PASSED'
    Write-Host 'Verified font rasterization and shaping only. No APK change, full game-data installation, game rendering or LAN play was tested.'
    Write-Host "Test files retained for diagnosis: $remote"
    $testExit = 0
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
} finally { exit $testExit }
