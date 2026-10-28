# Buddy development status

Updated 2026-10-27. This is the public handoff for an early-stage project.
Local `HANDOFF.md`, deployment logs/accounts/endpoints and evidence artifacts are
private and absent from fresh clones. Earlier investigation documents may
describe superseded behavior; this file describes the current implementation.

## Implemented

- Inspected live79 game 2.32.0, distributed as 2.32.0-buddy in the separate
  `com.BuddyGames.Buddy` package. Keep the Unity 6000 library and SDK dispatch-slot
  hooks; earlier inline jumps corrupted adjacent 12-byte SDK stubs.
- Backend profile `buddy232`, default database `Yeeps232Buddy`. Modern account
  arrays/credentials, room batches, terrain defaults, map chunk chains and
  private-world graphs have compatibility fixes and regression coverage.
- Ownership uses 6,263 PatternManager keys, distinct from 1,629 MapObject keys.
  Normal accounts receive no staff roles by default. Custom keys need explicit
  grants only to accounts with matching APKs.
- Explicit compact installer builds Public Hub, Welcome and Playground. Wilds
  has zero placed blocks, native ground and temporary Creative mode. Private
  worlds support persistent builds. Public graph aliases are `trickOrTreat_0`
  and `public`; the old `beta4` seed remains a fallback.
- Modern GLB and separate music-disc tools; APK builder supports complete edited
  asset folders. Fumo interaction uses a normal pillow's `Destructable` root tag,
  Ground layer and an enabled nontrigger root collider.

## Wilds repair and older installations

Native public-room initialization rejects ordinary players' Creative joins
when Wilds has `noSave=0`. Corrected generator flags for Wilds:
`isCreative=1`, `noSave=1`, `isModeLocked=0`, `isFreebuild=0`, `isOfficial=0`.
Hub rooms keep `noSave=0` to load their generated builds.

The installer preserves already installed layouts, so rerunning it does not
repair old Wilds settings. Inspect the active database, back up the exact affected
record, apply a targeted update and verify private data is unchanged. Do not
reset the database or reinstall the world to repair a single flag.

Creative is separate from Photon's round-mode key: `gameMode=hangout` can coexist
with `creativeMode=true`. Do not invent a `creative` game-mode key. Refract
showed stable joins and actual Creative state after this repair; block placement
and removal in Wilds remain unverified. Wilds builds are temporary.

## Remaining testing

Quest reports include working private-world placement/save/load, return to
Public Hub and Fumo placement. These observations are from one development setup.
Fumo pickup after its asset repair, disc playback/save/reload, all public routes,
full cosmetics, moderator UI and multiplayer/Voice still need verification.
Photon Voice unsupported-authentication errors remain a separate open issue.
Offline tests, signing and backend health do not prove gameplay compatibility.

Never invoke native `RoomManager.GoToRoom` through Explorer: it previously
crashed. Explorer `/api/invoke` also aborted the app even for a GameManager
getter. Prefer metadata inspection, logs and manual user navigation. Never
automate headset power keys, clear app data or replace custom builds with older
test APKs as routine troubleshooting.
