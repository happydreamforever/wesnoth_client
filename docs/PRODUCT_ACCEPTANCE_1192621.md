# Windows / LDPlayer product acceptance — 1192621

This is the new A–Z acceptance sequence for release1192621. Windows/LDPlayer1192621 installation and gameplay are **pending**. Earlier1192620 build/service observations remain historical evidence, not acceptance of this release. Use the existing `runtime-v2`, accounts, collections, Teams, public server identity, pinned tools and one LDPlayer. Keep completed old builds, journals, backups, saves and the existing OBB.

The update adds a bundled public server configuration, branded startup, login-first navigation, hero discovery and flexible Team plans. It explicitly migrates schema2 to3; it does not initialize a replacement database. Team plans may contain Open, Reserved, AI and Closed seats. Before preparation both teams need equal enabled counts, at least one human seat each, every human seat occupied, current captain approvals and all human players Ready. Individual rooms still require the full configured human room and every player Ready.

Record the WAN state once per batch. After C, disconnect WAN while keeping local Ethernet connected. Continue only until the first unexpected result; preserve its state and saved logs. A later step never makes an earlier failed step acceptable. Passwords, tokens, `.env` contents, private runtime files and cold backups stay on the Windows computer.

## A. Finish active work and stop the five services

Finish existing battles normally, or explicitly cancel a draft/running Team battle through its supported captain/administrator control if that is the intended outcome. Review any pending result before cancelling it; cancellation grants no rewards. Save ordinary campaign/Standard multiplayer work, leave rooms, stop companions, log out and Quit the app. Do not leave a live native individual room running during the service upgrade.

Stop native Multiplayer, shared battles, account bridge, backend and MongoDB with Ctrl+C in their existing windows. Wait for all five prompts. Keep the one LDPlayer instance available. Do not clear Android data, delete a saved profile, remove a journal or edit database records.

## B. Cold-backup the current runtime before pulling

Run in the PowerShell window you will retain for B–H:

```powershell
$ErrorActionPreference = 'Stop'
$productRuntime = Join-Path $env:LOCALAPPDATA 'WesnothAlliance\runtime-v2'
$productBackup = Join-Path $env:LOCALAPPDATA ('WesnothAlliance\backups\before-1192621-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' + [guid]::NewGuid().ToString('N'))
powershell.exe -NoProfile -ExecutionPolicy Bypass `
    -File 'D:\Test\wesnoth-setup\wesnoth_server\tools\Invoke-WindowsAllianceMaintenance.ps1' `
    -Operation Backup -RuntimeRoot $productRuntime -DestinationPath $productBackup
if ($LASTEXITCODE -ne 0) { throw 'Backup failed. Stop and retain the diagnostic output.' }
Write-Host ('Retain this exact BackupPath for E: ' + $productBackup)
```

Expect `ALLIANCE BACKUP VERIFIED` and MA03 completion. A cold backup can report `schema_checked:false`; this does not mean schema validation passed. Keep this exact new backup privately; E binds its migration journal to it. Do not substitute an earlier1192619 backup or repeat B after a migration interruption. If you later open another PowerShell window, restore `$productBackup` from B's exact recorded destination and `$productRuntime` to the existing runtime-v2 path.

## C. Pull both repositories, then disconnect WAN

