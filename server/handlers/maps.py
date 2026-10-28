"""
Map save/fetch handlers: g2_saveRoomMapCompressed / fetchMapData.
Stores map data with version history.
"""

import os

from datetime import datetime, timezone

from services.db import maps, rooms
from config import IS_BUDDY

# Official rooms that must NOT be editable in-game (raid protection). The host
# changes them with tools/import_map.py (a separate code path). Override
# with env PROTECTED_ROOMS (comma list) and/or PROTECTED_ROOM_PREFIX.
_PROTECTED_ROOMS = set(filter(None, (os.getenv("PROTECTED_ROOMS") or
    "official_0,official_1,official_2,tutorial").replace(" ", "").split(",")))
_PROTECTED_PREFIX = os.getenv("PROTECTED_ROOM_PREFIX", "official_")


def _is_protected_room(room_key: str) -> bool:
    return bool(room_key) and ((IS_BUDDY and room_key == "nexus") or room_key in _PROTECTED_ROOMS or
                               (bool(_PROTECTED_PREFIX) and room_key.startswith(_PROTECTED_PREFIX)))


# Who MAY edit official/protected maps in-game (everyone else is blocked = raid
# protection). Map makers carry the `developer_mapMaker` role (NOT a default
# role, so regular players don't have it); staff and the testing toggle also pass.
MAP_EDIT_ROLES = set(filter(None, (os.getenv("MAP_EDIT_ROLES") or
    "developer_mapMaker").replace(" ", "").split(",")))


# Owner's rule (2026-10-10): every map except a player's own private world is
# saved only by holders of the in-game "blaze" role, from their own bound
# headset. Everyone else's saves are acknowledged and dropped.
SHARED_SAVE_ROLE = os.getenv("SHARED_SAVE_ROLE", "blaze")


# Official rooms (2026-10-11): people found how to flip a room to creative with
# BNM and save it. In-game saves to an official room are refused for everyone,
# whatever mode the room is in (the save request doesn't say which mode it's
# in). Official maps change only by import (tools/import_map.py) or while
# UNLOCK_ALL_MODES is on. Read from the room
# record (isOfficial), cached a minute, with the known official names as a floor.
_OFFICIAL_PREFIX = ("official_", "bam_", "halloween_", "bonus_", "researchFacility", "tutorial", "freebuild_ch2")
_official_cache: dict = {}


async def _is_official_room(room_key: str) -> bool:
    if not room_key or room_key.startswith("p_"):
        return False
    # Kit: UNLOCK_ALL_MODES=1 is the host's build switch, so official rooms take
    # in-game saves while it's on (turn it off again when the maps are done).
    from config import UNLOCK_ALL_MODES
    if UNLOCK_ALL_MODES:
        return False
    if (IS_BUDDY and room_key == "nexus") or room_key.startswith(_OFFICIAL_PREFIX):
        return True
    import time as _t
    hit = _official_cache.get(room_key)
    if hit and hit[1] > _t.monotonic():
        return hit[0]
    try:
        doc = await rooms.find_one({"roomKey": room_key}, {"_id": 0, "isOfficial": 1})
        val = bool(doc) and int((doc or {}).get("isOfficial") or 0) == 1
    except Exception:
        val = False
    _official_cache[room_key] = (val, _t.monotonic() + 60)
    return val


def _is_own_pw(account_id: str, room_key: str) -> bool:
    """room_key is account_id's private world (main room or _sandbox)."""
    if not account_id:
        return False
    own = f"p_{account_id}"
    return room_key == own or room_key.startswith(own + "_")


async def _from_bound_headset(account_id: str, body: dict) -> bool:
    try:
        from services import device_auth
        bound = await device_auth._keys_of(account_id)
        key = body.get("_deviceKey", "")
        return bool(bound) and device_auth.valid_key(key) and device_auth.key_hash(key) in bound
    except Exception as e:
        print(f"[maps] headset check failed for {account_id}: {e}")
        return False


