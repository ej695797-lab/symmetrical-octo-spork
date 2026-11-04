"""Trading card + stash handlers ported from server-live/vr-server.

Simplified for the 2.32 self-hosted server: no live-Yeeps read-through
(yeeps_direct not available). Cards are served from a local catalog file.
"""
from datetime import datetime, timezone

from services.db import accounts

STASH_AMOUNT = 100
CARD_PACK_SIZE = 100
GOLDEN_CHANCE = 0.10
LEGENDARY_SUFFIX = "_legendary"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y %m/%d %H:%M:%S")


async def _add(acct: str, field: str, amount: int) -> int:
    if not acct:
        return amount
    from services.db import accounts as _a
    doc = await _a.find_one_and_update(
        {"accountID": acct}, {"$inc": {field: int(amount)}},
        projection={"_id": 0, field: 1}, return_document=True)
    try:
        return int((doc or {}).get(field) or amount)
    except Exception:
        return amount


def _reward(kind: str, key: str, amount: int) -> dict:
    return {"type": kind, "rewardType": kind, "key": key, "itemKey": key,
            "amount": amount, "value": amount, "count": amount, "quantity": amount,
            "currencyAmount": amount if kind == "currency" else 0}


_DAILY_FALLBACK_ITEMKEYS = {}


async def _daily_challenges() -> dict:
    return {"itemKeys": _DAILY_FALLBACK_ITEMKEYS, "startTime": _now(),
            "expireTime": _now(), "source": "fallback"}


async def _challenge_value(pool: str, item_key: str) -> int:
    return 0


async def _capture(fn: str, body: dict, reply: dict) -> None:
    try:
        from services.db import db as _db
        clean = {k: v for k, v in body.items() if not str(k).startswith("_")}
        await _db["card_capture"].insert_one({"at": _now(), "kind": "claim", "fn": fn,
                                             "body": clean, "reply": reply})
    except Exception:
        pass


async def handle_redeem_challenge_reward(body: dict) -> dict:
    acct = str(body.get("accountID") or "")
    pool = str(body.get("poolKey") or "")
    item = str(body.get("itemKey") or "")
    amount = await _challenge_value(pool, item) or 50
    new_cur = await _add(acct, "currency", amount)
    if acct:
        await accounts.update_one({"accountID": acct},
                                  {"$set": {"lastChallengeRedeemedTime": _now()},
                                   "$addToSet": {"redeemedChallengeKeys": f"{pool}:{item}"}})
    reply = {"success": 1, "reward": _reward("currency", "currency", amount),
             "currencyAmount": amount, "newCurrency": new_cur,
             "items": [], "newLastChallengeRedeemedTime": _now()}
    await _capture("redeemChallengeReward", body, reply)
    return reply


async def handle_redeem_trading_card_reward(body: dict) -> dict:
    acct = str(body.get("accountID") or "")
    item = str(body.get("itemKey") or "")
    if acct:
        await accounts.update_one({"accountID": acct},
                                  {"$set": {"lastCardChallengeRedeemedTime": _now()},
                                   "$addToSet": {"redeemedCardChallengeKeys": item}})
    reply = {"success": 1, "reward": "tradingCardPack",
             "newLastCardChallengeRedeemedTime": _now()}
    await _capture("redeemTradingCardReward", body, reply)
    return reply


async def handle_try_redeem_currency_stash(body: dict) -> dict:
    acct = str(body.get("accountID") or "")
    stash = str(body.get("stashKey") or "")
    amount = 100
    new_cur = await _add(acct, "currency", amount)
    if acct and stash:
        await accounts.update_one({"accountID": acct},
                                  {"$addToSet": {"redeemedStashIDs": f"{body.get('roomKey', '')}:{stash}"}})
    reply = {"success": 1, "reward": _reward("currency", "currency", amount),
             "currencyAmount": amount, "newCurrency": new_cur}
    await _capture("tryRedeemCurrencyStash", body, reply)
    return reply


async def card_catalog() -> list:
    try:
        from services.db import globals_col
        doc = await globals_col.find_one({"key": "trading_card_keys"}, {"_id": 0, "value": 1})
        v = (doc or {}).get("value")
        if isinstance(v, list) and v:
            return [str(x) for x in v]
    except Exception:
        pass
    try:
        import json as _j, os as _o
        f = _o.path.join(_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))), "data", "trading_card_keys.json")
        keys = _j.load(open(f))
        if keys:
            return [str(k) for k in keys]
    except Exception:
        pass
    return []


async def handle_try_purchase_trading_card_pack(body: dict) -> dict:
    import random
    acct = str(body.get("accountID") or "")
    is_beans = bool(int(body.get("isBeans") or 0))
    price = max(0, int(body.get("expectedPrice") or 0))
    field = "beans" if is_beans else "currency"
    new_bal = await _add(acct, field, -price)
    cat = await card_catalog()
    size = 100
    try:
        from services.db import globals_col as _gc
        _ps = await _gc.find_one({"key": "card_pack_size"}, {"_id": 0, "data": 1})
        if _ps and _ps.get("data") is not None:
            size = max(1, int(_ps["data"]))
    except Exception:
        pass
    cards = random.sample(cat, size) if cat and len(cat) >= size else ([random.choice(cat) for _ in range(size)] if cat else [])
    cards = [c + LEGENDARY_SUFFIX if random.random() < GOLDEN_CHANCE else c for c in cards]
    if acct:
        upd = {"$inc": {"beanPurchasedCardCount": 1}} if is_beans else {}
        upd.setdefault("$set", {})["lastCardBeanPurchaseTime" if is_beans else "lastCardPurchaseTime"] = _now()
        await accounts.update_one({"accountID": acct}, upd)
    reply = {"success": 1, "cost": price, "currencyValueIndex": 1 if is_beans else 0,
             "newCurrency": new_bal, "newCardKeys": cards}
    await _capture("tryPurchaseTradingCardPack", body, reply)
    print(f"[cards] {acct} bought a card pack -> {len(cards)} cards")
    return reply