Temporarily connect to GitHub, leaving services stopped:

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $productBash = 'D:\Test\wesnoth-setup\msys64\usr\bin\bash.exe'
    foreach ($name in @('wesnoth_server', 'wesnoth_client')) {
        $productRepo = '/d/Test/wesnoth-setup/' + $name
        Write-Host "Updating $name"
        & $productBash --noprofile --norc -c ('PATH=/usr/bin:$PATH /usr/bin/git -C ' + $productRepo + ' pull --ff-only origin main')
        if ($LASTEXITCODE -ne 0) { throw "Pull failed for $name. Preserve local changes and send the output." }
        & $productBash --noprofile --norc -c ('/usr/bin/git -C ' + $productRepo + ' rev-parse HEAD')
        if ($LASTEXITCODE -ne 0) { throw "Could not verify $name commit." }
    }
}
```

Expect two successful fast-forward updates and both commit IDs from the release handoff. Do not reset unrelated README edits. Disconnect WAN and retain local Ethernet; record that actual state. Keep all services stopped through F. Do not update dependencies or regenerate the server certificate.

## D. Append the public profile reference without replacing `.env`

The existing public profile is `wesnoth_server\runtime\alliance-transport\public-profile.json`. It contains public endpoint/certificate information only. The build reads only `WESNOTH_PUBLIC_PROFILE_PATH`; unrelated well-formed assignments are ignored without interpolation or inclusion in the APK/receipt. A missing, duplicate or invalid public path/certificate is a build error.

This block preserves every existing byte, appending the public key only if absent. It prints no configuration values:

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $productEnv = 'D:\Test\wesnoth-setup\wesnoth_client\.env'
    $productPublic = 'D:\Test\wesnoth-setup\wesnoth_server\runtime\alliance-transport\public-profile.json'
    if (-not (Test-Path -LiteralPath $productPublic -PathType Leaf)) { throw 'The existing public profile is missing. Stop without regenerating identity.' }
    $productEncoding = New-Object System.Text.UTF8Encoding($false, $true)
    $productExisting = ''
    if (Test-Path -LiteralPath $productEnv) {
        $productItem = Get-Item -LiteralPath $productEnv -Force
        if ($productItem.PSIsContainer -or ($productItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'The existing .env path is not an ordinary file.' }
        try { $productExisting = $productEncoding.GetString([IO.File]::ReadAllBytes($productEnv)) }
        catch { throw 'The existing .env is not readable UTF-8. Preserve it and stop.' }
    }
    $productMatches = [regex]::Matches($productExisting.TrimStart([char]0xfeff), '(?m)^\s*WESNOTH_PUBLIC_PROFILE_PATH\s*=')
    if ($productMatches.Count -gt 1) { throw 'The public profile key is duplicated. Preserve .env and stop.' }
    if ($productMatches.Count -eq 0) {
        $productPrefix = if ($productExisting.Length -gt 0 -and -not $productExisting.EndsWith("`n")) { "`r`n" } else { '' }
        [IO.File]::AppendAllText($productEnv, $productPrefix + 'WESNOTH_PUBLIC_PROFILE_PATH=../wesnoth_server/runtime/alliance-transport/public-profile.json' + "`r`n", $productEncoding)
        Write-Host 'Public profile reference appended; existing configuration preserved.'
    } else { Write-Host 'Existing public profile reference retained; the build will validate it.' }
    Remove-Variable productExisting -ErrorAction SilentlyContinue
}
```

Do not paste `.env`, run it as a shell script, copy server-key.pem, or import anything in Android. Existing imported Android trust remains on disk unused; the signed APK's validated public profile is authoritative.

## E. Migrate the existing schema with B's exact backup

```powershell
$ErrorActionPreference = 'Stop'
if (-not $productBackup -or -not (Test-Path -LiteralPath (Join-Path $productBackup 'manifest.json') -PathType Leaf)) { throw 'Select the exact verified B backup first.' }
powershell.exe -NoProfile -ExecutionPolicy Bypass `
    -File 'D:\Test\wesnoth-setup\wesnoth_server\tools\Migrate-WindowsAllianceRuntime.ps1' `
    -RuntimeRoot $productRuntime -BackupPath $productBackup
