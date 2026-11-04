#!/usr/bin/env python3
"""Yeeps 2.32 command-line editor.

    python tools\\editor.py

Talks to the server's /editor/* API.  Auth is a single password prompt at
startup (stored in memory for the session).  Tabs are picked by number; file
uploads ask for a path instead of a file picker.
"""
import json
import os
import re
import sys
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_URL = os.getenv("EDITOR_URL", "http://127.0.0.1:8000")
DEFAULT_PW = os.getenv("EDITOR_PASSWORD", "")

BANNER = r"""
  ______  _   _  _____  _____  _____
 |  ____|| \ | ||  __ \|  __ \|  _  |
 | |__   |  \| || |  | | |__) | | | |
 |  __|  | . ` || |  | |  ___/| | | |
 | |____ | |\  || |__| | |    | |_| |
 |______||_| \_||_____/|_|     \___/

        Yeeps 2.32 Editor (CLI)
"""


def ask(prompt, default=None):
    suffix = f" [{default}]" if default not in (None, "") else ""
    v = input(f"{prompt}{suffix}: ").strip()
    if not v and default is not None:
        v = str(default)
    return v


def ask_int(prompt, default=None):
    while True:
        v = ask(prompt, default)
        try:
            return int(v)
        except ValueError:
            print("  enter a number")


def ask_choice(prompt, options):
    print(prompt + ":")
    for i, label in enumerate(options, 1):
        print(f"  {i}. {label}")
    while True:
        v = input("> ").strip()
        if v.isdigit() and 1 <= int(v) <= len(options):
            return int(v) - 1
        print("  pick a number")


class Client:
    def __init__(self, base, password):
        self.base = base.rstrip("/")
        self.password = password
        self.http = httpx.Client(timeout=30.0)

    def _headers(self):
        return {"x-editor-password": self.password}

    def get(self, path, params=None):
        r = self.http.get(self.base + path, params=params, headers=self._headers())
        return self._handle(r)

    def post(self, path, json=None, files=None, data=None):
        r = self.http.post(self.base + path, json=json, files=files, data=data,
                           headers=self._headers())
        return self._handle(r)

    def put(self, path, json=None):
        r = self.http.put(self.base + path, json=json, headers=self._headers())
        return self._handle(r)

    def patch(self, path, json=None):
        r = self.http.patch(self.base + path, json=json, headers=self._headers())
        return self._handle(r)

    def delete(self, path):
        r = self.http.delete(self.base + path, headers=self._headers())
        return self._handle(r)

    def _handle(self, r):
        if r.status_code == 401:
            raise RuntimeError("Invalid editor password")
        if not r.is_success:
            try:
                d = r.json()
                msg = d.get("detail") or d.get("error") or r.text
            except Exception:
                msg = r.text
            raise RuntimeError(f"HTTP {r.status_code}: {msg}")
        if not r.content:
            return None
        try:
            return r.json()
        except Exception:
            return r.text


def pretty(o):
    return json.dumps(o, indent=2, ensure_ascii=False, default=str)


def toast(msg):
    print(f"\n  >> {msg}\n")


def _downloads_dir():
    home = os.path.expanduser("~")
    for sub in ("Downloads", "downloads"):
        p = os.path.join(home, sub)
        if os.path.isdir(p):
            return p
    return None


def _find_map_file():
    dl = _downloads_dir()
    if not dl:
        return None
    for name in ("yeeps_map_blocks.json",):
        p = os.path.join(dl, name)
        if os.path.exists(p):
            return p
    cands = []
    for f in os.listdir(dl):
        if f.lower().endswith(".json") and "map" in f.lower():
            cands.append(os.path.join(dl, f))
    if cands:
        cands.sort(key=lambda x: os.path.getmtime(x), reverse=True)
        return cands[0]
    return None


