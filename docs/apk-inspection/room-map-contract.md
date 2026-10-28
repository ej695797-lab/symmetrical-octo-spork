# live79 starting room map

> Historical investigation notes. For current behavior and testing limits, read
> [public status](../STATUS.md) and the [project guide](../PROJECT_GUIDE.md).

On 2026-10-27, the supplied live79 client (game 2.32.0) requested
`fetchRoomMapData` for `trickOrTreat_0`, then tried to locate room `official_1`
inside the returned graph. In `build/refract/runs/20261027-083255/logcat.txt`,
the request appears at 09:07:03.313, succeeds at 09:07:04.014, and fails with
`NO_NODE_FOUND` at 09:07:04.284. This is distinct from the credential warnings.

Read-only inspection of `Yeeps232Buddy.room_maps` found one imported document:
`versionKey: beta4`, `data: [22 nodes]`, `version: 1`. It includes `official_1`.
The fetch handler queried `roomMapKey` and expected a nested `graph`, so neither
the document key nor its payload shape matched. It returned
`{"data":{"uniqueID":"trickOrTreat_0","data":[],"version":0}}`.
The client accepted the empty graph and then could not find its starting node.

The Buddy handler now adapts an exact legacy `versionKey` match, and explicitly
aliases the observed starting map `trickOrTreat_0` to the imported `beta4` public
world when no exact graph exists. The imported nodes and version are preserved;
`uniqueID` uses the requested map key. Existing host graphs take precedence.
Unknown keys remain empty, legacy profile behavior stays unchanged, and the
database is not rewritten. This exposes the imported world, not a reconstruction
of the production seasonal map.

## Native node metadata

Read-only native class inspection of `RoomMapNodeData` reports:

| Field | Native type |
| --- | --- |
| roomKey, roomName, themeKey, transitionKey | String |
| gridPosition | Vector2Int |
| blockUp, blockRight, blockDown, blockLeft, isOfficial | Boolean |
| passagewayLinks, nodeTypes | List |
| visitVersion, maxMapSessionID, maxTerrainSessionID | Int32 |
| timeMachineRoomKey, gameModeKey | String |
| lobbyWarpPortalRoomKey, lobbyWarpPortalRoomMapKey, lobbyWarpPortalColorKey | String |
| displayType | Obfuscated enum |

The legacy seed uses `roomKey`, `themeKey`, `[x,y]` grid positions, optional
transition keys and numeric 0/1 block flags. This patch preserves those values.
Class metadata establishes field types, not which absent JSON fields the parser
requires or what defaults it supplies; no extra node defaults were guessed.

Five isolated regression tests cover the populated starting graph, exact legacy
key adaptation, host graph precedence, unknown keys, and legacy profile behavior.
Both populated-map tests failed with the previous handler and pass after the
adaptation. Emulator validation after the backend restart loaded the populated graph and
spawn room, then joined Photon. A separate missing mapDataVersion in the empty-map
response initially blocked map loading; after that correction, the loading screen
was removed and the viewer showed the playable room.