if ($LASTEXITCODE -ne 0) { throw 'Migration stopped. Retain this same backup and both migration journals; send the saved log.' }
```

Expect `ALLIANCE SCHEMA 3 MIGRATION COMPLETE`, schema_version3 and complete/already_complete status. The helper temporarily owns the database process, verifies its identity, updates schema metadata/validators/indexes and stops it. Existing records, database generation, certificate and replay files stay intact. If interrupted, keep every journal and resume E with the **same** BackupPath; do not initialize or restore over the current runtime.

## F. Rebuild only the changed bridge, native Multiplayer and Android

```powershell
& {
    $ErrorActionPreference = 'Stop'
    powershell.exe -NoProfile -ExecutionPolicy Bypass `
        -File 'D:\Test\wesnoth-setup\wesnoth_server\tools\Rebuild-WindowsAllianceBridge.ps1' `
        -MsysRoot 'D:\Test\wesnoth-setup\msys64'
    if ($LASTEXITCODE -ne 0) { throw 'Bridge rebuild failed. Retain both its old stage and logs.' }
    powershell.exe -NoProfile -ExecutionPolicy Bypass `
        -File 'D:\Test\wesnoth-setup\wesnoth_server\tools\Build-WindowsMultiplayer.ps1' `
        -MsysRoot 'D:\Test\wesnoth-setup\msys64' -PreservePreviousBuild
    if ($LASTEXITCODE -ne 0) { throw 'Native Multiplayer rebuild failed. Retain its stages and logs.' }
    powershell.exe -NoProfile -ExecutionPolicy Bypass `
        -File 'D:\Test\wesnoth-setup\wesnoth_client\tools\Rebuild-WindowsAndroidAccountGame.ps1'
    if ($LASTEXITCODE -ne 0) { throw 'Android rebuild failed. Retain its stages, journals and logs.' }
}
```

Expect verified old bridge/native/account stages retained under unique names, `ALLIANCE ACCOUNT BRIDGE REBUILD PASSED`, `UNIFIED MULTIPLAYER ARTIFACT VERIFIED` / `UNIFIED MULTIPLAYER BUILD PASSED`, and `ANDROID ACCOUNT ENGINE BUILD PASS` / `ANDROID ACCOUNT GAME BUILD PASS` / `ANDROID ACCOUNT GAME REBUILD COMPLETE`. The APK must be1192621 with the original signing identity. All builds use existing offline dependencies; no fresh OBB is made or transferred. Stop at the first failure rather than repeating the whole sequence.

## G. Start and verify the five current services

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
    -File 'D:\Test\wesnoth-setup\wesnoth_server\tools\Start-WindowsAllianceStack.ps1' `
    -RuntimeRoot (Join-Path $env:LOCALAPPDATA 'WesnothAlliance\runtime-v2') `
    -MsysRoot 'D:\Test\wesnoth-setup\msys64' -IncludeBattles
if ($LASTEXITCODE -ne 0) { throw 'Startup failed. Return its saved workflow text/JSON and failing service log.' }
```

Expect W02–W06 PASS and W07 UNIFIED GAME SERVICES READY: database27018, backend18765, account bridge15001, shared Teams15002 and native Multiplayer15000. Keep all five windows open. A healthy server probe is not proof of Android login or gameplay.

## H. Install in place and observe branded startup

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
    -File 'D:\Test\wesnoth-setup\wesnoth_client\tools\Install-AndroidAccountGame.ps1' `
    -KeepSavedProfile
if ($LASTEXITCODE -ne 0) { throw 'Installation failed. Preserve app data and return the saved install log.' }
```

Expect `ANDROID ACCOUNT GAME INSTALL COMPLETE`, verified1192621 and a fresh startup-screen launch. `KeepSavedProfile` remains a compatible option; it does not select old imported trust. Visually verify the Wesnoth background/logo, readable checking phase and percentage/progress bar, then login. Progress can restart for a separately labelled phase;100% is shown only after game-data preparation completes. There is no import/setup/Start game page. The installer marker establishes startup-screen launch, not completion of data verification or login; return the actual visible outcome separately.

## I–L. Authentication, input, navigation and discovery

| Step | Action | Expected result |
| --- | --- | --- |
| I — signed out | Open Log in; close it. Open Sign up; close it. Quit while signed out and reopen. | Credential UI covers the whole previous view; closing returns to the signed-out entry. No usable authenticated home behind it. Quit returns to the LDPlayer launcher. No profile-import screen on reopen. |
| J — keyboard and eyes | In a harmless credential attempt, use the physical keyboard to type `aAzZ1!`, exercise Shift/Caps Lock, turn Caps Lock off and toggle the password eye. Clear/cancel, then log in with an existing account privately. Test one deliberate wrong password. | Exact case/symbols, password eye reflects visibility, adequately sized fields/buttons and no overlapping labels. Wrong password remains an authentication error and cannot open home. Never show a real password in evidence. VPS event injection does not establish this physical LDPlayer result. |
| K — home/session/Quit | Inspect home choices. Enter each major section and Back. Open ordinary Multiplayer then Back. Log out, log in again, Quit from authenticated home, reopen and log in. | Campaigns, Heroes, Teams and Multiplayer navigation is readable. Back retains the current account. Logout returns to signed-out UI. Quit clears the local account session and closes the whole app; reopening requires login. Server revocation is only claimed when its request succeeds. |
| L — hero discovery | Open all heroes and owned heroes, search/select cards, try an empty search and clear it. Inspect campaign source, earned forms, portrait, permanent level/XP, HP and attacks. Open a campaign's reward preview and return without starting. | Full catalog includes locked heroes with discovery information; owned markers/forms match the actual account. Missing portraits are not blank cards. Merely browsing or opening a reward preview grants nothing. Empty collection gives normal campaign guidance; no invented starter hero. Record actual ID/count/XP, never assumed values. |

Use existing runtime-v2 players. Create an additional player only if needed through normal Sign up, then earn that player's heroes through their own normal story play. Never seed heroes, reset accounts or borrow another account's campaign save.

## M–P. Teams, saved plans and consent

| Step | Action | Expected result |
| --- | --- | --- |
| M — Team workspace | Open your Team and area directory, members, applications/decisions and management. Inspect long names, last row, no-match search and paging/Refresh. | Current area/team/level/rating/roles remain clear. Only authorized explicit actions apply or change membership. Back is read-only. Member accounts do not get captain edits. Existing roster/history survives the upgrade. |
| N — plan creation | As captain choose opponent and an approved map. Preview gold/income/turns/fog/shroud/observers. Back before Save once; repeat and save. Inspect Open, Reserved, AI and Closed options for your team's seats. | Cancel creates no plan. Saved settings and map match review. Opponent slots begin Open and are configured by that team's captain. Reserved names are current own-team members. AI has faction/leader but no account/hero XP owner. Closed retains the map side without a participant. Drafts may be incomplete/unequal; preparation requires equal enabled counts. |
| O — membership and revisions | With owned accounts sequentially, join only eligible own-team human slots, choose owned hero/forms, approve as each captain and set Ready as each human. Change one setting/slot/hero, then refresh the other account before consenting again. | Only the actor's own human seat/hero/Ready changes. Reserved slots reject other players. Real plan changes invalidate earlier approvals/readiness; stale revisions require refresh and are never silently retried. An unseated teammate cannot use Leave seat to reset other players' preparation. No start while a human slot is empty/unready or enabled counts differ. |
| P — custom/ranked policy | Prepare a balanced custom fixture on Hexcake with one human+oneAI+oneClosed per Team. Separately inspect the standard ranked preset: all human slots,100gold,2baseincome,unlimitedturns,fog on,shroud/observers off. | Custom battle remains playable and awards legitimate hero/Team XP after agreed settlement, with no rating change. Only the exact all-human standard preset is rated. No XP/rating for AI identities, browsing, readiness or local practice. Do not claim ranked gameplay if there are not enough independently authenticated human-account seats. |

A Team run UUID is an operator identifier: obtain it from the local administrator's Runs page (`http://127.0.0.1:18765/runs`) by matching the reviewed teams/map/time. It is not a numeric Individual Room ID. Do not expose internal IDs throughout ordinary player screens merely to run a test.

## Q. Shared Team game using the one-LDPlayer companion

Prepare the balanced custom fixture in P using two existing owned players from opposing Teams. Each selects their own earned hero and consents; both captains approve. Return the visible client to its intended account. In a separate PowerShell window:

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $productCompanionUser = Read-Host 'Other owned player username (not the visible player)'
    $productBattle = Read-Host 'Team run UUID from the local administrator Runs page'
    powershell.exe -NoProfile -ExecutionPolicy Bypass `
        -File 'D:\Test\wesnoth-setup\wesnoth_client\tools\Run-AndroidAllianceCompanion.ps1' `
        -Username $productCompanionUser -BattleId $productBattle
    if ($LASTEXITCODE -ne 0) { Write-Host 'Return the sanitized companion report; do not repeat a mutation automatically.' }
}
```

The helper asks explicit CONTROL consent and privately requests the other player's password. Use the visible player's current Team battle entry while the helper joins. Preparation is time bounded: if it expires, refresh/reconfirm Ready normally rather than editing a journal. Play at least two full rounds; verify humans and host-owned AI act on the correct sides, Closed sides are skipped, gold/income/fog/shroud/turn settings match and no out-of-sync error occurs. The helper controls a separately authenticated computer seat in the same emulator; this is not a second human's Android acceptance. Never simultaneously log the helper's account into the visible client.

## R–T. Shared recovery, observers and settlement

| Step | Action | Expected result |
| --- | --- | --- |
| R — host reconnect | In a controlled Team fixture, disconnect the engine host, keep its opponent waiting, then reconnect as the same account to the same retained run/generation. Preserve any failed attempt. | The retained host replays the accepted history and resumes its own human side plus its assigned AI. Another account cannot take those controllers. The running Team room is retained; no duplicate launch, reward or guest replacement. If disconnection cannot be exercised without destroying the test, report NOT RUN. |
| S — observers | In an observer-enabled Team fixture, use another eligible Team member sequentially after leaving/stopping its earlier test session. Observe the running battle; try selecting units and unavailable action controls. Repeat with observers disabled. | Authorized observer receives the retained game state but owns no side, cannot send captain/game commands, cannot submit results and receives no battle XP. Disabled observer access is rejected. One visible client cannot prove simultaneous multi-human viewing; report the scope actually observed. |
| T — result/history | Finish normally or explicitly confirm a legitimate captain surrender/draw. Review both players' reports and both captain confirmations. Then inspect hero/Team changes and verified completed replay/history; restart/reopen the app. | Result upload is distinct from settlement. All player evidence and both captain confirmations precede one settlement. Custom fixture grants agreed XP but zero rating delta; exact ranked preset applies its rating rule. A disputed result does not settle. Replay belongs to the actual run; unavailable recording is NOT RUN, never fabricated. Persistence retains the same actual account, heroes, Team and history. |

Do not cancel a running battle merely to create history screenshots. Local practice, if offered for an eligible legacy plan, remains separate and grants no permanent XP/rating. Existing format1 plans/results must still be viewable under their original rules; report unavailable historical fixtures as NOT RUN.

## U–W. Individual rooms and standard multiplayer

| Step | Action | Expected result |
| --- | --- | --- |
| U — room dashboard/create | Open Individual rooms; use map/name, open/running and gold/turn filters. Create a standalone two-player room with finite turns, review then Back once, repeat and Publish. Inspect all published rules and own hero selection. | One reviewed settings flow; readable occupancy/map and current room state. Cancel creates no room.1/2 remains waiting even when its only player is Ready. No manual Start bypass. Individual mode needs no permanent Team or area membership. Record the numeric Room ID shown in Room settings. |
| V — full human room | Run the Individual companion below. Cancel its first READY prompt once, then rerun, review the latest settings and explicitly consent. Ready the visible player against that same revision. | Departure reopens the seat and invalidates earlier consent. Only2/2human accounts with every player Ready start automatically. A changed hero/setting/member list requires renewed Ready; computer slots cannot satisfy the human capacity rule. Play two rounds and finish normally. Individual battles grant no permanent hero/Team XP or rating. |
| W — exit/rejoin/Standard | Close a waiting room as host; inspect a larger room without filling it. In a separate controlled fixture disconnect after an Individual room starts, then inspect the lobby. Exercise Standard manual/AI multiplayer, save, leave and host its saved game. | Waiting host departure closes the room; an unfilled room never starts. Started Individual rooms do **not** support player rejoin/takeover; failure is reported as incomplete, not a successful reconnect. Standard manual start/AI/saved-game resume remains available and is separate from the full-human Individual room rule. |

Run V in another PowerShell window with the visible host waiting:

```powershell
& {
    $productIndividualUser = Read-Host 'Other owned player username (not the visible player)'
    $productRoom = Read-Host 'Numeric Individual Room ID from Room settings'
    powershell.exe -NoProfile -ExecutionPolicy Bypass `
        -File 'D:\Test\wesnoth-setup\wesnoth_client\tools\Run-AndroidIndividualCompanion.ps1' `
        -Username $productIndividualUser -RoomId $productRoom
    if ($LASTEXITCODE -ne 0) { Write-Host 'For deliberate pre-Ready cancellation report CANCELLED; otherwise return the sanitized failure report.' }
}
```

Review CONTROL/JOIN/READY prompts; the password is hidden and never passed as an argument. Expect INDIVIDUAL COMPANION STARTED/FINISHED and I05/I06 completion only for a completed game and successful logout. Do not substitute a Team run UUID. A real human on another device remains a separate unperformed acceptance item.

## X. Campaign victory, reward preview and ownership

Resume a normal campaign save owned by the signed-in account. Compare the campaign reward preview with the eligible named heroes/forms in that story. Finish a legitimate scenario while controlling an eligible hero; inspect the permanent collection and XP afterwards. Verify unlocks at the scenario victory boundary, not at preview/selection time, and repeat opening the recorded result without duplicate reward. Story branches can change availability. Record the actual scenario, hero/form and before/after values; do not assert rewards for an unfinished scenario or an AI-controlled enemy.

## Y. LAN loss, session identity and clean restart

Save/leave games and stop companions. Idle signed in, then stop only account bridge15001. Expect local sign-out after the existing monitor/request timeout, an opaque signed-out view and a connection error on login while unavailable. WAN disconnection alone must not break a healthy LAN connection. Restart only the bridge:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
    -File 'D:\Test\wesnoth-setup\wesnoth_server\tools\Run-WindowsAllianceBridge.ps1' `
    -RuntimeRoot (Join-Path $env:LOCALAPPDATA 'WesnothAlliance\runtime-v2') `
    -MsysRoot 'D:\Test\wesnoth-setup\msys64'
```

