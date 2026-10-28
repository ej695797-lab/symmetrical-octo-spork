"""Load the 1.31.0-blaze world (seed/world/*.json) into the database.

Runs at startup (SEED_WORLD=1, the default). It only ADDS what's missing, never
overwrites: rooms/maps/room maps/bundles are inserted when their key isn't in the
database yet, globals when their key is missing. So a host's own edits survive
restarts, and deleting a room's document brings back the original on the next start.

    python -m seed.load_world          # run it by hand (from the server folder)
"""
import json
from pathlib import Path

from services.db import rooms, room_maps, maps, globals_col, bundles
from services.client_profile import seed_document

WORLD = Path(__file__).parent / "world"

# collection, file, unique key
_SETS = [
    (room_maps, "room_maps.json", "versionKey"),
    (rooms, "rooms.json", "roomKey"),
    (maps, "maps.json", "mapKey"),
    (bundles, "bundles.json", "bundleKey"),
    (globals_col, "globals.json", "key"),
]


async def load_world() -> dict:
    added = {}
    for col, fname, key in _SETS:
        path = WORLD / fname
        if not path.exists():
            continue
        docs = json.loads(path.read_text(encoding="utf-8"))
        n = 0
        for doc in docs:
            doc = seed_document(fname.split(".")[0], doc)
            if not doc.get(key):
                continue
            res = await col.update_one({key: doc[key]}, {"$setOnInsert": doc}, upsert=True)
            n += 1 if res.upserted_id is not None else 0
        added[fname.split(".")[0]] = n
    from config import IS_BUDDY
    if IS_BUDDY:
        from services.client_profile import block_catalog
        from services.role_locks import ROLE_LOCKED_PATTERNS
        await globals_col.update_one({"key": "techweb_items"}, {"$setOnInsert": {
            "key": "techweb_items", "value": [k for k in block_catalog() if k not in ROLE_LOCKED_PATTERNS]}}, upsert=True)
    print(f"[seed] world loaded (new documents): {added}")
    return added


if __name__ == "__main__":
    import asyncio
    asyncio.run(load_world())
