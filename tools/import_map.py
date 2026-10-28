#!/usr/bin/env python3
"""Put a map into one of your server's rooms.

    python tools/import_map.py --room official_1 map.json
    python tools/import_map.py --room official_1 map.json --dry-run

map.json is a room's block list from YeepsMapLoader: the relay's /map reply
{ok, roomKey, dimensions, blocks:[{name,x,y,z,fwd,up,color,owner}, ...]}, or just
the blocks array. Blocks that don't exist in 1.31 or sit outside a room are left
out. The server reads the same database (server/.env), so it can keep running:
players see the new map the next time they load the room.
"""
import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("map", help="the map JSON file")
    ap.add_argument("--room", help="room key, e.g. official_1 (default: the roomKey in the file)")
    ap.add_argument("--dry-run", action="store_true", help="only report what would be imported")
    a = ap.parse_args()

    from services.db import globals_col, imported_maps, rooms
    from services.mapsync_serializer import extract_blocks, serialize_blocks_spam
    from services.role_locks import DISABLED_PATTERNS

    raw = json.load(open(a.map, encoding="utf-8"))
    room_key = (a.room or (raw.get("roomKey") if isinstance(raw, dict) else "") or "").strip()
    if not room_key:
        print("Give the room: --room <roomKey> (e.g. official_1)")
        return 1
    if room_key.startswith("c_"):
        print("Community worlds aren't part of this server.")
        return 1
    room = await rooms.find_one({"roomKey": room_key}, {"_id": 0, "roomName": 1})
    if not room:
        print(f"Warning: there's no room {room_key!r} in the database (check the key).")

    blocks = extract_blocks(raw)
    total = len(blocks)
    # Only blocks the 1.31 game has (globals valid_mapobject_keys, from seed/world).
    ov = await globals_col.find_one({"key": "valid_mapobject_keys"}, {"_id": 0, "value": 1})
    allow = set(ov["value"]) if ov and isinstance(ov.get("value"), list) else None
    unknown = set()
    kept = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        name = str(b.get("name") or b.get("key") or "")
        if (allow is not None and name not in allow) or name in DISABLED_PATTERNS:
            unknown.add(name)
            continue
        try:
            # A corrupt coordinate outside every room's grid breaks the whole room.
            if not all(0 <= int(b.get(ax, 0)) < 256 for ax in ("x", "y", "z")):
                continue
        except (TypeError, ValueError):
            continue
        kept.append(b)
    try:
        from services.map_owners import default_block_owners
        await default_block_owners(kept)          # blocks of unknown players -> DEFAULT_MAP_OWNER
    except Exception as e:
        print(f"(owner check skipped: {e})")

    print(f"{room_key} ({(room or {}).get('roomName') or '?'}): {len(kept)} of {total} blocks"
          + (f"; left out {len(unknown)} kinds 1.31 doesn't have, e.g. {', '.join(sorted(unknown)[:8])}"
             if unknown else ""))
    if a.dry_run:
        return 0
    doc = {
        "roomKey": room_key,
        "blocks": kept,
        "blockCount": len(kept),
        "dimensions": raw.get("dimensions") if isinstance(raw, dict) else None,
        "saveData": serialize_blocks_spam(kept),
        "importedAt": datetime.now(timezone.utc).strftime("%Y %m/%d %H:%M:%S"),
    }
    await imported_maps.update_one({"roomKey": room_key}, {"$set": doc}, upsert=True)
    # A new edit session: the game asks for this session's map, finds no in-game
    # save for it, and gets the imported map. Must stay an int (a string here makes
    # the game fail to load).
    await rooms.update_one({"roomKey": room_key}, {"$set": {"lastEditedSessionID": int(time.time())}})
    print("Imported. Players see it the next time they load that room.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
