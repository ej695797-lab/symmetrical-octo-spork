# live79 APK inspection and Buddy migration findings

> Historical investigation notes. For current behavior and testing limits, read
> [public status](../STATUS.md) and the [project guide](../PROJECT_GUIDE.md).

Inspected 2026-10-26. Current target distribution name: **2.32.0-buddy** (updated after the investigation).
The user confirmed that this supplied APK should be the base even though its
Android and Unity version fields are 2.32.0.

This document records the initial static investigation. For the subsequent
implementation and verification, see the repository README and implementation
plan. The investigation itself is not a claim that a working server
or patched APK has been produced. The source APK was not changed, launched, or
connected to its author's server. No game/backend implementation changed during
this investigation.

## Source and evidence

- Input: `/path/to/live79-blaze.apk`.
- APK SHA256: `21e203194d07a20a68f37b4d34d74ab2c34e94077f38ae26d9caf90c8e37a905`.
- Package: `com.TrassGames.Yeeps`.
- Android manifest: versionName `2.32.0`, versionCode `957`.
- Unity PlayerSettings also contains `2.32.0`.
- Unity BuildSettings and serialized-file versions: `6000.1.17f1`.
- Minimum and target Android SDK: 32. Native ABI: arm64-v8a.
- Native mod build ID: `live79`.
- Mod startup text includes `2.31.4-blaze`; some older diagnostic messages still
  say `2.4.0-blaze`. Those messages do not establish the underlying game version.

Selected recovered evidence is in [live79/](live79/). It contains no copied APK,
native binaries, signing keys, Photon app IDs, or Unity authentication tokens.
Large extraction and disassembly files remain under
`/tmp/yeeps-2314-inspection/` and are disposable.

## 1. The APK is not a configurable kit template

The APK has `libBNMExplorer.so` but **no `libblazeconfig.so`**. Consequently,
`tools/configure_apk.py` currently refuses it. Merely adding a settings file would
not establish that the existing mod reads it: no kit-settings loader was found
in the inspected mod symbols.

Native ELF symbols and relocations identify these embedded settings:

| Native object | ELF virtual address | Observed purpose/value |
|---|---|---|
| `kBuildId` | `0xad9920` | `live79` |
| `kOurPhotonAppId` | `0xad9c30` | Embedded Realtime app ID |
| `kOurVoiceAppId` | `0xad9c38` | Embedded Voice app ID |
| `kSelfBase` | `0xad9c40` | `https://live.yeeps-tools.org/xxxxxxxxxxxxxxxxxx` |
| `kDevlogHost` | `0xad9c80` | `live.yeeps-tools.org` |
| `kCosHost` | `0xad9c98` | `live.yeeps-tools.org` |

These are virtual addresses in this exact ELF, **not generic file offsets**.
Pointer relocation and string storage must be respected in any patcher.

`RewriteBackendUrl` at `0x5646fc` recognizes AWS API Gateway, Lambda, and DynamoDB
hosts and uses `kSelfBase`. It also contains special handling for Yeeps domains
and other asset hosts. The server's existing `/xxxxxxxxxxxxxxxxxx`, `/k/`, and
`/c/` prefix handling is therefore a useful starting point, but actual request
paths and query parameters still need runtime capture.

Changing only `kSelfBase` would leave independent logging/cosmetic hosts in the
binary. Inventory and redirect or disable every relevant client service when
making an independent build.

## 2. Native compatibility requires more than replacing the library

The repo's BNM configuration currently selects `UNITY_VER 213` and
`UNITY_PATCH_VER 19`. This APK uses Unity 6000.1.17f1. The repository's
`InstallAccountValidateBypass` also contains hardcoded 1.31-specific RVAs.
Those offsets must not be carried into the new APK.

More significantly, this APK's `libil2cpp.so` exports no standard `il2cpp_*`
names. The existing mod supplies `BNM_dlsym_livemap_impl` at `0x5eba6c`, with
indexed export-name tables for several game builds. Its V4 table matches this
APK.

**Recovered result: 241 canonical IL2CPP API names mapped to 241 actual exports;
every mapping was checked against the supplied game's ELF dynamic symbol table.**
See [il2cpp-api-map.json](live79/il2cpp-api-map.json).

Examples, all RVAs specific to this input:

| Canonical API | Actual export | RVA |
|---|---|---|
| `il2cpp_init` | `OTNznPGLsJO` | `0x4b962d4` |
| `il2cpp_domain_get` | `qHYzgqSkosU` | `0x4b968f8` |
| `il2cpp_class_from_name` | `xoUPYEohgR_` | `0x4b96434` |
| `il2cpp_runtime_invoke` | `KQeifRVLVlh` | `0x4b96d1c` |
| `il2cpp_raise_exception` | `eiruJFYACFW` | `0x4b96928` |

