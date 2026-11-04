#!/usr/bin/env python3
"""2.32 server: stop players showing up under their player ID.

Run from your 2.32 repo folder (the one containing server/):

    python apply_name_fix.py

Changes server/handlers/login.py only (backup: login.py.bak_before_namefix; safe to run twice):
  1. The login looks for the player's name in several request fields, not just "oculusID".
  2. If the request has NO usable name, the server log says exactly which fields did arrive
     (field names only, never values), so you can see what the 2.32 client really sends.
  3. An account that was named after its player ID gets its real name the next time the
     client sends one (before, it stayed on the ID forever).
"""
import os
import shutil
import sys

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server", "handlers", "login.py")
MARK = "NAMEFIX-v1"

HELPER = '''# NAMEFIX-v1: where the client might put the player's name (oculusID is the one the kit knew)
_NAME_KEYS = ("oculusID", "oculusId", "oculusUsername", "username", "userName", "displayName",
              "metaUsername", "metaName", "platformUsername", "platformName", "playerName",
              "nickname", "nickName", "name")
_NAME_PLACEHOLDERS = ("buddyplayer", "buddy", "null", "none", "unknown")


def _login_name(body: dict) -> str:
    for k in _NAME_KEYS:
        v = body.get(k)
        if isinstance(v, str) and v.strip() and v.strip().lower() not in _NAME_PLACEHOLDERS:
            return v.strip()
    print(f"[login] no player name in the login request for {body.get('accountID')!r}; "
          f"fields sent: {sorted(str(k) for k in body)}")
    return str(body.get("oculusID", "") or "")


'''

EDITS = [
    ('async def handle_login(body: dict) -> dict:\n    """\n    Process a login request.',
     HELPER + 'async def handle_login(body: dict) -> dict:\n    """\n    Process a login request.'),
    ('    oculus_id = body.get("oculusID", "")\n',
     '    oculus_id = _login_name(body)\n'),
    ('            await accounts.update_one({"accountID": account_id},\n'
     '                                     {"$set": {"displayName": account_id,\n'
     '                                               "displayName_searchable": account_id.lower()}})\n'
     '    account = account_view(account)\n',
     '            await accounts.update_one({"accountID": account_id},\n'
     '                                     {"$set": {"displayName": account_id,\n'
     '                                               "displayName_searchable": account_id.lower()}})\n'
     '        elif (str(account.get("displayName", "")) == account_id and oculus_id\n'
     '              and oculus_id.strip().lower() not in _NAME_PLACEHOLDERS and oculus_id.strip() != account_id):\n'
     '            # NAMEFIX-v1: it was named after its ID because no name arrived; one has now\n'
     '            print(f"[login] {account_id}: name {account_id!r} -> {oculus_id.strip()!r}")\n'
     '            account["displayName"] = oculus_id.strip()\n'
     '            account["displayName_searchable"] = oculus_id.strip().lower()\n'
     '            await accounts.update_one({"accountID": account_id},\n'
     '                                     {"$set": {"displayName": oculus_id.strip(),\n'
     '                                               "displayName_searchable": oculus_id.strip().lower()}})\n'
     '    account = account_view(account)\n'),
]


def main():
    if not os.path.isfile(PATH):
        sys.exit("Run this from your 2.32 repo folder (the one that contains server/).")
    with open(PATH, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    if MARK in text:
        print("Already applied. Nothing to do.")
        return
    nl = "\r\n" if "\r\n" in text else "\n"
    new = text
    for old, rep in EDITS:
        o, r = old.replace("\n", nl), rep.replace("\n", nl)
        if new.count(o) != 1:
            sys.exit("Couldn't find the exact spot to change in login.py:\n  "
                     + old.splitlines()[0][:90] + "\nThis version differs from the one the patch was written for. "
                     "Nothing was changed.")
        new = new.replace(o, r, 1)
    shutil.copy2(PATH, PATH + ".bak_before_namefix")
    with open(PATH, "w", encoding="utf-8", newline="") as f:
        f.write(new)
    print("Done. Commit and push it (Render redeploys), or restart the server.")


if __name__ == "__main__":
    main()
