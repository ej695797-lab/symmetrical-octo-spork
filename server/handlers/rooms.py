"""
Room data handlers: g2_getRoomData / g2_fetchRoomData
"""

from services.db import rooms, room_maps
from services.property_defaults import default_for_property


def _fallback_room_defaults(room_key: str):
    from config import IS_BUDDY
    if not IS_BUDDY:
        return None
    if room_key == "nexus":
        return dict(PUBLIC_HUB_DEFAULTS)
    if not room_key.startswith(("p_o_", "o_")):
        return None
    from services.dynamo_ops import _full_room
    if room_key.endswith("_sandbox"):
        return _full_room(room_key, "Sandbox", [32, 80, 32])
    return _full_room(room_key, "Private World", [80, 80, 80], theme="suburb", lobby=3)


def _with_public_return_room(graph: dict, theme: str) -> dict:
    """live79's fixed private exit targets nexus, absent from the beta4 seed.

    Extend only the response, using a vacant cell on an open public-room edge.
    Existing nodes, host nexus layouts and stored graphs remain untouched.
    """
    nodes = graph.get("data", [])
    if any(node.get("roomKey") == "nexus" for node in nodes):
        return graph
    occupied = {tuple(node["gridPosition"]) for node in nodes}
    for node in nodes:
        if not node.get("roomKey"):
            continue
        x, y = node["gridPosition"]
        for side, dx, dy, opposite in (("Up", 0, 1, "Down"), ("Right", 1, 0, "Left"),
                                       ("Down", 0, -1, "Up"), ("Left", -1, 0, "Right")):
            position = (x + dx, y + dy)
            if node.get("block" + side) or position in occupied:
                continue
            hub = {"roomKey": "nexus", "themeKey": theme, "gridPosition": list(position),
                   "blockUp": 1, "blockRight": 1, "blockDown": 1, "blockLeft": 1}
            hub["block" + opposite] = 0
            return {**graph, "data": [*nodes, hub], "version": graph.get("version", 0) + 1}
    return graph


async def handle_room_map_extra_properties(body: dict) -> dict:
    # live79 parser 0x50a75dc expects a dictionary keyed by roomKey under
    # data.responses, and projects each room using propertiesToGet.
    keys = body.get("roomKeys", [])
    props = body.get("propertiesToGet", [])
    responses = {}
    from services.client_profile import room_defaults
    stored = {doc["roomKey"]: doc async for doc in
              rooms.find({"roomKey": {"$in": keys}}, {"_id": 0})}
    for key in keys:
        doc = stored.get(key) or _fallback_room_defaults(key)
        if doc is None:
            continue
        values = {**room_defaults(), **doc}
        responses[doc["roomKey"]] = {
            prop: values[prop] if prop in values else default_for_property(prop)
            for prop in props}
    return {"data": {"responses": responses}}


async def handle_room_map_data(body: dict) -> dict:
    """
    fetchRoomMapData: returns the room-map graph (the list of connected room
    nodes with themes/grid positions/links) for a roomMapKey (e.g. freebuild_0).
    Seeded from the real Yeeps backend. The client needs this to build the hub;
    without it the room can't load ("no room is loaded"). Returns the full
    {"data": {uniqueID, data:[nodes], version}} envelope so the router passes it
    through unwrapped.
    """
    room_map_key = body.get("roomMapKey", "")
    doc = None
    if room_map_key:
        doc = await room_maps.find_one({"roomMapKey": room_map_key}, {"_id": 0})
    # live79 selects trickOrTreat_0 at startup. This deployment's imported
    # public world is still stored in the legacy versionKey/data format.
    # Adapt that known map at the response boundary; preserve host graphs and
    # leave unknown map keys empty instead of sending players to another world.
    from config import IS_BUDDY
    if IS_BUDDY and room_map_key and doc is None:
        legacy = await room_maps.find_one({"versionKey": room_map_key}, {"_id": 0})
        if legacy is None and room_map_key == "trickOrTreat_0":
            legacy = await room_maps.find_one({"versionKey": "beta4"}, {"_id": 0})
        if legacy is None and room_map_key.startswith(("p_o_", "o_")):
            from services.dynamo_ops import _sandbox_base, _sandbox_room_map
            base = _sandbox_base(room_map_key)
            main = await rooms.find_one({"roomKey": base}, {"_id": 0, "themeKey": 1})
            sandbox = await rooms.find_one({"roomKey": base + "_sandbox"},
                                           {"_id": 0, "themeKey": 1})
            legacy = _sandbox_room_map(room_map_key,
                main_theme=(main or {}).get("themeKey") or "suburb",
                sandbox_theme=(sandbox or {}).get("themeKey") or "sandbox")
        if legacy is not None:
            doc = {"graph": {"uniqueID": room_map_key,
                             "data": legacy.get("data", []),
                             "version": legacy.get("version", 0)}}
            if room_map_key == "trickOrTreat_0" and legacy.get("versionKey") == "beta4":
                hub = await rooms.find_one({"roomKey": "nexus"}, {"_id": 0, "themeKey": 1})
                doc["graph"] = _with_public_return_room(
                    doc["graph"], (hub or {}).get("themeKey") or PUBLIC_HUB_DEFAULTS["themeKey"])
    graph = (doc or {}).get("graph") or {"data": [], "version": 0, "uniqueID": room_map_key}
    return {"data": graph}