Require READY, log in to the same account/database identity and confirm the same collection/Team. No mutation is automatically retried. If resuming a paused authenticated game, a different account must not inherit it. Finish/leave test games, Quit, stop all five services cleanly, restart with G and repeat H's visible launch observation without reinstalling. Accounts, earned results, Team plans and public identity remain. Do not perform a new backup/pull/build/migration merely to repeat this restart check.

## Z. Return evidence and preserve recovery options

Return A–Z as PASS / FAIL / NOT RUN with actual WAN status, account/public room or run identifier where relevant, expected/observed behavior and exact diagnostic filenames. For visuals include1280×720/actual emulator resolution and a screenshot with no credentials. Retain only the observed scope: build/service probes do not prove login, and one-LDPlayer automation does not prove multiple-human behavior.

Sanitized helper outputs are under each repository's `diagnostics`: server `alliance-maintenance-*`, `alliance-migration-*`, `alliance-bridge-rebuild-*` / `alliance-bridge-build-*`, `unified-multiplayer-build-*`, `alliance-workflow-*` text/JSON and W02–W06 logs; client `android-account-rebuild-*` / `android-account-build-*` / `android-account-install-*`, `android-companion-*.json` and `android-individual-companion-*.json`. Send the relevant saved helper report/console, not raw app storage, runtime, `.env`, Mongo logs with private data, passwords/tokens or backup contents. Stop at the first unexpected result; fixes are reproduced and verified on VPS, then only the failed/affected steps are repeated.