async def _may_save_shared(account_id: str, room_key: str, body: dict) -> bool:
    """True if this save may be stored (always for the saver's own private world)."""
    if _is_own_pw(account_id, room_key):
        return True
    try:
        from config import UNLOCK_ALL_MODES
        if UNLOCK_ALL_MODES:
            return True
    except Exception:
        pass
    why = ""
    try:
        acc = await accounts.find_one({"accountID": account_id}, {"_id": 0, "roleKeys": 1})
        from services import role_locks
        await role_locks.load_blaze_holders()
        if SHARED_SAVE_ROLE not in ((acc or {}).get("roleKeys") or []) or (
                SHARED_SAVE_ROLE == role_locks.BLAZE_ROLE and not role_locks.is_blaze_holder(account_id)):
            why = f"no {SHARED_SAVE_ROLE} role"
        else:
            from services import device_auth
            bound = await device_auth._keys_of(account_id)
            key = body.get("_deviceKey", "")
            if not bound or not device_auth.valid_key(key) or device_auth.key_hash(key) not in bound:
                why = "not from its bound headset"
    except Exception as e:
        why = f"check failed: {e}"
    if why:
        print(f"[maps] BLOCKED save to {room_key} by {account_id}: {why} ip={body.get('_clientIP', '')}")
        return False
    return True


async def _can_edit_protected(account_id: str, body: dict | None = None) -> bool:
    """True if this account may edit a protected (official) map in-game."""
    try:
        from config import UNLOCK_ALL_MODES
        if UNLOCK_ALL_MODES:     # testing: anyone can edit maps
            return True
    except Exception:
        pass
    if not account_id:
        return False
    # The save names its account in the body, so a script can claim any account
    # with no headset bound yet (Oct 10: one posed as an owner account and
    # emptied Playground, Starter House, official_2 and the tutorial). Official
    # maps only take saves from the account's own bound headset.
    if body is not None:
        try:
            from services import device_auth
            bound = await device_auth._keys_of(account_id)
            key = body.get("_deviceKey", "")
            if not bound or not device_auth.valid_key(key) or device_auth.key_hash(key) not in bound:
                print(f"[maps] protected-room save refused for {account_id}: not from its bound headset "
                      f"ip={body.get('_clientIP', '')}")
                return False
        except Exception as e:
            print(f"[maps] headset check failed for {account_id}: {e}")
            return False
    try:
        from handlers.moderation import MOD_STAFF
        if account_id in MOD_STAFF:
            return True
    except Exception:
        pass
    try:
        acc = await accounts.find_one({"accountID": account_id}, {"_id": 0, "roleKeys": 1})
        if set((acc or {}).get("roleKeys") or []) & MAP_EDIT_ROLES:
            return True
    except Exception:
        pass
    return False

# Room-property fields that g2_saveRoomMapProperties may carry. Floor/ground
# level (floorDepth), liquid, lighting, theme, doors etc. live on G2_Rooms, not
# in the map blob — if we don't persist them here they reset on the next load.
_ROOM_PROP_FIELDS = {
    "floorDepth", "dimensions", "liquidType", "liquidLevel", "liquidColor",
    "customLightingKey", "lobbyDirection", "themeKey", "isModeLocked", "noSave",
    "isSaveLocked", "isDevLocked", "noPaste", "autoCycleTimestamp", "isAutoCycling",
    "door_up", "door_down", "door_left", "door_right", "roomName",
    "itemRestrictionsGroupKeys", "itemRestrictionsIsWhiteList",
    "terrainDataVersion", "roomDataVersion",
}


async def handle_save_room_map_properties(body: dict) -> dict:
    """Persist room-level properties (floor/ground level, liquid, lighting,
    theme, doors, ...) changed in game. These are stored on G2_Rooms; the
    client sends them via g2_saveRoomMapProperties, separate from the map blob."""
    room_key = body.get("roomKey", "")
    account_id = body.get("accountID", "")
    if not room_key:
        return {"success": 0}
    if room_key.startswith("c_"):          # community worlds are read live from Yeeps, never stored here
        print(f"[cw] property save ignored (live from Yeeps): {room_key} by {account_id}")
        return {"success": 1}
    # Raid protection: regular players can't change official rooms; map
    # makers / staff (and anyone while UNLOCK_ALL_MODES is on) may edit them in-game.
    if _is_protected_room(room_key) and not await _can_edit_protected(account_id, body):
        print(f"[maps] BLOCKED in-game property edit to protected room {room_key} by {account_id}")
        return {"success": 1}
    if await _is_official_room(room_key):
        print(f"[maps] BLOCKED in-game save to official room {room_key} by {account_id}")
        return {"success": 1}
    if not await _may_save_shared(account_id, room_key, body):
        return {"success": 1}
    props = {}
    # The ground-level shifter sends the new floor as "newDepth".
    if "newDepth" in body:
        props["floorDepth"] = body["newDepth"]
    for container in (body, body.get("properties"), body.get("roomMapProperties"),
                      body.get("mapProperties"), body.get("roomProperties")):
        if isinstance(container, dict):
            for k in _ROOM_PROP_FIELDS:
                if k in container:
                    props[k] = container[k]
    if props:
        from services.db import rooms
        await rooms.update_one({"roomKey": room_key}, {"$set": props}, upsert=True)
    print(f"[maps] saved room properties for {room_key}: {props}")
    return {"success": 1}