The input mod also has hooks absent from this repo's 1.31 implementation:

- `UnityWebRequest.InternalSetUrl` and the two-argument `System.Uri` constructor.
- JSON null handling and a build-specific, obfuscated Lambda helper.
- `AccountOnlineManager.DoRefresh` handling.
- `AccountManager.ValidateHash`, found by managed method name rather than the
  old 1.31 account-coroutine RVAs.
- Room permission handling, version text handling, and connection-pool tuning.

These are observed differences, not a recommendation to preserve every bypass.
The minimum required set must be established against the independent backend.
The native binary contains debug symbols, which made this inspection possible;
the complete newer C++ source is not in this repository.

## 3. Photon settings are embedded in multiple places

The two native authentication hooks call `OurAppId` at `0x563160` before invoking
Photon's original methods. That function recognizes the embedded Realtime and
Voice IDs and can substitute the mod's Realtime ID for other values. Editing
Unity settings alone is therefore insufficient.

Unity ResourceManager contains:

| Resource | Serialized file | Path ID |
|---|---|---|
| `photonserversettings` | `resources.assets` | `150717` |
| `voiceappsettings` | `resources.assets` | `150718` |

The PhotonServerSettings asset contains two app IDs also present in native
constants. VoiceAppSettings contains another app ID not found in the mod's
`.rodata`. Both assets and native substitution need review/configuration.

For independent deployment, use the host's own Photon apps and custom-auth URL.
End-to-end testing must include both room joins and actual Voice connection.

## 4. Login/account compatibility: do not assume the old 2.x profile is correct

Readable, length-prefixed constant runs in `global-metadata.dat` expose account,
room, map, room-map, and global-property names. The file header is nonstandard
(`6f 06 09 08`), so this was not a complete conventional IL2CPP metadata dump.
Recovered strings do not prove which fields are mandatory, their types, or the
exact login response envelope.

The account-adjacent constants include:

- `accessKey`, `secretKey`, `sessionToken`.
- `gameSessionID`, `publicSessionID`, **`challengeNonces`** (plural).
- `customizedItemDatas`, `currency_ciPending`, `completedPuzzles`.
- `longestLoginStreakData`, `longestLoginStreakRestoreCost`, `lootQuotaProgress`.
- `collectedCardShardIDs`, `collectedButtonRewardKeys`, `featureGrants`.
- `mobileLinkSessionID`, `dataSegment`.

No literal `e_accessKey` or `e_secretKey` was found in this metadata file. The
older server's credential key/IV were not found as raw bytes, hex strings, or
base64 strings in this APK's `libil2cpp.so`. These negative searches do not prove
that all credential encryption is absent, but they **do not support reusing the
old 2.4.0 encrypted-credential assumption**.

The current server already emits plaintext placeholder credentials. Preserve
that useful behavior while validating the new client contract. Its `IS_V2`
branch also encrypts credentials with a historical key and changes other
behavior based only on the major version. A profile for this exact APK is safer
than treating every 2.x build alike.

See [metadata-property-runs.json](live79/metadata-property-runs.json). The
account runs include nested pet fields such as `name`, `level`, and
`location_position`; **do not promote every recovered name to a top-level
account property**.

## 5. Room defaults and terrain need explicit types and runtime verification

Recovered room constants include newer settings such as:

- `visitVersion`, `teamItemRestrictions`, `gravityType`.
- `noCopy`, `noPaste`, `isFreebuild`, `isSolo`, `overrideMaxPlayers`.
- `mapDescription`, `warpPortalDescription`, `thumbnail_s3_uri`.
- `passagewayLinks_creative`, `overrideTokenUniqueID`.
- `lastValidSaveOffset`, `lastEditedTerrainSessionID`, `terrainDataVersion`.
- `isNoTerrain`, `isNoFloor`, `overrideExpireDurationMinutes`,
  `overrideSpawnPosition`.

The current heuristic `default_for_property` supplies empty strings for several
of these, including `gravityType`, `overrideMaxPlayers`,
`teamItemRestrictions`, and `overrideSpawnPosition`. Their expected types must
be recovered from the client or observed responses before selecting defaults.

The existing world can provide a temporary room graph for initial tests, but
the 1.31 room documents are not a verified modern world configuration. Use a
separate Buddy database so old accounts and host settings are not silently
reinterpreted. `seed/load_world.py` uses insert-only seeding, so changing seed
JSON alone will not migrate existing databases.

