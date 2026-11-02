# Full-width Court maps — Android1192628

This client-only refinement keeps protocol5, schema5, server/worker1192626 and
`runtime-v3`. Keep the existing five Windows services running. Retain the user's
1192627 "All pass" report and earlier accepted results as user-reported evidence;
no new per-step measurements or numerical player baseline were supplied.

World and each Area start with artwork fitted to the map viewport's width.
The first view may crop the top or bottom: pan to reach those parts, or use
Centre for the full overview. Returning and refreshing retain the camera.
Map navigation, zoom and selection actions share one top toolbar. The minus
and plus controls use drawn symbols with normal native touch buttons.
Location information remains in the compact bottom details panel. Ordinary
forms keep their existing review/confirmation controls.

## Client update

Finish the current action, save any campaign and Quit the game. Leave LDPlayer
and all five services open. Record your current player, owned heroes, Available
Crowns and Held Crowns; compare with these current values after installation.
Temporarily enable WAN only for the client transfer:

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $toolbarBash = 'D:\Test\wesnoth-setup\msys64\usr\bin\bash.exe'
    & $toolbarBash --noprofile --norc -c 'PATH=/usr/bin:$PATH /usr/bin/git -C /d/Test/wesnoth-setup/wesnoth_client pull --ff-only origin main'
    if ($LASTEXITCODE -ne 0) { throw 'Client pull stopped. Preserve README edits and send the complete output.' }
    & $toolbarBash --noprofile --norc -c 'PATH=/usr/bin:$PATH /usr/bin/git -C /d/Test/wesnoth-setup/wesnoth_client rev-parse HEAD'
    if ($LASTEXITCODE -ne 0) { throw 'Commit check failed. Stop and send the output.' }
    & $toolbarBash --noprofile --norc -c 'PATH=/usr/bin:$PATH /usr/bin/git -C /d/Test/wesnoth-setup/wesnoth_client merge-base --is-ancestor 3f12c4f31cf874da86270b110651eda616182377 HEAD'
    if ($LASTEXITCODE -ne 0) { throw 'The1192628 implementation is missing. Stop before rebuilding and send the output.' }
}
```

Disconnect WAN again while keeping local Ethernet connected. Record that status
once for this1192628 batch. Then run:

```powershell
& {
    $ErrorActionPreference = 'Stop'
    powershell.exe -NoProfile -ExecutionPolicy Bypass `
        -File 'D:\Test\wesnoth-setup\wesnoth_client\tools\Rebuild-WindowsAndroidAccountGame.ps1'
    if ($LASTEXITCODE -ne 0) { throw 'Toolbar rebuild stopped. Preserve all stages and send the build/rebuild logs.' }

    powershell.exe -NoProfile -ExecutionPolicy Bypass `
        -File 'D:\Test\wesnoth-setup\wesnoth_client\tools\Install-AndroidAccountGame.ps1' `
        -KeepSavedProfile
    if ($LASTEXITCODE -ne 0) { throw 'Installation stopped. Preserve app data and send the install log.' }
}
```

Expected: build/rebuild complete, artifact verification, streamed installation
Success, installed version1192628 and `ANDROID ACCOUNT GAME INSTALL COMPLETE`.
Retain the printed client `diagnostics/android-account-{build,rebuild,install}-*`
logs. Send the complete output before the touch checks. No server pull, bridge,
Room or worker build, dependency setup, database work or profile import is needed.

## Focused acceptance

Stop at the first unexpected result and report the exact screen/action/input.
Keep existing saves and avoid paid actions or battles during these UI checks.

| Check | Test and expected result |
|---|---|
| T1 | Log in as the same player. Hero and wallet values match the recorded pre-update values; Load lists the existing saves. |
| T2 | Open Team → World for the first time after launch. Artwork fills the map viewport's width and every map button is on one top row, without overlap or clipped labels. Minus is a clear horizontal symbol and reduces zoom; Plus increases it; Centre displays the full overview. |
| T3 | Pan, pinch and select a location; use the separate Enter action. Open an eligible Area and repeat the initial width, toolbar and minus checks. Returning to World/Area keeps that map's camera. Without an eligible Team, report Area checks NOT RUN. |
| T4 | Select a location, show/hide Details and Refresh. The map does not jump or shrink when the panel changes; selection remains. Search switches to the searchable list; return to Map preserves the camera. Check Back and Inbox. With a Team, check My Team; without a Team, check Find Team and New Team. Cancel forms before confirming changes. |
| T5 | Quit and reopen; Login is required. Current hero/wallet/save values remain unchanged. |

If LDPlayer cannot provide independent two-finger input, follow the existing
[optional companion commands](SMOOTH_MAPS_1192627.md#optional-two-pointer-companion-for-one-ldplayer).
Use the World command only with World open, and the Area command only with its
Castle map open. The updated helper requires1192628. Its success means synthetic
input was sent, not that physical touchscreen usability was verified. Record the
visible gesture outcome separately.

After this refinement passes, resume the first unaccepted Court acceptance step.
Do not repeat unrelated accepted work.