async def handle_save_room_map(body: dict) -> dict:
    """
    Save compressed room map data.
    Stores the current version and updates room metadata to trigger client reloads.
    """
    account_id = body.get("accountID", "")
    room_key = body.get("roomKey", "")
    map_data = body.get("mapData", "")
    internal_states = body.get("internalStates", "")
    map_data_version = body.get("mapDataVersion", 2)
    game_session_id = body.get("gameSessionID", "")
    app_version = body.get("applicationVersion", "")

    if not room_key:
        return {"success": 0}
    if room_key.startswith("c_"):          # community worlds are read live from Yeeps, never stored here
        print(f"[cw] map save ignored (live from Yeeps): {room_key} by {account_id}")
        return {"success": 1}

    # Raid protection: regular players can't change official rooms (ack so the
    # client doesn't error, but don't persist).
    # Map makers (developer_mapMaker) / staff — and anyone during testing — may
    # edit them in-game and fall through to the normal save below.
    if _is_protected_room(room_key) and not await _can_edit_protected(account_id, body):
        print(f"[maps] BLOCKED in-game save to protected room {room_key} by {account_id}")
        return {"success": 1}

    if await _is_official_room(room_key):
        print(f"[maps] BLOCKED in-game save to official room {room_key} by {account_id}")
        return {"success": 1}
    if not await _may_save_shared(account_id, room_key, body):
        return {"success": 1}

    timestamp = datetime.now(timezone.utc).strftime("%Y %m/%d %H:%M:%S")
    map_key = f"{room_key}$current"

    # Anti-wipe guard: refuse a save that shrinks a substantial map by >85% in
    # one go (an accidental autosave from a client that loaded a stale/empty map
    # kept nuking restored official rooms). saveData size is a cheap proxy for
    # block count.
    try:
        new_len = len(map_data or "")
        existing = await maps.find_one({"mapKey": map_key}, {"_id": 0, "saveData": 1})
        old_save = ((existing or {}).get("saveData")) or ""
        old_len = len(old_save)
        # Only compare like with like: a 1.31 SPAM save ("U1BBTQ") is far more
        # compact than the 1.19 BinaryFormatter save ("AAEAAAD") of the same map,
        # so a cross-format size drop is not a wipe.
        same_format = old_save[:6] == (map_data or "")[:6]
        # An empty/near-empty save is a wipe whatever the format (Oct 10: raider
        # saves with mapData "" slipped past the same-format check).
        # A player clearing their own private world is a real clear, as long as
        # the save comes from their own bound headset (a script naming the
        # account in the body still can't empty it). map_history below keeps
        # the previous version.
        if old_len > 4000 and (new_len < 200 or (same_format and new_len < old_len * 0.15)) \
                and _is_own_pw(account_id, room_key) and await _from_bound_headset(account_id, body):
            print(f"[maps] owner cleared own pw {room_key}: {old_len} -> {new_len} bytes (bound headset)")
        elif old_len > 4000 and (new_len < 200 or (same_format and new_len < old_len * 0.15)):
            print(f"[maps] BLOCKED shrink-save for {room_key} by {account_id}: "
                  f"{old_len} -> {new_len} bytes (anti-wipe)")
            return {"success": 1}   # ack so the client doesn't error, but don't persist
    except Exception as e:
        print(f"[maps] anti-wipe check failed for {room_key}: {e}")

    # Strip out-of-bounds blocks (a corrupt coord like y=14456 breaks the room's
    # lighting/grid and keeps getting re-saved by clients that have it loaded).
    # Only re-encode when something is actually out of range, to avoid touching
    # normal saves (which would otherwise drop per-object internal states).
    try:
        from services.mapsync_decoder import decode_map
        from services.mapsync_serializer import serialize_blocks_compressed
        # The old decoder/re-encoder does not cover all live79 map fields.
        blocks = [] if IS_BUDDY else decode_map(map_data)
        if blocks:
            good = [b for b in blocks
                    if all(0 <= int(b.get(ax, 0) or 0) < 256 for ax in ("x", "y", "z"))]
            if len(good) != len(blocks):
                print(f"[maps] stripped {len(blocks) - len(good)} out-of-bounds block(s) "
                      f"from {room_key} save by {account_id}")
                map_data = serialize_blocks_compressed(good)
    except Exception as e:
        print(f"[maps] OOB strip failed for {room_key}: {e}")

    # Blocks owned by accounts that aren't in the database (official players in
    # copied maps, bot accounts, "testing") belong to the server owner. Only the
    # owner-ID strings in the blob change (lossless; internal states stay valid).
    try:
        from services.map_owners import default_unknown_owners
        if IS_BUDDY:
            _n_owned = 0
        else:
            map_data, _n_owned = await default_unknown_owners(map_data)
        if _n_owned:
            print(f"[maps] gave {_n_owned} unknown owner id(s) in {room_key} to the default owner")
    except Exception as e:
        print(f"[maps] owner defaulting failed for {room_key}: {e}")

    # Bounded, rate-limited history so a raided map can be reverted. Before the
    # current map is overwritten, snapshot it to map_history — but at most once
    # per room per 10 min, so a bot spamming saves can't flush the good pre-raid
    # version out of the capped (15-deep) history. The first raid save therefore
    # preserves the last good map; the Revert button restores it.
    try:
        import time as _t
        from services.db import db as _db
        _hist = _db["map_history"]
        _prev = await maps.find_one({"mapKey": map_key}, {"_id": 0})
        if _prev and (_prev.get("saveData")):
            _last = await _hist.find_one({"roomKey": room_key}, sort=[("ts", -1)],
                                         projection={"_id": 0, "ts": 1})
            _now = int(_t.time())
            if not _last or _now - int(_last.get("ts", 0) or 0) >= 600:
                await _hist.insert_one({"roomKey": room_key, "ts": _now,
                                        "savedBy": _prev.get("savedBy"),
                                        "savedAt": _prev.get("savedAt"),
                                        "bytes": len(_prev.get("saveData") or ""),
                                        "doc": _prev})
                _olds = [d["_id"] async for d in _hist.find(
                    {"roomKey": room_key}, sort=[("ts", -1)], projection={"_id": 1})]
                for _oid in _olds[15:]:
                    await _hist.delete_one({"_id": _oid})
    except Exception as e:
        print(f"[maps] history snapshot failed for {room_key}: {e}")

    # 1. Update the 'maps' collection (G2_Maps)
    current_doc = {
        "mapKey": map_key,
        "roomKey": room_key,
        "saveData": map_data,
        "saveInternalStates": internal_states,
        "mapDataVersion": map_data_version,
        "savedBy": account_id,
        "gameSessionID": game_session_id,
        "applicationVersion": app_version,
        "savedAt": timestamp,
    }

    # A save from a client that was served the FULL internal states (only
    # possible while every playable build is new enough) supersedes the stored
    # full copy; a save from an older build (served the blank-safe version) must
    # NOT wipe it, or the Starter House signs would be lost.
    _upd = {"$set": current_doc}
    try:
        from services.build_gate import only_guard_builds
        if await only_guard_builds():
            _upd["$unset"] = {"saveInternalStatesFull": ""}
    except Exception:
        pass
    await maps.update_one({"mapKey": map_key}, _upd, upsert=True)

    # 2. Update the 'rooms' collection (G2_Rooms metadata)
    # This is vital so clients know the map has changed
    from services.db import rooms
    await rooms.update_one(
        {"roomKey": room_key},
        {
            "$set": {
                # Must stay a number: a text value (Oct 10, raider saves with no session
                # id stored "") makes the 1.31 client fail "Failed to load game".
                "lastEditedSessionID": int(game_session_id) if str(game_session_id).isdigit() else 1,
                "roomDataVersion": map_data_version
            }
        },
        upsert=False # Only update if room exists
    )

    # (No per-save version-history snapshot: it grew the maps collection without
    # bound on every save — a flood risk for a public server. The current map is
    # kept in-place at roomKey$current above.)

    print(f"[maps] Saved map for {room_key} by {account_id} (version: {map_data_version})")
    return {"success": 1}