Map table names and properties still include `G2_Maps`, `mapDataVersion`,
`saveData`, and `saveInternalStates`. This suggests reusable plumbing, **not
confirmed binary-format compatibility**. The repo writes SPAM v3, and one
Lambda fetch path explicitly refuses SPAM while the DynamoDB path accepts it.
New map, terrain, internal-state, and chunk-offset behavior must be checked with
a real client save and reload. Preserve raw save blobs until a round trip is
verified; speculative decode/re-encode risks losing new fields.

## 6. Rebuild catalogs from the new APK

`PatternManager` is a MonoBehaviour in `level2`, path ID `495`, with 364,800 bytes
of serialized data. Its map-block records expose original-case keys and names.
ResourceManager provides independent prefab-path evidence.

Recovered map-block catalog:

- 1,647 distinct `mapObject` key/name entries.
- 1,629 have a direct matching lowercase ResourceManager prefab path.
- The other 18 are item-decoration spawner variants; absence of a direct path
  does not establish that they are unusable.
- Only 714 exact keys overlap the old `valid_mapobject_keys` seed.
- 933 recovered map-block keys are absent from the old allowlist.

See [mapobject-catalog.json](live79/mapobject-catalog.json) and
[mapobject-comparison.json](live79/mapobject-comparison.json).

The old allowlist also includes non-map-object keys. The catalog above does not
enumerate every cosmetic, held item, pet, bundle, or ownership key. Therefore,
the old keys missing from this map-block catalog must **not** all be declared
invalid or deleted from accounts automatically. Ownership and tech-web lists
need their own complete catalog extraction.

The APK includes custom Cucumber Cat, BLAZE Pillow, and BLAZE Screen entries.
Its content differs from the old kit's "exactly one added block" description.
This investigation preserved those assets. Naming the distribution Buddy does
not automatically specify replacement of every embedded custom-content asset.

## 7. The mod expects services that kit2 removed

Observed native paths include:

```text
/client/feed.txt
/client/video.txt
/client/skel?room=
/client/dances/
/client/creative_builders
/client/cards/frostbadge.rgb
/client/cosmetics/frostpack.bundle
/client/cosmetics/frost2.bundle
/client/devlog
```

Native symbols show screen/video, remote logging, cosmetic download, friend
feed, and skeleton functionality. These are beyond this repo's game-plus-BNM
scope. The current catch-all returns API-Gateway-style 403s for unknown 2.x
routes; that is not an implementation of these protocols.

Choose whether to remove optional client integrations or implement their actual
contracts. Do not invent successful placeholder bodies until their parsers are
understood. Independent host constants for devlog and cosmetics must be handled
alongside the gameplay backend.

## Files implicated by the migration

| Area | Repository files |
|---|---|
| Client profile/version/database | `server/config.py`, `server/.env.example` |
| Login and account defaults | `server/handlers/login.py`, `server/services/account_privacy.py` |
| Routing/envelopes/custom auth | `server/main.py`, `server/routers/lambda_router.py` |
| Typed defaults/room reads | `server/services/property_defaults.py`, `server/services/dynamo_ops.py`, `server/handlers/rooms.py` |
| Map/terrain reads and saves | `server/handlers/maps.py`, `server/services/mapsync_decoder.py`, `server/services/mapsync_serializer.py` |
| World/catalog data | `server/seed/world/*.json`, `server/seed/load_world.py`, `server/handlers/challenges.py` |
| Build/identity/moderation compatibility | `server/services/build_gate.py`, `server/services/device_auth.py`, `server/handlers/moderation.py` |
| Native loader and hooks | `client/native/native-lib.cpp`, BNM loading/resolver code and Unity settings |
| APK assembly | `tools/configure_apk.py` |
| Validation | `tools/smoke_test.py` and new recorded-request/format checks |

## Recommended sequence

1. Keep the supplied APK immutable and use a separate Buddy database/profile.
2. Make an inspection build that routes gameplay to the host's backend, uses
   the host's Photon apps, and redirects/disables independent remote services.
   Preserve the newer mod's working compatibility layer during initial
   protocol discovery where practical.
3. Capture first-launch login, account/global reads, room graph and room reads,
   map fetches, and Photon auth. Resolve response envelopes and field types
   against that evidence. Update the server and replay meaningful captures.
4. Verify spawn and a second player's room join; then Voice, private worlds,
   cosmetics, and purchases. Verify map placement/save/relaunch and terrain
   separately before allowing valuable builds.
