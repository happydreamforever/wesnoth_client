# Smooth Emerald Court maps — Android 1192627

This is one **client-only** update. Keep the five existing Windows services on
`runtime-v3` running. Protocol 5, schema 5 and server/worker release 1192626 stay
in use. Retain all previous accepted Court results and campaign saves.

The World and Castle maps now use a retained viewport with immediate selection,
a compact bottom panel, pinch/pan continuity, bounded swipe momentum, fixed-size
symbols and soft inactive mist. Enter/Open is separate from selection. World and
individual Area cameras are retained during navigation and refresh. Search opens
the readable location list. The profile fits its contents with equal padding and
has no tap, hold or tooltip action.

## Windows update — U1

1. Finish the current action, save any campaign, and Quit the Android game. Leave
   LDPlayer and all five service windows open. Record the current username,
   owned-character count, Available Crowns and Held Crowns. Use current values;
   do not substitute an older acceptance baseline.
2. Temporarily restore WAN for the client pull only. Run this in PowerShell:

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $mapBash = 'D:\Test\wesnoth-setup\msys64\usr\bin\bash.exe'
    & $mapBash --noprofile --norc -c 'PATH=/usr/bin:$PATH /usr/bin/git -C /d/Test/wesnoth-setup/wesnoth_client pull --ff-only origin main'
    if ($LASTEXITCODE -ne 0) { throw 'Client pull stopped. Preserve README edits and send the complete output.' }
    & $mapBash --noprofile --norc -c 'PATH=/usr/bin:$PATH /usr/bin/git -C /d/Test/wesnoth-setup/wesnoth_client rev-parse HEAD'
    if ($LASTEXITCODE -ne 0) { throw 'Client commit check failed. Send the output.' }
    & $mapBash --noprofile --norc -c 'PATH=/usr/bin:$PATH /usr/bin/git -C /d/Test/wesnoth-setup/wesnoth_client merge-base --is-ancestor da5876cda97c170a0e6ba0d7143a6df649d176fa HEAD'
    if ($LASTEXITCODE -ne 0) { throw 'The smooth-map implementation is missing. Stop and send the output.' }
}
```

3. Disconnect WAN again while retaining local Ethernet. Record this **once for
   the U1–U7 batch**. Run the existing preserving rebuild and in-place installer:

```powershell
& {
    $ErrorActionPreference = 'Stop'
    powershell.exe -NoProfile -ExecutionPolicy Bypass `
        -File 'D:\Test\wesnoth-setup\wesnoth_client\tools\Rebuild-WindowsAndroidAccountGame.ps1'
    if ($LASTEXITCODE -ne 0) { throw 'Map update rebuild stopped. Retain all stages and send the rebuild/build logs.' }

    powershell.exe -NoProfile -ExecutionPolicy Bypass `
        -File 'D:\Test\wesnoth-setup\wesnoth_client\tools\Install-AndroidAccountGame.ps1' `
        -KeepSavedProfile
    if ($LASTEXITCODE -ne 0) { throw 'Map update installation stopped. Preserve app data and send the install log.' }
}
```

Expected: `ANDROID ACCOUNT GAME BUILD COMPLETE`,
`ANDROID ACCOUNT GAME REBUILD COMPLETE`, `ANDROID ACCOUNT GAME ARTIFACTS VERIFIED`,
streamed install `Success`, installed **1192627**, then
`ANDROID ACCOUNT GAME INSTALL COMPLETE`. Logs remain under client `diagnostics/`
as `android-account-rebuild-*`, `android-account-build-*` and
`android-account-install-*`; use the exact paths printed by each helper.

Log into the same player and compare all four recorded values. Check that Load
still lists the existing saves. This update uses the bundled public profile and
retains the OBB and original Android signer. No bridge, Room-server or worker
build, dependency setup, database migration or new runtime is part of this batch.

## Touch acceptance — stop at the first unexpected result

| Check | Steps and expected result |
|---|---|
| **U2 Profile** | Check username, owned characters and Crowns. Left/right padding is equal and the panel remains at the upper right. Tap and hold the profile: no popup, tooltip or navigation. Check Help, Shop, Preferences, My Heroes, Achievements and the five bottom destinations still open their existing screens. |
| **U3 World** | Team opens World. Tap an active marker once: its name/status appear in the bottom panel. Use Enter separately; a teamless account still cannot enter. Tap a crowded cluster and use the location chooser if offered. Pan with one finger, pinch with two, then lift either finger and keep panning. New touches stop momentum. Check +/−, Centre, List, search and return to Map. Centre shows the overview. |
| **U4 Castle map** | With an eligible Team account, Enter an active Area. Repeat selection/pinch/pan, especially immediately after zooming. Open a Castle and return; its Area camera and selected location remain. Return to World and re-enter; each map keeps its own camera. Do not spend Crowns or initiate a battle for this UI test. |
| **U5 Mist/refresh** | Inactive locations remain faintly visible under mist and have small locked markers. Tap one: inactive status appears and entry is unavailable. Select an active location, pan/zoom, tap Refresh and wait through two normal polls. The camera and selected location remain; there is no repeated close/reopen or jump. Details/Hide details is an explicit button. Confirm previously entered form edits survive a refresh; Cancel before any paid confirmation. |
| **U6 Entry effects** | Enter an Area with map animation and UI sound enabled: one short fade and the existing soft button cue. Menu music continues. Refresh does not replay the fade or add an entry effect; ordinary button clicks retain their normal sound. Disable terrain/map animation and UI sounds in existing Preferences; repeat entry and confirm those preferences are respected. Restore your preferred settings. |
| **U7 Return/preservation** | Back through Castle → Area → World → home, then Quit. Reopen and log in again. Verify the current username, owned characters, wallet and saves match the U1 baseline. |

If no Team account is available, report U4 and dependent Area checks **NOT RUN**;
retain World/profile results. Do not create extra accounts or reset data to pass
a UI check. A failed paid action or changed balance is an unexpected result:
stop before retrying it and retain the exact message.

Report U1–U7 as PASS/FAIL/NOT RUN, the actual before/after values, and WAN status
once. For a failure, include the map, action, zoom/pan sequence, resolution and
whether input was physical touch, LDPlayer's controls or the companion below.
After these checks pass, resume the first unaccepted Court step from
`COURT_WINDOWS_ACCEPTANCE_1192626.md`; do not repeat unrelated accepted work.

## Optional two-pointer companion for one LDPlayer

Use this only when LDPlayer cannot supply two independent touch pointers. First
open **World**, keep the game in the foreground, and leave the pointer alone:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
    -File 'D:\Test\wesnoth-setup\wesnoth_client\tools\Test-AndroidCourtMap.ps1' `
    -Screen world -MapIsOpen
```

Repeat later with `-Screen area` while the Castle map is open. The helper builds
an isolated test APK using the existing offline SDK/JDK, signs it with a separate
retained local test key, installs `org.wesnoth.mapcheck`, and sends pinch → finger lift →
one-finger pan. It does not use the game's signer, authenticate or issue game
commands. Device/package/foreground checks precede injection; the map must be
opened by you. Do not run it on Login, a form or a battle.

Expected: `MAP INPUT COMPANION BUILD VERIFIED`,
`MOBILE MAP GESTURES SENT; VISUAL REVIEW REQUIRED`, then
`MAP INPUT CHECK COMPLETE`. The retained `diagnostics/court-map-input-*` folder
contains source/APK hashes, a device report and before/after screenshots. Completion
means input was sent; inspect the movement before reporting U3/U4 PASS. This is
synthetic Android multi-pointer evidence, not physical-touchscreen acceptance.
The separate companion may be uninstalled normally after testing.

## Implementation and evidence scope

The Android builder generates a workspace from exact pinned shared source,
changing only the reviewed presentation boundaries. Authentication, purchases,
command identities, recovery journals and server validation stay in their existing
paths. Shared `engine/`, `alliance_game/` and `alliance_native/` files are unchanged.
The smaller overview PNGs have exact hashes in the existing asset manifest; the
original art and geometry remain intact.

Native SDL fixtures exercise actual GUI2 rendering and touch dispatch. Their
server responses are fixtures. Software-renderer frame intervals are not an
LDPlayer GPU benchmark, and sound settings/source checks are not audio audition.
The separate Android input companion is compiled and signed on the VPS; execution
on LDPlayer remains a Windows check. See `SMOOTH_MAPS_VERIFICATION_1192627.json`
for measured evidence, limitations, preservation records and build outputs.

The VPS uses a software renderer: measured input-to-viewport p95 is 10–15 ms,
with no camera-only texture creation. Whole-window cadence is slower than the
60-FPS target in that renderer; LDPlayer performance and physical touch remain
unaccepted until U3/U4.

Pointer continuity follows Android's documented [drag and scale guidance](https://developer.android.com/develop/ui/views/touch-and-input/gestures/scale).
Device performance investigation can use Android's [system tracing guidance](https://developer.android.com/games/optimize).