async def _fetch_buddy_map_data(body: dict) -> dict:
    room_key = body.get("roomKey", "")
    is_terrain = bool(body.get("isTerrain", 0))
    empty = {"mapDataVersion": 1 if is_terrain else 3, "saveData": ""}
    try:
        offset = int(body.get("saveOffset", 0))
    except (ValueError, TypeError):
        return empty
    if not room_key or offset < 0:
        return empty

    doc = None
    session = str(body.get("targetSessionID", ""))
    if not is_terrain and session.isdecimal():
        if offset and not await maps.find_one(
                {"mapKey": f"{room_key}$map_{session}_save0"}, {"_id": 1}):
            return empty
        doc = await maps.find_one(
            {"mapKey": f"{room_key}$map_{session}_save{offset}"}, {"_id": 0})
    # Only an actual stored chunk can satisfy a later offset. Never repeat an
    # imported/current snapshot for every chunk or mix it into another session.
    if offset and doc is None:
        return empty
    current = None
    if doc is None:
        current = await maps.find_one({"mapKey": f"{room_key}$current"}, {"_id": 0})
    if is_terrain:
        # live79's terrain decoder accepts empty data as a successful null
        # terrain, which selects the theme's procedural preset. SPAM map bytes
        # and the map's version are not terrain data.
        return {"mapDataVersion": (current or {}).get("terrainDataVersion", 1),
                "saveData": (current or {}).get("terrainSaveData", "")}
    if doc is None and current and session.isdecimal() and str(current.get("gameSessionID")) == session:
        doc = current
    if doc is None:
        from services.db import imported_maps
        doc = await imported_maps.find_one({"roomKey": room_key}, {"_id": 0})
    if doc is None:
        doc = current
    if doc is None:
        from services.mapsync_serializer import serialize_blocks_spam
        return {**empty, "saveData": serialize_blocks_spam([]),
                "saveInternalStates": "U1BBTQMAAAAA"}
    return {"mapDataVersion": doc.get("mapDataVersion", 3),
            "saveData": doc.get("saveData", ""),
            "saveInternalStates": doc.get("saveInternalStates", "")}