def parse_dur(s):
    s = s.strip().lower()
    if not s:
        return 0
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(m|h|d|w)?", s)
    if not m:
        return None
    n = float(m.group(1))
    unit = m.group(2) or "h"
    mult = {"m": 1/60, "h": 1, "d": 24, "w": 168}[unit]
    return n * mult


# ---------------- Players ----------------
def players_menu(c):
    while True:
        print("\n-- Players --")
        print("  1. Search players")
        print("  2. Open player by ID")
        print("  3. Back")
        choice = input("> ").strip()
        if choice == "1":
            q = ask("Search (name or account ID)", "")
            try:
                rows = c.get("/editor/players", params={"q": q, "limit": 50})
            except Exception as e:
                toast(str(e)); continue
            if not rows:
                print("  no players found"); continue
            for i, p in enumerate(rows, 1):
                print(f"  {i}. {p.get('displayName') or '(no name)'}  {p.get('accountID')}  "
                      f"{p.get('currency', '?')}c {p.get('beans', '?')}b"
                      f"{'  BANNED' if p.get('adminBlocked') else ''}")
            sel = input("Open number (or Enter to skip): ").strip()
            if sel.isdigit() and 1 <= int(sel) <= len(rows):
                player_detail(c, rows[int(sel) - 1]["accountID"])
        elif choice == "2":
            aid = ask("Account ID")
            if aid:
                player_detail(c, aid)
        elif choice == "3":
            return
        else:
            print("  pick 1-3")


def player_detail(c, aid):
    try:
        p = c.get(f"/editor/players/{aid}")
    except Exception as e:
        toast(str(e)); return
    print("\n" + pretty(p))
    while True:
        print("\n  1. Save changes (edit fields)")
        print("  2. Ban (account only)")
        print("  3. Ban + IP")
        print("  4. Unban")
        print("  5. Unban + IP")
        print("  6. Reset headset lock")
        print("  7. Delete account")
        print("  8. Back")
        ch = input("> ").strip()
        if ch == "1":
            patch = {}
            for field in ("displayName", "currency", "beans", "anonymous_displayName"):
                v = ask(f"  {field} (Enter to skip)", "")
                if v == "":
                    continue
                if field in ("currency", "beans"):
                    try:
                        patch[field] = int(v)
                    except ValueError:
                        print("  must be a number"); continue
                else:
                    patch[field] = v
            for field in ("roleKeys", "activeCosmetics", "ownedCosmetics"):
                v = ask(f"  {field} JSON (Enter to skip)", "")
                if v:
                    try:
                        patch[field] = json.loads(v)
                    except json.JSONDecodeError as e:
                        print(f"  bad JSON: {e}")
            if not patch:
                print("  nothing to save"); continue
            try:
                c.patch(f"/editor/players/{aid}", json=patch)
                toast("Saved")
                p = c.get(f"/editor/players/{aid}")
                print(pretty(p))
            except Exception as e:
                toast(str(e))
        elif ch in ("2", "3"):
            also_ip = (ch == "3")
            dur = ask("Ban length (e.g. 2h, 3d, 7d, 1w) or blank for permanent", "")
            hours = None
            if dur.strip():
                h = parse_dur(dur.strip())
                if h is None:
                    toast("Bad length"); continue
                hours = h
            body = {"alsoIP": also_ip}
            if hours is not None:
                body["durationHours"] = hours
            try:
                r = c.post(f"/editor/players/{aid}/ban", json=body)
                toast("Banned: " + pretty(r))
                p = c.get(f"/editor/players/{aid}")
            except Exception as e:
                toast(str(e))
        elif ch in ("4", "5"):
            also_ip = (ch == "5")
            try:
                r = c.post(f"/editor/players/{aid}/unban", json={"alsoIP": also_ip})
                toast("Unbanned: " + pretty(r))
                p = c.get(f"/editor/players/{aid}")
            except Exception as e:
                toast(str(e))
        elif ch == "6":
            if not ask("Reset headset lock? (y/n)", "n").lower().startswith("y"):
                continue
            try:
                c.post(f"/editor/players/{aid}/device-reset")
                toast("Headset lock reset")
            except Exception as e:
                toast(str(e))
        elif ch == "7":
            if ask(f"Type DELETE to permanently delete {aid}", "") != "DELETE":
                continue
            try:
                c.delete(f"/editor/players/{aid}")
                toast("Deleted")
                return
            except Exception as e:
                toast(str(e))
        elif ch == "8":
            return
        else:
            print("  pick 1-8")