### Recovery verification — only if migration fails or explicitly requested

Keep the current runtime and its journals intact. With all five services stopped, restore B's verified schema2 backup into a **new isolated destination**, never over runtime-v2:

```powershell
$ErrorActionPreference = 'Stop'
if (-not $productBackup) { throw 'Use the exact verified B backup path.' }
$productRecovery = Join-Path $env:LOCALAPPDATA ('WesnothAlliance\recovery-1192621-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' + [guid]::NewGuid().ToString('N'))
powershell.exe -NoProfile -ExecutionPolicy Bypass `
    -File 'D:\Test\wesnoth-setup\wesnoth_server\tools\Invoke-WindowsAllianceMaintenance.ps1' `
    -Operation Restore -RuntimeRoot (Join-Path $env:LOCALAPPDATA 'WesnothAlliance\runtime-v2') `
    -BackupPath $productBackup -DestinationPath $productRecovery
if ($LASTEXITCODE -ne 0) { throw 'Recovery verification failed. Preserve both destinations and return its sanitized log.' }
```

Expect `ALLIANCE RESTORE VERIFIED`, schema checked and database stopped. The restore verifies the actual recovered schema2 database; it does not activate it or change runtime selection. Do not launch new schema3 services against this schema2 copy, delete the current runtime, copy individual database files across, or switch runtime selection without a reviewed recovery decision. This optional recovery drill is not a prerequisite for every successful upgrade.