5. Port a maintainable Buddy native library with Unity-appropriate layouts and
   the recovered export resolver. Replace the hardcoded 1.31 offsets. Add
   supported settings loading and Buddy version labeling.
6. Update the APK tool and documentation for this source APK. Preserve its ZIP
   compression properties and verify alignment/signatures on the final APK.

Simply changing `REQUIRED_CLIENT_VERSION`, renaming the APK, or injecting the
old native library will not complete this migration.

## Validation performed and remaining limits

Performed: ZIP inventory and source hashing; binary Android XML parsing; Unity
asset/settings inspection; native symbol, relocation, and ARM64 instruction
inspection; all 241 recovered API export checks; map-block/resource cross-checks;
comparison with existing server routing, seeds, defaults, and native hooks.

Not performed: installing/running the APK, headset log capture, login against a
Buddy backend, Photon room/Voice joins, map/terrain round trips, or APK
repackaging/signing. No Android `adb` executable was available on PATH during
inspection. A working-server claim requires those runtime checks.

### Refract reproduction: account parser container mismatch (2026-10-27)

The supplied client completes the HTTPS login request through the configured
Tailscale Funnel, then throws `NullReferenceException` and shows the empty error
rectangle. Read-only runtime Explorer metadata identifies the throwing nested
wrapper method at IL2CPP RVA `0x50a4df0`. AccountManager calls it at `0x53b9124`
with the string literal `customizedItemDatas` (GOT slot `0xc5848b8`). Its root
reader at `0x50a0f74` indexes the literal `data` (`0xc572a38`), confirming that the
existing response envelope is correct.

The accessor reads `data.customizedItemDatas`, invokes `JSONNode.AsArray`, then
explicitly throws on a null result (`0x50a4ea4` -> `0x50a4f94`). The initial Buddy
profile incorrectly supplied `{}`. The client requires an array of customization
records; an empty inventory must be `[]`. This establishes the cause directly
from the throwing method, rather than inferring container types from names.
Previously seeded overrides and accounts may retain the empty object, so fixing
only the default for new installs is insufficient. Existing nonempty array data
must remain intact.

### Runtime credential contract (2026-10-27)

After correcting `customizedItemDatas`, the client passes account parsing and
reports `Failed to get credentials`. The callback at `0x53d5d78` reads
`e_accessKey` and `e_secretKey` through GOT slots `0xc57ca70` and `0xc576210`,
then rejects empty strings. The earlier static metadata search missed these
constants; it cannot justify removing the fields.

The credential setter at `0x509f74c` concatenates `EnvironmentVariables.test`,
account ID, and `oculusNonce`. The login state machine at `0x53dada8` writes
that closure argument to the `oculusNonce` field (GOT `0xc584d68`); it is
not the request password. The decryptor at `0x509ef8c` uses PBKDF2-SHA256
with the 13-byte salt `Ivan Medvedev`, 1000 iterations, and 48 derived bytes.
The first 32 bytes are the AES-256 key and the remaining 16 the CBC IV.
Plaintext uses UTF-16LE and PKCS7 padding; the helper's base64 format substitutes
spaces for plus signs. The salt's SHA256 matches the runtime constant field
name. These settings were checked against the client's pure encryption helper
at `0x509d3b4` using only fixed dummy inputs, including the server's public
placeholder AWS credentials. Captured known-answer vectors are asserted in the
server regression test. No player passwords or device keys belong in fixtures.

Buddy responses now generate the encrypted pair per login using its nonce
(or an empty suffix when absent), while retaining the existing plaintext
compatibility fields. Encrypted values are not persisted across nonce changes. Historical client profiles
retain their previous encoding.

### Empty-map fetch contract (2026-10-27)

After account parsing, credential decryption, room-graph loading, and joining
Photon succeeded, the client reported `Outdated version` while fetching the
starting map. The Buddy database contains no maps or imported maps. The Lambda
handler returned only `saveData: ""` for that case.

`RoomDatabase.FetchMapDataDirect` (`0x6ba83ec`, async body `0x6bbf780`) reads
`mapDataVersion` with a default of -1 at `0x6bbf9c8`. It accepts 1, 2, or 3;
other values take the unsupported-version failure branch at `0x6bbfabc`. Thus
the error did not establish that an existing map was too new.

Buddy missing-map responses now match the existing DynamoDB fallback: version 3,
a valid empty SPAM map, and empty internal states. Later chunk offsets keep
empty `saveData` as the terminator and include the supported version. Nothing
is written to the database; existing stored payloads remain opaque and unchanged.