# ---------------- Maps ----------------
def maps_menu(c):
    while True:
        print("\n-- Maps --")
        print("  1. List maps")
        print("  2. Open/edit map")
        print("  3. Import map from file")
        print("  4. Import mapkeys (auto-find yeeps_map_blocks.json in Downloads)")
        print("  5. Reset/delete map")
        print("  6. Back")
        ch = input("> ").strip()
        if ch == "1":
            try:
                rows = c.get("/editor/maps")
            except Exception as e:
                toast(str(e)); continue
            if not rows:
                print("  no maps"); continue
            for m in rows:
                print(f"  {m.get('roomKey')}  ({m.get('source')})  blocks={m.get('blockCount')}")
        elif ch == "2":
            rk = ask("Room key")
            if not rk:
                continue
            try:
                doc = c.get(f"/editor/maps/{rk}")
            except Exception as e:
                toast(str(e)); continue
            print(pretty(doc))
            if ask("Edit JSON? (y/n)", "n").lower().startswith("y"):
                path = ask("Path to JSON file with new map doc")
                if path and os.path.exists(path):
                    try:
                        data = json.load(open(path, encoding="utf-8"))
                        c.put(f"/editor/maps/{rk}", json=data)
                        toast("Saved")
                    except Exception as e:
                        toast(str(e))
        elif ch == "3":
            rk = ask("Room key to import into")
            path = ask("Path to map JSON file")
            if not rk or not path or not os.path.exists(path):
                toast("room key and existing file required"); continue
            try:
                data = json.load(open(path, encoding="utf-8"))
                c.put(f"/editor/maps/{rk}", json=data)
                toast("Imported")
            except Exception as e:
                toast(str(e))
        elif ch == "4":
            auto = _find_map_file()
            if auto:
                print(f"  auto-found: {auto}")
                path = ask("Map file path", auto)
            else:
                print("  no yeeps_map_blocks.json found in Downloads")
                path = ask("Map file path")
            if not path or not os.path.exists(path):
                toast("file not found"); continue
            try:
                data = json.load(open(path, encoding="utf-8"))
            except Exception as e:
                toast(f"bad JSON: {e}"); continue
            if isinstance(data, dict) and data.get("roomKey"):
                rk = data.get("roomKey")
                print(f"  roomKey from file: {rk}")
            else:
                rk = ask("Room key to import into")
            if not rk:
                toast("no room key"); continue
            try:
                c.put(f"/editor/maps/{rk}", json=data)
                bc = data.get("blockCount") if isinstance(data, dict) else None
                toast(f"Imported {bc if bc is not None else '?'} blocks into {rk}")
            except Exception as e:
                toast(str(e))
        elif ch == "5":
            rk = ask("Room key to reset")
            if not rk:
                continue
            if ask(f"Type RESET to delete/reset map {rk}", "") != "RESET":
                continue
            try:
                c.delete(f"/editor/maps/{rk}")
                toast("Reset")
            except Exception as e:
                toast(str(e))
        elif ch == "6":
            return
        else:
            print("  pick 1-6")


