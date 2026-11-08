#!/usr/bin/env python3
"""Yeeps photon bot controller (CLI).

    python tools\\bot.py

Controls the photon bot via the server's /api/photon/* API endpoints.
Requires a running server with BOT_KEY set and the bot.py file available.

Number-based menu: pick a target (username or player ID), then type numbers
to perform actions on the bot.
"""
import json
import os
import sys
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_URL = os.getenv("EDITOR_URL", "http://127.0.0.1:8000")
DEFAULT_PW = os.getenv("EDITOR_PASSWORD", "301212")

BANNER = r"""
  ____   ___  _____  _   _  ____  _____
 |  _ \ / _ \|_   _|| | | ||  _ \|_   _|
 | |_) | | | | | |  | |_| || |_) | | |
 |  _ <| |_| | | |  |  _  ||  __/  | |
 | |_) |\___/  |_|  |_| |_||_|     |_|
        Photon Bot Controller
"""

BOT_COMMANDS = [
    ("1", "Status", None),
    ("2", "Start bot (join target's room)", "start"),
    ("3", "Stop bot", "stop"),
    ("4", "Lock onto target", "lock"),
    ("5", "Give powers (fly/speed/god)", "give"),
    ("6", "Fling target", "fling"),
    ("7", "Mimic target", "mimic"),
    ("8", "Mirror target", "mirror"),
    ("9", "Look like target", "look"),
    ("10", "Outline target", "outline"),
    ("11", "Mirrorbuild target", "mirrorbuild"),
    ("12", "Rain", "rain"),
    ("13", "Lightning", "lightning"),
    ("14", "Freeze", "freeze"),
    ("15", "Scare", "scare"),
    ("16", "Jumpscare", "jumpscare"),
    ("17", "Godhand", "godhand"),
    ("18", "Bigbuild", "bigbuild"),
    ("19", "Blackhole", "blackhole"),
    ("20", "Meteors", "meteors"),
    ("21", "Onlyup", "onlyup"),
    ("22", "Pelletfx", "pelletfx"),
    ("23", "Boom", "boom"),
    ("24", "Coins", "coins"),
    ("25", "Screen", "screen"),
    ("26", "Tetris", "tetris"),
    ("27", "Snake", "snake"),
    ("28", "Pong", "pong"),
    ("29", "Boss", "boss"),
    ("30", "Waves", "waves"),
    ("31", "Map", "map"),
    ("32", "Potato", "potato"),
    ("33", "Koth", "koth"),
    ("34", "Canvas", "canvas"),
    ("35", "Apple", "apple"),
    ("36", "Cleanup", "cleanup"),
    ("37", "Talk", "talk"),
    ("38", "Applesign", "applesign"),
    ("39", "Block", "block"),
    ("40", "Boss pause", "boss pause"),
    ("41", "Boss resume", "boss resume"),
    ("42", "Thaw", "thaw"),
    ("43", "Players list", "players"),
    ("44", "Send custom command", "custom"),
    ("45", "View log", "log"),
    ("46", "Quit", "quit"),
]


def ask(prompt, default=None):
    suffix = f" [{default}]" if default not in (None, "") else ""
    v = input(f"{prompt}{suffix}: ").strip()
    if not v and default is not None:
        v = str(default)
    return v


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

    def post(self, path, json=None):
        r = self.http.post(self.base + path, json=json, headers=self._headers())
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


def cmd(c, command):
    return c.post("/api/photon/cmd", json={"cmd": command})


