# Windows PowerShell 5.1. This project's .env controls preparation and APK building.
[CmdletBinding()]
param([ValidateSet('configure','check','prepare','build','install')][string]$Action='build',
      [string]$EnvironmentFile='', [switch]$Download, [switch]$Update)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'tools\Windows-Environment.ps1')
$oldEnvironment=@{}
$started=$false
$exitCode=1
try {
    if ($Action -eq 'configure') {
        Initialize-WesnothEnvironment -ProjectRoot $PSScriptRoot -EnvironmentFile $EnvironmentFile
        exit 0
    }
    if ($env:OS -ne 'Windows_NT') { throw 'Run development commands on Windows.' }
    if ($Download -and $Action -ne 'prepare') { throw 'Only prepare accepts -Download. Builds and installation remain offline.' }
    if ($Action -eq 'prepare' -and -not $Download) { throw 'Client prepare requires -Download. Use check for offline verification.' }
    if ($Update -and $Action -ne 'build') { throw '-Update is only valid with build.' }
    $c=Get-WesnothEnvironment -ProjectRoot $PSScriptRoot -Project client -EnvironmentFile $EnvironmentFile
    foreach ($name in @('ANDROID_USER_HOME','JAVA_HOME','GRADLE_USER_HOME')) {
        $oldEnvironment[$name]=[Environment]::GetEnvironmentVariable($name,'Process')
    }
    $env:ANDROID_USER_HOME=$c.WESNOTH_ANDROID_USER_HOME
    $env:JAVA_HOME=$c.WESNOTH_JAVA_HOME
    $env:GRADLE_USER_HOME=$c.WESNOTH_GRADLE_USER_HOME
    $diagnostics=Join-Path $PSScriptRoot 'diagnostics'
    $null=New-Item -ItemType Directory -Path $diagnostics -Force
    $log=Join-Path $diagnostics ('development-'+$Action+'-'+[guid]::NewGuid().ToString('N')+'.txt')
    Start-Transcript -LiteralPath $log -NoClobber | Out-Null
    $started=$true
    $java=Join-Path $c.WESNOTH_JAVA_HOME 'bin\java.exe'
    $checker=Join-Path $PSScriptRoot 'tools\windows_development.py'
    # Resolve the sole public path into an automatically generated public-only input.
    # No other .env settings or private runtime credentials enter Android assets.
    $publicConfig=Join-Path $diagnostics ('public-build-'+[guid]::NewGuid().ToString('N')+'.env')
    $utf8=New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($publicConfig,('WESNOTH_PUBLIC_PROFILE_PATH='+$c.WESNOTH_PUBLIC_PROFILE_PATH+"`n"),$utf8)
    $package=@{SdkRoot=$c.WESNOTH_SDK_ROOT;PythonPath=$c.WESNOTH_PYTHON_PATH;JavaHome=$c.WESNOTH_JAVA_HOME;
               GradleUserHome=$c.WESNOTH_GRADLE_USER_HOME;ArchiveDirectory=$c.WESNOTH_ARCHIVE_DIRECTORY}
    if ($Action -eq 'prepare') {
        Invoke-WesnothPython $c.WESNOTH_PYTHON_PATH @('-I','-B',(Join-Path $PSScriptRoot 'tools\prepare_android_source.py'))
        Invoke-WesnothPowerShell $c (Join-Path $PSScriptRoot 'tools\Prepare-WindowsAndroidTools.ps1') @{
            SdkRoot=$c.WESNOTH_SDK_ROOT;PythonPath=$c.WESNOTH_PYTHON_PATH;JavaHome=$c.WESNOTH_JAVA_HOME;
            GradleUserHome=$c.WESNOTH_GRADLE_USER_HOME;Download=[bool]$Download}
        Invoke-WesnothPowerShell $c (Join-Path $PSScriptRoot 'tools\Prepare-WindowsAndroidIcuSources.ps1') @{Download=[bool]$Download}
        Invoke-WesnothPowerShell $c (Join-Path $PSScriptRoot 'tools\Prepare-WindowsAndroidLayoutSources.ps1') @{Download=[bool]$Download}
    } else {
        Invoke-WesnothPython $c.WESNOTH_PYTHON_PATH @('-I','-B',$checker,'check',
            '--sdk',$c.WESNOTH_SDK_ROOT,'--java',$java,'--cmake',$c.WESNOTH_CMAKE_PATH,
            '--ninja',$c.WESNOTH_NINJA_PATH,'--public-config',$publicConfig,'--openssl',$c.WESNOTH_OPENSSL_PATH,
            '--gradle-home',$c.WESNOTH_GRADLE_USER_HOME,'--android-user-home',$c.WESNOTH_ANDROID_USER_HOME,
            '--reference-apk',$c.WESNOTH_SIGNER_REFERENCE_APK,'--server-ip',$c.WESNOTH_SERVER_IP)
        if ($Action -eq 'build') {
            Invoke-WesnothPython $c.WESNOTH_PYTHON_PATH @('-I','-B',$checker,'import-archives','--archive-directory',$c.WESNOTH_ARCHIVE_DIRECTORY)
            foreach ($stage in @('media','fonts','text-base','glib-deps','glib','text-render','text-layout','icu','boost','network','engine')) {
                $receipt=Join-Path $PSScriptRoot ('build\android-'+$stage+'\receipt.json')
                if (Test-Path -LiteralPath $receipt -PathType Leaf) {
                    Invoke-WesnothPython $c.WESNOTH_PYTHON_PATH @('-I','-B',$checker,'verify-stage','--stage',$stage)
                    continue
                }
                $names=@{'media'='Media';'fonts'='Fonts';'text-base'='TextBase';'glib-deps'='GlibDeps';'glib'='Glib';
                    'text-render'='TextRender';'text-layout'='TextLayout';'icu'='Icu';'boost'='Boost';'network'='Network';'engine'='Engine'}
                $parameters=@{SdkRoot=$c.WESNOTH_SDK_ROOT;Jobs=$c.WESNOTH_JOBS}
                if ($stage -in @('text-base','glib-deps','glib','text-render','text-layout')) { $parameters.MsysRoot=$c.WESNOTH_MSYS_ROOT }
                else {
                    $parameters.PythonPath=$c.WESNOTH_PYTHON_PATH
                    $parameters.CMakePath=$c.WESNOTH_CMAKE_PATH
                    $parameters.NinjaPath=$c.WESNOTH_NINJA_PATH
                }
                if ($stage -in @('media','fonts','text-base','glib-deps','glib','text-render','text-layout')) {
                    $parameters.ArchiveDirectory=$c.WESNOTH_ARCHIVE_DIRECTORY
                }
                if ($stage -eq 'network') { $parameters.MsysRoot=$c.WESNOTH_MSYS_ROOT }
                Invoke-WesnothPowerShell $c (Join-Path $PSScriptRoot ('tools\Build-WindowsAndroid'+$names[$stage]+'.ps1')) $parameters
            }
            $dataReceipt=Join-Path $PSScriptRoot 'build\android-game-data\receipt.json'
            $dataArguments=@('-I','-B',(Join-Path $PSScriptRoot 'tools\prepare_android_game_data.py'),'--data-root',$c.WESNOTH_GAME_DATA_ROOT)
            if (Test-Path -LiteralPath $dataReceipt -PathType Leaf) { $dataArguments+='--verify-only' }
            Invoke-WesnothPython $c.WESNOTH_PYTHON_PATH $dataArguments
            Invoke-WesnothPowerShell $c (Join-Path $PSScriptRoot 'tools\Build-WindowsAndroidGame.ps1') $package
            $baseApk=Join-Path $PSScriptRoot 'build\android-game\app\build\outputs\apk\debug\app-debug.apk'
            Invoke-WesnothPython $c.WESNOTH_PYTHON_PATH @('-I','-B',$checker,'verify-signer','--apk',$baseApk,
                '--reference-apk',$c.WESNOTH_SIGNER_REFERENCE_APK,'--sdk',$c.WESNOTH_SDK_ROOT,'--java',$java)
            $account=@{}; foreach($key in $package.Keys){$account[$key]=$package[$key]}
            $account.CMakePath=$c.WESNOTH_CMAKE_PATH; $account.NinjaPath=$c.WESNOTH_NINJA_PATH
            $account.PublicConfigPath=$publicConfig; $account.OpenSslPath=$c.WESNOTH_OPENSSL_PATH; $account.Jobs=$c.WESNOTH_JOBS
            $accountHelper='Build-WindowsAndroidAccountGame.ps1'
            if ($Update) { $accountHelper='Rebuild-WindowsAndroidAccountGame.ps1' }
            Invoke-WesnothPowerShell $c (Join-Path $PSScriptRoot ('tools\'+$accountHelper)) $account
            $apk=Join-Path $PSScriptRoot 'build\android-account-game\app\build\outputs\apk\debug\app-debug.apk'
            Invoke-WesnothPython $c.WESNOTH_PYTHON_PATH @('-I','-B',$checker,'verify-signer','--apk',$apk,
                '--reference-apk',$c.WESNOTH_SIGNER_REFERENCE_APK,'--sdk',$c.WESNOTH_SDK_ROOT,'--java',$java)
        } elseif ($Action -eq 'install') {
            Invoke-WesnothPowerShell $c (Join-Path $PSScriptRoot 'tools\Install-AndroidAccountGame.ps1') @{
                SdkRoot=$c.WESNOTH_SDK_ROOT;PythonPath=$c.WESNOTH_PYTHON_PATH;JavaHome=$c.WESNOTH_JAVA_HOME;
                AdbPath=$c.WESNOTH_ADB_PATH;Serial=$c.WESNOTH_ADB_SERIAL;PublicConfigPath=$publicConfig;
                OpenSslPath=$c.WESNOTH_OPENSSL_PATH;KeepSavedProfile=$true}
        }
    }
    Write-Host ('WINDOWS CLIENT DEVELOPMENT '+$Action.ToUpperInvariant()+' COMPLETE')
    $exitCode=0
} catch {Write-Host ('ERROR: '+$_.Exception.Message) -ForegroundColor Red}
finally {
    foreach($name in $oldEnvironment.Keys){[Environment]::SetEnvironmentVariable($name,$oldEnvironment[$name],'Process')}
    if($started){Stop-Transcript | Out-Null;Write-Host ('Saved development log: '+$log)}
}
exit $exitCode