# Base defaults for the tutorial room
TUTORIAL_DEFAULTS = {
    "roomKey": "tutorial",
    "roomName": "Tutorial",
    "isOfficial": 1,
    "dimensions": [52, 20, 52],
    "floorDepth": 0,
    "lobbyDirection": 1,
    "door_up": {"block": 1, "position": [0, 0]},
    "door_right": {"block": 1, "position": [0, 0]},
    "door_down": {"block": 1, "position": [0, 0]},
    "door_left": {"block": 1, "position": [0, 0]},
    "isCreative": 0,
    "isModeLocked": 1,
    "isAutoCycling": 0,
    "autoCycleTimestamp": 0,
    "noSave": 1,
    "isDevLocked": 0,
    "lastEditedSessionID": 1,
    "roomDataVersion": 1,
    # Frequently requested optional room fields in older/newer clients
    "liquidType": 0,
    "liquidColor": "",
    "liquidLevel": 0,
    "itemRestrictionsIsWhiteList": 0,
    "itemRestrictionsGroupKeys": [],
    "minVersion": "1.0.0",
    "maxVersion": "",
    "versionKey": "teams1",
    "themeKey": "tutorial",
    "isVisible": 1,
}

# Overrides for non-tutorial rooms
NON_TUTORIAL_OVERRIDES = {
    "door_up": {"block": 0, "position": [0, 0]},
    "door_right": {"block": 0, "position": [0, 0]},
    "door_down": {"block": 0, "position": [0, 0]},
    "door_left": {"block": 0, "position": [0, 0]},
    "isCreative": 0,
    "isModeLocked": 0,
    "isAutoCycling": 0,
    "autoCycleTimestamp": 0,
    "noSave": 1,
    "isDevLocked": 0,
    "lastEditedSessionID": 1,
    "roomDataVersion": 1,
}

# Fallback payload for official_1 matching the older working traffic.
OFFICIAL_1_DEFAULTS = {
    "roomKey": "official_1",
    "roomName": "Tung Tung Sahur world",
    "themeKey": "suburb",
    "dimensions": [80, 80, 80],
    "floorDepth": 9,
    "lobbyDirection": 1,
    "door_up": {"block": 0, "position": [0, 0]},
    "door_right": {"block": 0, "position": [0, 0]},
    "door_down": {"block": 0, "position": [0, 0]},
    "door_left": {"block": 0, "position": [0, 0]},
    "isCreative": 0,
    "isModeLocked": 0,
    "isAutoCycling": 0,
    "autoCycleTimestamp": 0,
    "noSave": 0,
    "isDevLocked": 0,
    "lastEditedSessionID": 1,
    "roomDataVersion": 1,
}

PUBLIC_HUB_DEFAULTS = {
    **OFFICIAL_1_DEFAULTS,
    "roomKey": "nexus", "roomName": "Public Hub", "isOfficial": 1,
    "isModeLocked": 1, "noSave": 1,
}


async def handle_room_data(body: dict) -> dict:
    """
    Fetch room data for a given roomKey.
    Tries MongoDB first, falls back to tutorial/non-tutorial defaults.
    """
    room_key = body.get("roomKey", "")
    props = body.get("propertiesToGet", [])

    # Try to get room from DB
    room_doc = None
    if room_key:
        room_doc = await rooms.find_one({"roomKey": room_key}, {"_id": 0})
        if room_doc is None:
            room_doc = _fallback_room_defaults(room_key)

    # Build base values. Real room data is seeded into Mongo from the actual
    # Yeeps API (g2_getRoomData) - including distinct rooms like ch2_hub_creative
    # ("Build Central"), which is NOT a variant of ch2_hub ("Gnome Capital").
    # Defaults below are only a fallback for rooms not yet seeded.
    if not room_key or room_key == "tutorial":
        base = dict(TUTORIAL_DEFAULTS)
    elif room_key == "official_1":
        base = dict(OFFICIAL_1_DEFAULTS)
    else:
        base = dict(TUTORIAL_DEFAULTS)
        base.update(NON_TUTORIAL_OVERRIDES)
        base["roomName"] = room_key

    # Merge in real DB values (seeded from the real Yeeps API)
    if room_doc:
        base.update(room_doc)
    from services.client_profile import room_defaults
    base = {**room_defaults(), **base}

    # TESTING: un-mode-lock every room so modes can be switched freely.
    # Toggle off with env UNLOCK_ALL_MODES=0 after testing.
    from config import IS_BUDDY, UNLOCK_ALL_MODES
    if UNLOCK_ALL_MODES:
        base["isModeLocked"] = 0

    if IS_BUDDY and room_key == "wild_0":
        # Log effective values after overrides, without account/authentication data.
        print(f"[rooms] Wilds creative={base['isCreative']} noSave={base['noSave']} "
              f"modeLocked={base['isModeLocked']} freebuild={base.get('isFreebuild', 0)}")

    # Ensure requested properties are always present even if DB/defaults miss one.
    for p in props:
        if p not in base:
            base[p] = default_for_property(p)

    # Match legacy behavior: when propertiesToGet is provided, return only
    # those requested properties.
    if props:
        return {p: base[p] for p in props}
    return base


# 2.x deployment: standard hub/creative build-space size when a room's real data
# has no explicit dimensions (better than the tiny [52,20,52] tutorial default).
from config import IS_V2 as _IS_V2
if _IS_V2:
    NON_TUTORIAL_OVERRIDES.setdefault("dimensions", [128, 96, 128])