def main():
    print(BANNER)
    url = ask("Server URL", DEFAULT_URL)
    pw = ask("Editor password", DEFAULT_PW)
    c = Client(url, pw)

    # Target selection
    print("\n-- Target --")
    print("  1. Username")
    print("  2. Player ID")
    choice = input("> ").strip()
    if choice == "1":
        target_name = ask("Username")
        target_id = None
    elif choice == "2":
        target_id = ask("Player ID (o_...)")
        target_name = None
    else:
        print("  pick 1 or 2")
        return

    target = target_id if target_id else target_name
    print(f"  Target: {target}")

    while True:
        print("\n=== Photon Bot ===")
        for num, label, _ in BOT_COMMANDS:
            print(f"  {num:>2}. {label}")
        ch = input("> ").strip()

        if ch == "46":
            print("bye")
            return
        elif ch == "1":
            try:
                st = c.get("/api/photon/status")
                print(pretty(st))
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "2":
            try:
                r = c.post("/api/photon/start", json={"target": target, "stay": False})
                print("  started:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "3":
            try:
                r = c.post("/api/photon/stop")
                print("  stopped:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "4":
            try:
                r = cmd(c, f"lock {target}")
                print("  locked onto:", target, r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "5":
            power = ask("Power to give (e.g., fly, speed, god)", "fly")
            try:
                r = cmd(c, f"give {target} {power}")
                print("  gave", power, "to", target, r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "6":
            try:
                r = cmd(c, f"fling {target}")
                print("  flung", target, r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "7":
            try:
                r = c.post("/api/photon/mimic", json={"target": target})
                print("  mimic:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "8":
            try:
                r = cmd(c, f"mirror {target}")
                print("  mirror:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "9":
            try:
                r = cmd(c, f"look {target}")
                print("  look:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "10":
            try:
                r = cmd(c, f"outline {target}")
                print("  outline:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "11":
            try:
                r = cmd(c, f"mirrorbuild {target}")
                print("  mirrorbuild:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "12":
            sub = ask("rain [off/clean/200 10]?", "rain")
            try:
                r = cmd(c, f"rain {target} {sub}".strip())
                print("  rain:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "13":
            try:
                r = cmd(c, f"lightning {target}")
                print("  lightning:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "14":
            try:
                r = cmd(c, f"freeze {target}")
                print("  freeze:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "15":
            try:
                r = cmd(c, f"scare {target}")
                print("  scare:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "16":
            try:
                r = cmd(c, f"jumpscare {target}")
                print("  jumpscare:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "17":
            try:
                r = cmd(c, f"godhand {target}")
                print("  godhand:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "18":
            try:
                r = cmd(c, f"bigbuild {target}")
                print("  bigbuild:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "19":
            try:
                r = cmd(c, f"blackhole {target}")
                print("  blackhole:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "20":
            n = ask("Number of meteors", "14")
            try:
                r = cmd(c, f"meteors {target} {n}")
                print("  meteors:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "21":
            sub = ask("simple?", "")
            cmd_str = f"onlyup {sub} {target}".strip() if sub else f"onlyup {target}"
            try:
                r = cmd(c, cmd_str)
                print("  onlyup:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "22":
            try:
                r = cmd(c, f"pelletfx {target}")
                print("  pelletfx:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "23":
            try:
                r = cmd(c, f"boom {target}")
                print("  boom:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "24":
            n = ask("Number of coins", "3")
            try:
                r = cmd(c, f"coins {target} {n}")
                print("  coins:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "25":
            try:
                r = cmd(c, "screen")
                print("  screen:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "26":
            try:
                r = cmd(c, "tetris")
                print("  tetris:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "27":
            try:
                r = cmd(c, "snake")
                print("  snake:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "28":
            try:
                r = cmd(c, "pong")
                print("  pong:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "29":
            hp = ask("Boss HP", "1500")
            try:
                r = cmd(c, f"boss {hp}")
                print("  boss:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "30":
            try:
                r = cmd(c, "waves")
                print("  waves:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "31":
            try:
                r = cmd(c, "map")
                print("  map:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "32":
            try:
                r = cmd(c, "potato")
                print("  potato:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "33":
            mins = ask("Minutes", "5")
            try:
                r = cmd(c, f"koth {mins}")
                print("  koth:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "34":
            try:
                r = cmd(c, "canvas")
                print("  canvas:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "35":
            size = ask("Size (big/small)", "big")
            try:
                r = cmd(c, f"apple {size}")
                print("  apple:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "36":
            try:
                r = cmd(c, f"cleanup {target}")
                print("  cleanup:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "37":
            try:
                r = cmd(c, "talk")
                print("  talk:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "38":
            try:
                r = cmd(c, "applesign")
                print("  applesign:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "39":
            pat = ask("Block pattern (e.g., 1x1_stuffed)")
            color = ask("Color", "red")
            size = ask("Size (WxHxD)", "3x3x1")
            try:
                r = cmd(c, f"block {pat} {color} {size}")
                print("  block:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "40":
            try:
                r = cmd(c, "boss pause")
                print("  boss pause:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "41":
            try:
                r = cmd(c, "boss resume")
                print("  boss resume:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "42":
            try:
                r = cmd(c, "thaw")
                print("  thaw:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "43":
            try:
                r = cmd(c, "players")
                print("  players:", r)
            except Exception as e:
                print(f"  error: {e}")
        elif ch == "44":
            cmd_str = ask("Command")
            if cmd_str:
                try:
                    r = cmd(c, cmd_str)
                    print("  result:", r)
                except Exception as e:
                    print(f"  error: {e}")
        elif ch == "45":
            try:
                r = c.get("/api/photon/log")
                print(r if isinstance(r, str) else pretty(r))
            except Exception as e:
                print(f"  error: {e}")
        else:
            print("  pick 1-46")


if __name__ == "__main__":
    main()