# ---------------- Announcements ----------------
def announcements_menu(c):
    while True:
        print("\n-- Announcements --")
        print("  1. List")
        print("  2. Create")
        print("  3. Edit")
        print("  4. Delete")
        print("  5. Upload video (paste path)")
        print("  6. Back")
        ch = input("> ").strip()
        if ch == "1":
            try:
                rows = c.get("/editor/announcements")
            except Exception as e:
                toast(str(e)); continue
            if not rows:
                print("  none"); continue
            for a in rows:
                print(f"  {a.get('id')}  {a.get('title')}  ({a.get('type')})  "
                      f"{'enabled' if a.get('enabled') else 'disabled'}")
        elif ch == "2":
            create_announcement(c)
        elif ch == "3":
            aid = ask("Announcement ID")
            if aid:
                edit_announcement(c, aid)
        elif ch == "4":
            aid = ask("Announcement ID")
            if not aid:
                continue
            if ask("Type DELETE to confirm", "") != "DELETE":
                continue
            try:
                c.delete(f"/editor/announcements/{aid}")
                toast("Deleted")
            except Exception as e:
                toast(str(e))
        elif ch == "5":
            path = ask("Path to video file")
            if not path or not os.path.exists(path):
                toast("file not found"); continue
            try:
                with open(path, "rb") as fh:
                    files = {"file": (os.path.basename(path), fh, "video/mp4")}
                    r = c.post("/editor/announcements/upload-video", files=files)
                toast("Uploaded: " + r.get("videoUrl", ""))
                print(pretty(r))
            except Exception as e:
                toast(str(e))
        elif ch == "6":
            return
        else:
            print("  pick 1-6")


def create_announcement(c):
    title = ask("Title")
    if not title:
        return
    atype = ask_choice("Type", ["text", "video"])
    msg = ask("Message/description", "")
    video = ask("Video URL (optional)", "")
    priority = ask_int("Priority (-1000..1000)", 0)
    enabled = ask("Enabled? (y/n)", "y").lower().startswith("y")
    payload = {"type": atype, "title": title, "message": msg,
               "videoUrl": video, "priority": priority, "enabled": enabled}
    try:
        r = c.post("/editor/announcements", json=payload)
        toast("Created")
        print(pretty(r))
    except Exception as e:
        toast(str(e))


def edit_announcement(c, aid):
    try:
        rows = c.get("/editor/announcements")
    except Exception as e:
        toast(str(e)); return
    a = next((x for x in rows if x.get("id") == aid), None)
    if a is None:
        toast("not found"); return
    print(pretty(a))
    title = ask("Title", a.get("title", ""))
    atype = ask("Type", a.get("type", "text"))
    msg = ask("Message", a.get("message", ""))
    video = ask("Video URL", a.get("videoUrl", ""))
    priority = ask_int("Priority", a.get("priority", 0))
    enabled = ask("Enabled? (y/n)", "y" if a.get("enabled") else "n").lower().startswith("y")
    expires = ask("Expiry (ISO or blank)", a.get("expiresAt", ""))
    payload = {"type": atype, "title": title, "message": msg,
               "videoUrl": video, "priority": priority, "enabled": enabled,
               "expiresAt": expires}
    if ask("Save? (y/n)", "y").lower().startswith("y"):
        try:
            c.put(f"/editor/announcements/{aid}", json=payload)
            toast("Saved")
        except Exception as e:
            toast(str(e))


# ---------------- main ----------------
def main():
    print(BANNER)
    url = ask("Server URL", DEFAULT_URL)
    pw = ask("Editor password", DEFAULT_PW)
    c = Client(url, pw)
    try:
        me = c.get("/editor/me")
        print(f"  signed in as: {me.get('name', '?')}  (db: {me.get('db', '?')})")
    except Exception as e:
        print(f"  login failed: {e}")
        if not ask("Continue anyway? (y/n)", "n").lower().startswith("y"):
            return

    while True:
        print("\n=== Editor ===")
        print("  1. Players")
        print("  2. Maps")
        print("  3. Announcements")
        print("  4. Quit")
        ch = input("> ").strip()
        if ch == "1":
            players_menu(c)
        elif ch == "2":
            maps_menu(c)
        elif ch == "3":
            announcements_menu(c)
        elif ch == "4":
            print("bye")
            return
        else:
            print("  pick 1-4")


if __name__ == "__main__":
    main()