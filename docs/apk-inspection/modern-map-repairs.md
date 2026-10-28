# Modern map, lobby and terrain contracts

> Historical investigation notes. For current behavior and testing limits, read
> [public status](../STATUS.md) and the [project guide](../PROJECT_GUIDE.md).

## Supplemental lobby data

RoomDatabase.BatchGetRoomDatasForRoomMap (0x6bae2d0) starts async body
0x6bbc9d0, which calls parser0x50a75dc. The parser selects data.responses,
enumerates its room keys, then projects each room using the requested properties.
Runtime GOTc577010 resolves responses; c580d08 roomKeys; c5747b8 propertiesToGet.
The previous generic fallback returned one flat property dictionary, so room
names/official flags could not be applied to the 20 requested nodes. The Buddy
handler now returns a dictionary of actual rooms, preserving host names and types.

## Terrain

TerrainManager.OnRoomLoaded (0x703d3f0) checks isNoTerrain at Room+0x118. The
old Buddy default was1, suppressing outside-border scenery. Normal defaults are
now0 with terrainDataVersion1. Host overrides still take precedence.

Terrain fetch state0x6bc5fdc calls decoder0x4e27924. Empty saveData succeeds
with null terrain; the callback reaches loader0x703ca68 and selects the theme's
procedural preset. Therefore absence is version1/empty data, not a SPAM block map.
Stored terrain uses its own version and bytes. A one-time exact-match repair
backed up20 unchanged Buddy seed records before changing only their terrain flag.
Custom records are excluded by full-record comparison. Backup is under build/backups.

## Map retrieval

Buddy Lambda reads now accept exact requested session chunks, including later
offsets. A later chunk requires that session's save0, preventing orphan chunks
from being concatenated after a fallback snapshot. Only offset0 can select a
current/editor-imported snapshot. A current snapshot explicitly belonging to the
requested session wins; otherwise an editor import precedes an older snapshot,
matching the existing import workflow. Payloads remain opaque and unchanged.

The local projects contain room layouts, not original saved map builds. Fixing
metadata and procedural terrain does not reconstruct missing official buildings.
No official service was contacted to obtain missing content.

## Private-world travel

The native GetPrivateRoomMapKey helper maps a fixed dummy account o_buddy_probe
to p_o_buddy_probe. The modern Lambda handler previously returned an empty graph
for that key, although Dynamo already had private-world fallback logic. Buddy
Lambda now uses the same three-node private layout, including the return doorway,
with host room themes preserved. Missing private room data uses the established
editable/saveable room defaults; supplemental lobby data includes those rooms.
Unknown public keys and community-world behavior are unchanged.

## Validation

Regression suite:41 tests,30 passed,11 APK-dependent checks skipped. Tests cover
room batch shape, missing/saved terrain, imported maps, session chunks, snapshot
precedence and orphan-chunk termination. Emulator screenshot
build/refract/shots/refract-20261027-100113.png shows restored outside terrain.
Further travel/reload validation is in progress.

NVIDIA615.71.09 repeatedly lost the emulator device; kernel reports Xid109
CTX SWITCH TIMEOUT. Refract is being tested with the installed Intel Vulkan
driver,768px eyes and CPU image streaming. All files/cache remain on the HDD.

Manual emulator travel on 2026-10-27: user entered Playground from Starter House.
Process6017 logged official_0 transition at10:22:53, loaded the room set, then
finished loading its empty map at10:22:59 with hasLoadedMapData=True. No fatal
map-load error appeared during that transition. Nonempty save/reload validation
remains pending. The temporary three-block probe was removed and the emulator
account's prior spawn setting restored before manual testing.