async def handle_fetch_map_data(body: dict) -> dict:
    """
    Return a room's stored map blob exactly as it was saved -- we echo back the
    same bytes the client itself wrote via saveRoomMapCompressed. The client
    fetches this offset-by-offset (saveOffset 0, 1, 2, ...) and stops once it
    gets an empty saveData back, so everything fits in a single "chunk" at
    offset 0.

    Only 1.19-native blobs are served. A 2.x SPAM blob ("U1BBTQ"...) would be
    rejected by the 1.19 client as INVALID_FORMAT and can brick the room, so we
    serve empty for those instead of handing the client something it can't read.
    """
    if IS_BUDDY:
        return await _fetch_buddy_map_data(body)

    room_key = body.get("roomKey", "")
    save_offset = body.get("saveOffset", 0)
    is_terrain = body.get("isTerrain", 0)

    if not room_key or save_offset:
        # Everything fits in offset 0 (real Yeeps rooms return the whole map in
        # a single chunk here) -- any later offset means "no more data".
        return {"saveData": ""}

    doc = await maps.find_one({"mapKey": f"{room_key}$current"}, {"_id": 0})
    if not doc:
        return {"saveData": ""}

    if is_terrain:
        return {
            "mapDataVersion": doc.get("mapDataVersion", 2),
            "saveData": doc.get("terrainSaveData", ""),
        }

    save_data = doc.get("saveData", "") or ""
    # Don't serve a 2.x SPAM blob to a 1.19 client (INVALID_FORMAT / brick).
    if save_data.startswith("U1BBTQ") and not IS_BUDDY:
        return {"saveData": ""}

    return {
        "mapDataVersion": doc.get("mapDataVersion", 2),
        "saveData": save_data,
        "saveInternalStates": doc.get("saveInternalStates", ""),
    }
