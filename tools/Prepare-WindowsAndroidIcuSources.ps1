# Windows PowerShell 5.1. Download only when requested; the default is read-only/offline.
[CmdletBinding()]
param([switch]$Download)
$ErrorActionPreference = 'Stop'
$setupExit = 2
$temporary = $null
$temporaryOwned = $false
$originalProtocol = $null

function Test-IcuPath([string]$Path, [string]$Kind) {
    $Path = [IO.Path]::GetFullPath($Path)
    $cursor = $Path
    $leaf = $null
    while ($cursor) {
        $item = Get-Item -LiteralPath $cursor -Force -ErrorAction SilentlyContinue
        if ($item) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Links/reparse points are not accepted for ICU sources: $cursor"
            }
            if ($cursor -eq $Path) { $leaf = $item }
            elseif (-not $item.PSIsContainer) { throw "A source-directory ancestor is not a directory: $cursor" }
        }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
    if ($null -eq $leaf) { return $false }
    if (($Kind -eq 'File' -and $leaf.PSIsContainer) -or ($Kind -eq 'Directory' -and -not $leaf.PSIsContainer)) {
        throw "Expected a regular $Kind at: $Path"
    }
    return $true
}
function Assert-IcuArchive([string]$Path, $Pin) {
    if (-not (Test-IcuPath $Path 'File')) { throw "Pinned ICU source is missing: $Path" }
    if ((Get-Item -LiteralPath $Path -Force).Length -ne $Pin.bytes -or
        (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -cne $Pin.sha256.ToUpperInvariant()) {
        throw "ICU source size/SHA256 differs from its pin: $Path. Keep the file and send the output; nothing was overwritten."
    }
}
function New-IcuSourceResponse([string]$Uri) {
    $request = [Net.HttpWebRequest]::CreateHttp($Uri)
    $request.UserAgent = 'Wesnoth-offline-icu-preparation/1'
    $request.Timeout = 60000
    $request.ReadWriteTimeout = 60000
    $request.MaximumAutomaticRedirections = 5
    return $request.GetResponse()
}
try {
    # Resolve these only after script initialization, never in parameter defaults.
    $repoRoot = Split-Path $PSScriptRoot -Parent
    $manifestPath = Join-Path $PSScriptRoot 'native-icu/sources.json'
    if (-not (Test-IcuPath $manifestPath 'File')) { throw 'The ICU source manifest is missing.' }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($manifest.schema_version -ne 1 -or $manifest.kind -cne 'wesnoth-android-icu-sources' -or
        @($manifest.archives).Count -ne 1) { throw 'Unexpected ICU source manifest.' }
    $pin = $manifest.archives[0]
    if ($pin.package -cne 'icu4c' -or $pin.version -cne '78.3' -or
        $pin.name -cne 'icu4c-78.3-sources.tgz' -or $pin.source_dir -cne 'icu' -or
        $pin.url -cne 'https://github.com/unicode-org/icu/releases/download/release-78.3/icu4c-78.3-sources.tgz' -or
        (-not ($pin.bytes -is [int] -or $pin.bytes -is [long])) -or $pin.bytes -le 0 -or $pin.bytes -gt 33554432 -or
        $pin.sha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'Invalid pinned ICU source declaration.' }
    $cache = Join-Path $repoRoot 'build/android-icu-sources'
    $archive = Join-Path $cache $pin.name
    $null = Test-IcuPath $cache 'Directory'
    if (Test-IcuPath $archive 'File') {
        Assert-IcuArchive $archive $pin
        Write-Host 'Existing ICU source matches its pin; no download needed.'
    } else {
        if (-not $Download) {
            throw "Pinned ICU source is missing: $archive. Temporarily connect and rerun this helper with -Download."
        }
        $null = [IO.Directory]::CreateDirectory($cache)
        $null = Test-IcuPath $cache 'Directory'
        $temporary = Join-Path $cache ('.icu-download-' + [guid]::NewGuid().ToString('N') + '.tmp')
        $null = Test-IcuPath $temporary 'File'
        $destination = $null
        $response = $null
        $inputStream = $null
        $originalProtocol = [Net.ServicePointManager]::SecurityProtocol
        [Net.ServicePointManager]::SecurityProtocol = $originalProtocol -bor [Net.SecurityProtocolType]::Tls12
        Write-Host "Downloading pinned ICU $($pin.version) ($($pin.bytes) bytes)."
        try {
            # CreateNew reserves only this invocation's temporary file; never replace a user's file.
            $destination = [IO.File]::Open($temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            $temporaryOwned = $true
            $response = New-IcuSourceResponse $pin.url
            $finalUri = $response.ResponseUri
            if ($response.StatusCode -ne [Net.HttpStatusCode]::OK -or $finalUri.Scheme -cne 'https' -or
                $finalUri.Host -notin @('github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com') -or
                $finalUri.UserInfo -or $finalUri.Fragment) { throw 'Source download returned an unexpected HTTP response or redirect.' }
            if ($response.ContentLength -ge 0 -and $response.ContentLength -ne $pin.bytes) {
                throw 'Source download Content-Length differs from its pinned size.'
            }
            $inputStream = $response.GetResponseStream()
            $buffer = New-Object byte[] 65536
            [long]$received = 0
            while (($count = $inputStream.Read($buffer, 0, [int][Math]::Min($buffer.Length, $pin.bytes + 1 - $received))) -gt 0) {
                $received += $count
                if ($received -gt $pin.bytes) { throw 'Source download exceeded its pinned size.' }
                $destination.Write($buffer, 0, $count)
            }
        } finally {
            if ($null -ne $inputStream) { $inputStream.Dispose() }
            if ($null -ne $response) { $response.Close() }
            if ($null -ne $destination) { $destination.Dispose() }
        }
        Assert-IcuArchive $temporary $pin
        $null = Test-IcuPath $cache 'Directory'
        if (Test-IcuPath $archive 'File') { throw "The destination appeared during download; preserve it and retry verification: $archive" }
        # Same-directory rename is atomic and the two-argument Move never overwrites.
        [IO.File]::Move($temporary, $archive)
        $temporaryOwned = $false
        $temporary = $null
        Assert-IcuArchive $archive $pin
    }
    Write-Host "PASS: ICU $($pin.version); bytes=$($pin.bytes); SHA256=$($pin.sha256)"
    Write-Host "Source cache: $archive"
    Write-Host 'ANDROID ICU SOURCES VERIFIED'
    Write-Host 'Source preparation only. ICU compilation and Android runtime testing are not performed.'
    $setupExit = 0
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
} finally {
    if ($null -ne $originalProtocol) { [Net.ServicePointManager]::SecurityProtocol = $originalProtocol }
    if ($temporaryOwned -and $temporary) {
        try {
            if (Test-IcuPath $temporary 'File') { [IO.File]::Delete($temporary) }
        } catch { Write-Host "Temporary download cleanup failed; preserve this path for diagnosis: $temporary" }
    }
    exit $setupExit
}
