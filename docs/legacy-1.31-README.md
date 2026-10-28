# Yeeps 1.31.0-blaze (self-host kit)

> Historical upstream guide. Release assets and Claude-only instructions below
> describe the original kit, not Buddy. For current setup use [the Buddy README](../README.md).

Run your own Yeeps 1.31.0-blaze server on your own PC, with your own Photon apps.
Your players install an APK made for your server. Nothing here talks to anyone
else's game server.

Join the Discord: https://discord.gg/VhFmdAyXGk

Thanks to @Mixid for the aws api!

> **Status (kit2):** the game and the BNM mod menu, nothing else (kit1 also had a web
> editor and other extras). kit1 was tested end to end on a headset; kit2 is the same
> game with those extras taken out. Problems or questions: ask in the Discord.

## Made for Claude

This kit is made to be set up with **[Claude Code](https://claude.com/claude-code)**, an
AI assistant that works on your PC. You don't need to know Python or Android tools:
the repo has a guide written for Claude (`CLAUDE.md`), and Claude follows it to do the
setup with you.

1. Install Claude Code from https://claude.com/claude-code.
2. Download this repo (green **Code** button > **Download ZIP**, then unzip it, or
   `git clone` it) and open the folder in Claude Code.
3. Ask it what you want, for example:
   - "Set up my server and make my APK"
   - "Help me make my Photon apps"
   - "Let players on other networks join my server"
   - "Put my song on a record"
   - "Add a picture block of my dog"
   - "I want maps"

Claude runs the steps on your own PC and tells you when it needs something from you
(your Photon App IDs, a headset plugged in). The rest of this README is the same steps
by hand, if you'd rather do it yourself.

## What you get

- **The APK** (historically supplied through upstream Releases): the original Yeeps 1.31.0 game with
  GameAnalytics tracking blocked, the version shown as "1.31.0-blaze", the BLAZE
  block, the news board with its Discord button, and the BNM mod menu (opened from the
  news board). No other added content.
- **A server** that runs in a console window on your PC (`start.bat` on Windows,
  `start.sh` on Linux/macOS): logins, the world and private worlds. There's no admin
  website: see [Players, bans and roles](#players-bans-and-roles).
- **The 1.31 world layout**: Starter House, Playground, Blue House, the Wilds, the
  tutorial, the Build-A-Map rooms and the rest, with **no maps built in them**: every
  room starts empty. See [Maps](#maps).
- **Tools** to make your APK, check your server, import maps, and add your own blocks
  and music.

Not included: an admin website, community worlds, live player lookups from real Yeeps,
a Discord bot, or a Photon bot.

## What you need

- A PC that stays on while people play (Windows, Linux or macOS).
- **Python 3.11+**: on Windows get it from https://www.python.org/downloads/ and tick
  "Add python.exe to PATH" (the "python" that opens the Microsoft Store doesn't work).
- **A database**: [MongoDB Community Server](https://www.mongodb.com/try/download/community)
  on the same PC (default settings are fine), or a free
  [MongoDB Atlas](https://www.mongodb.com/cloud/atlas) cluster.
- **A free [Photon](https://dashboard.photonengine.com) account.**
- **To make your APK: Java and Android build-tools.**
  - Java: [Eclipse Temurin JDK](https://adoptium.net) (Windows: tick "Add to PATH").
  - Android build-tools (`zipalign`, `apksigner`): install
    [Android Studio](https://developer.android.com/studio) and add "Android SDK
    Build-Tools" in its SDK Manager, or the command-line tools and run
    `sdkmanager "build-tools;34.0.0"`. Then add the build-tools folder to PATH, or set
    `ANDROID_HOME` (Windows: usually `%LOCALAPPDATA%\Android\Sdk`).
- **Quest headsets** with developer mode, and [SideQuest](https://sidequestvr.com) or `adb`.

## Setup

### 1. Make two Photon apps

At https://dashboard.photonengine.com create a **PUN** app (Multiplayer Game, Photon PUN)
and a **Voice** app (Photon Voice). Copy both App IDs.

The game signs in to Photon with **custom authentication** (the Yeeps client always has),
so the **PUN app needs one Custom Authentication provider pointing at your server**, or
players log in but the room join is rejected with "Authentication type not supported"
(ReturnCode 32755). You can't fill in the URL until you've picked your server address, so
do this after step 3:

- Open the **PUN** app > **Authentication** (or "Custom Server Authentication") and add a
  provider of type **Custom**.
- **Authentication URL** = your server's public address with a trailing slash, e.g.
  `https://yeeps.your-domain.com/` or your quick tunnel `https://....trycloudflare.com/`
  (the same address you put in `--server`, just the base ending in `/`). Photon calls it
  and your server answers; nothing else to configure there.
- Leave **Allow anonymous clients** ticked.
- The **Voice** app needs no provider: just tick **Allow anonymous clients** on it. (If
  voice ever shows the same 32755, add the same Custom URL to the Voice app too.)

### 2. Start the server

Windows: double-click `start.bat`. Linux/macOS: `./start.sh`.

The first run installs the Python packages and creates `server/.env`. In it, set
`MONGO_URI`: leave the default for a local MongoDB, or paste your Atlas connection string.

Close the window and start it again after editing `.env`. Keep it open while people play.

Check it: `python tools/smoke_test.py http://127.0.0.1:8000` should end with `ALL PASS`.

### 3. Pick your server's address

The headsets need to reach your PC. Pick one:

- **Same Wi-Fi only:** `http://<your PC's local IP>:8000`, e.g. `http://192.168.1.20:8000`
  (Windows: `ipconfig` shows it). Your firewall must allow the port: Windows asks the
  first time the server starts (allow "Private networks"); on Linux with ufw run
  `sudo ufw allow 8000/tcp`.
- **Over the internet, fixed address (best):** a Cloudflare named tunnel with your own
  domain, e.g. `https://yeeps.your-domain.com`, pointed at `http://127.0.0.1:8000`
  (see Cloudflare's "Create a tunnel" guide).
- **Quick internet test:** `start.bat --tunnel` / `./start.sh --tunnel` opens a free
  `https://....trycloudflare.com` address (Windows: `winget install --id Cloudflare.cloudflared`
  first). It changes every time, so use it only for testing (see "Override file").

### 4. Make your APK

Download `Yeeps-1.31.0-blaze-kit2.apk` from Releases, then:

```
python tools/configure_apk.py --apk Yeeps-1.31.0-blaze-kit2.apk --server http://192.168.1.20:8000 --photon-app-id <PUN app id> --photon-voice-app-id <Voice app id>
```

It writes your settings into the APK and signs it with your own key (created in
`tools/.keys/` the first time: keep that folder, or later APKs won't install over
earlier ones). The output is `Yeeps-1.31.0-blaze-<your server>.apk`.

### 5. Install it on each headset

With SideQuest, or `adb install -r <your apk>`. If another Yeeps build is already
installed, uninstall it first: a different signing key can't update it.

## Players, bans and roles

There's no admin website. Everything lives in your MongoDB database (`DB_NAME` in
`server/.env`, collections `accounts`, `rooms`, `globals`, ...). Ask Claude ("ban
<player>", "give <player> the BLAZE block"), or edit it yourself with
[MongoDB Compass](https://www.mongodb.com/products/compass). Players who should ban and
warn from inside the game go in `MOD_STAFF` in `server/.env`.

## Maps

The kit has no map builds. To get maps, use
[YeepsMapLoader](https://github.com/Blazeisodd/YeepsMapLoader) (a Unity tool that loads
real Yeeps rooms), save the room's block list (JSON) to a file, and import it:

```
python tools/import_map.py --room official_1 map.json
```

Blocks that don't exist in 1.31 are left out. Players see the map the next time they
load that room. You can also build in game: set `UNLOCK_ALL_MODES=1` in `.env` and
restart while you build. Then anyone can switch rooms to creative and their builds are
saved, so turn it off again when your maps are done.

## Your own music (disks)

Put your song on a record inside the APK, then give players the new APK:

```
python tools/data/extract_data.py Yeeps-1.31.0-blaze-kit2.apk data
python tools/data/bake_disk.py data --list
python tools/data/bake_disk.py data --clip SuburbTheme --audio song.mp3 --art recordArtTexture_suburbTheme --label cover.png
python tools/configure_apk.py ... --data data
```

## Your own blocks (prefabs)

Add a picture block (a cube with your picture on every side):

```
python tools/data/extract_data.py Yeeps-1.31.0-blaze-kit2.apk data
python tools/data/add_picture_block.py data/data.unity3d data/data.unity3d --key 1x1_stuffedMyDog --name "My Dog" --image dog.png
python tools/configure_apk.py ... --data data
```

Then give players the block: add the key (here `1x1_stuffedMyDog`) to their
`ownedPatterns` in the database (ask Claude: "give everyone my dog block").

**The BLAZE block** (`1x5x2_stuffedTextBlaze`) is only for players with the `blaze`
role: put their account ID in `BLAZE_HOLDERS` in `server/.env` and add `blaze` to
their `roleKeys` (ask Claude: "give <player> the BLAZE block").

## Override file (testing)

A headset can use a different server without a new APK. Put a file named
`blaze-config.txt` at `/sdcard/Android/data/com.TrassGames.Yeeps/files/` with:

```
server=https://whatever.trycloudflare.com
photon_app_id=<PUN app id>
photon_voice_app_id=<Voice app id>
```

`python tools/configure_apk.py ... --settings-only` prints this file for you. The only
settings are the server address and your Photon apps; the version label, the news board
and its Discord button are fixed and can't be changed here.

## Troubleshooting

- **"address already in use" when the server starts:** another program uses port 8000.
  Set `PORT=8001` (or any free port) in `server/.env`, and use that port in the APK's
  `--server`.
- **"Install Python 3.11 or newer"** on Windows: install it from python.org with
  "Add python.exe to PATH" ticked, then open a new window.
- **The game can't reach the server:** check the `--server` you built the APK with
  and your firewall. From the headset's network,
  `<server>/health` should answer.

## Disclaimer

A fan-made private server project. Not affiliated with or endorsed by Trass Games or
Yeeps. Don't use it to cheat in, or harm, the real game.
