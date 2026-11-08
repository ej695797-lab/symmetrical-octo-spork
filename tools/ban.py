"""Yeeps ban tool (CLI). Bans/unbans accounts and IPs directly in MongoDB.

    python tools\\ban.py

Usage:
    python tools\\ban.py ban <username_or_account_id> [duration_hours]
    python tools\\ban.py unban <username_or_account_id>
    python tools\\ban.py unban-ip <ip_address>
    python tools\\ban.py list
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
from config import MONGO_URI, DB_NAME
import pymongo

def get_db():
    return pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000)[DB_NAME]

def find_account(db, name_or_id):
    if name_or_id.startswith("o_"):
        return db.accounts.find_one({"accountID": name_or_id}, {"_id": 0})
    return db.accounts.find_one({"$or": [
        {"displayName": {"$regex": f"^{name_or_id}$", "$options": "i"}},
        {"accountID": name_or_id},
        {"oculusID": name_or_id},
    ]}, {"_id": 0})

def cmd_ban(args):
    if len(args) < 1:
        print("Usage: ban <username_or_account_id> [duration_hours]")
        return
    db = get_db()
    target = args[0]
    dur = float(args[1]) if len(args) > 1 else 0
    acc = find_account(db, target)
    if not acc:
        print(f"Account not found: {target}")
        return
    aid = acc["accountID"]
    from datetime import datetime, timedelta, timezone
    if dur > 0:
        expire = (datetime.now(timezone.utc) + timedelta(hours=dur)).strftime("%Y %m/%d %H:%M:%S")
        db.accounts.update_one({"accountID": aid}, {"$set": {
            "adminBlocked": 1, "isPermabanned": 0, "banReason": f"Banned via CLI ({dur}h)",
            "banExpireTimestamp": expire, "shouldForceRefresh": 1}})
        print(f"Banned {aid} ({acc.get('displayName','')}) for {dur}h (expires {expire})")
    else:
        expire = ""
        db.accounts.update_one({"accountID": aid}, {"$set": {
            "adminBlocked": 1, "isPermabanned": 1, "banReason": "Banned via CLI (permanent)",
            "banExpireTimestamp": "", "shouldForceRefresh": 1}})
        # IP ban
        ip = acc.get("lastIP", "")
        if ip:
            db.globals.update_one({"key": "banned_ips"}, {"$addToSet": {"ips": ip}}, upsert=True)
            print(f"  IP banned: {ip}")
        print(f"Permanently banned {aid} ({acc.get('displayName','')})")

def cmd_unban(args):
    if len(args) < 1:
        print("Usage: unban <username_or_account_id>")
        return
    db = get_db()
    target = args[0]
    acc = find_account(db, target)
    if not acc:
        print(f"Account not found: {target}")
        return
    aid = acc["accountID"]
    db.accounts.update_one({"accountID": aid}, {"$set": {
        "adminBlocked": 0, "isPermabanned": 0, "isMutebanned": 0,
        "banReason": "", "blockReason": "", "banExpireTimestamp": "", "shouldForceRefresh": 1}})
    ip = acc.get("lastIP", "")
    if ip:
        db.globals.update_one({"key": "banned_ips"}, {"$pull": {"ips": ip}})
        print(f"  IP unbanned: {ip}")
    print(f"Unbanned {aid} ({acc.get('displayName','')})")

def cmd_unban_ip(args):
    if len(args) < 1:
        print("Usage: unban-ip <ip_address>")
        return
    db = get_db()
    ip = args[0]
    db.globals.update_one({"key": "banned_ips"}, {"$pull": {"ips": ip}})
    print(f"Unbanned IP: {ip}")

def cmd_list(args):
    db = get_db()
    bans = db.globals.find_one({"key": "banned_ips"}, {"_id": 0})
    print("Banned IPs:", bans.get("ips", []) if bans else [])
    print("\nBanned accounts:")
    for a in db.accounts.find({"adminBlocked": 1}, {"_id": 0, "accountID": 1, "displayName": 1, "banReason": 1}):
        print(f"  {a.get('accountID')} ({a.get('displayName','')}) - {a.get('banReason','')}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    cmd = sys.argv[1]
    args = sys.argv[2:]
    {"ban": cmd_ban, "unban": cmd_unban, "unban-ip": cmd_unban_ip,
     "list": cmd_list}.get(cmd, lambda a: print(f"Unknown command: {cmd}"))(args)
"