
import random
import uuid
from datetime import datetime, timezone

from services.db import accounts

FISH = [
    {"id": "dobber", "name": "Dobber", "biome": "any", "beanValue": 40, "colors": 10},
    {"id": "nibblet", "name": "Nibblet", "biome": "any", "beanValue": 40, "colors": 10},
    {"id": "clownfish", "name": "Clownfish", "biome": "any", "beanValue": None, "colors": 10},
    {"id": "soft_belly_snapper", "name": "Soft-Belly Snapper", "biome": "any", "beanValue": 80, "colors": 10},
    {"id": "thickjaw_perch", "name": "Thickjaw Perch", "biome": "any", "beanValue": 80, "colors": 10},
    {"id": "mub", "name": "Mub", "biome": "any", "beanValue": None, "colors": 10},
    {"id": "pufferfish", "name": "Pufferfish", "biome": "any", "beanValue": 200, "colors": 10},
    {"id": "fivehead_flounder", "name": "Fivehead Flounder", "biome": "any", "beanValue": 200, "colors": 10},
    {"id": "ribbon_longback", "name": "Ribbon Longback", "biome": "any", "beanValue": 200, "colors": 10},
    {"id": "billyfish", "name": "Billyfish", "biome": "suburbs", "beanValue": 320, "colors": 6},
    {"id": "ice_fish", "name": "Ice Fish", "biome": "snow", "beanValue": 320, "colors": 6},
    {"id": "spearfin", "name": "Spearfin", "biome": "ocean", "beanValue": 320, "colors": 6},
    {"id": "metalhead", "name": "Metalhead", "biome": "facility", "beanValue": 320, "colors": 6},
    {"id": "wispfish", "name": "Wispfish", "biome": "fantasy", "beanValue": 320, "colors": 6},
    {"id": "droughtfish", "name": "Droughtfish", "biome": "desert", "beanValue": 320, "colors": 6},
    {"id": "glorp", "name": "Glorp", "biome": "space", "beanValue": None, "colors": None},
    {"id": "dapper_whale", "name": "Dapper Whale", "biome": "any", "beanValue": None, "colors": None},
    {"id": "legendary_sea_dragon", "name": "Legendary Sea Dragon", "biome": "any", "beanValue": 4000, "colors": 3},
]

BIOME_ALIASES = {
    "suburb": "suburbs",
    "suburbs": "suburbs",
    "snow": "snow",
    "ocean": "ocean",
    "research": "facility",
    "facility": "facility",
    "fantasy": "fantasy",
    "desert": "desert",
    "space": "space",
}

def get_biome(body):
    raw = str(body.get("biome") or body.get("biomeName") or "any").lower()
    return BIOME_ALIASES.get(raw, raw)

async def handle_generate_fish_bite(body: dict) -> dict:
    account_id = str(body.get("accountID") or "").strip()
    if not account_id:
        return {"success": 0, "error": "MISSING_ACCOUNT_ID"}

    account = await accounts.find_one(
        {"accountID": account_id},
        {"_id": 0, "accountID": 1}
    )
    if not account:
        return {"success": 0, "error": "ACCOUNT_NOT_FOUND"}

    biome = get_biome(body)
    available = [
        fish for fish in FISH
        if fish["biome"] == "any" or fish["biome"] == biome
    ]

    if not available:
        available = [fish for fish in FISH if fish["biome"] == "any"]

    ordinary = [fish for fish in available if fish["id"] != "legendary_sea_dragon"]
    legendary = next(
        (fish for fish in available if fish["id"] == "legendary_sea_dragon"),
        None
    )

    if legendary and random.random() < 0.005:
        chosen = legendary
        color_index = random.randrange(3)
    else:
        chosen = random.choice(ordinary)
        color_index = (
            random.randrange(chosen["colors"])
            if chosen["colors"] else 0
        )

    caught_at = datetime.now(timezone.utc).isoformat()
    catch = {
        "catchID": uuid.uuid4().hex,
        "fishID": chosen["id"],
        "fishName": chosen["name"],
        "biome": biome,
        "colorIndex": color_index,
        "caughtAt": caught_at,
    }

    await accounts.update_one(
        {"accountID": account_id},
        {
            "$push": {
                "fishBucket": catch,
                "fishCatalog": catch,
            },
            "$inc": {
                f"fishBucketCounts.{chosen['id']}": 1,
                f"fishCatalogCounts.{chosen['id']}": 1,
                "totalFishCaught": 1,
            },
        }
    )

    return {
        "success": 1,
        "fish": catch,
        "fishBucket": "saved",
        "totalFishCaught": 1,
    }
