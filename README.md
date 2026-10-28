# Yeeps Buddy — 2.32.0 self-host kit

Run a fan-made Yeeps private server on your own PC, with your own MongoDB database
and Photon apps. Build a configured APK that installs separately as **Yeeps
Buddy** (`com.BuddyGames.Buddy`). Based on Blaze's self-host kit and the inspected
`live79-blaze.apk`; the actual game version is **2.32.0**, and Buddy reports
**2.32.0-buddy** despite this repository's older 2.31.4 name.

> **Early-stage project:** expect bugs, incomplete features and untested behavior.
> Some parts have only been checked offline or against the backend. Passing tests
> or a successful APK build do not mean everything works on a Quest. Back up your
> database and signing key before updating; experiment on a separate test database.

## Made for Claude Code and Codex

Set this up with **[Claude Code](https://claude.com/claude-code)** or
**[Codex](https://developers.openai.com/codex/)**. Download or clone this repository,
open its folder in your assistant, and ask it to do the setup with you. You do not
need to know Python or Android development.

Claude Code starts with [CLAUDE.md](CLAUDE.md); Codex uses [AGENTS.md](AGENTS.md)
([how Codex reads project instructions](https://developers.openai.com/codex/guides/agents-md)).
Both point to the same current project guide. Example requests:

- "Read the project guide, set up my Buddy server, and help me build my APK."
- "Help me configure my Photon Realtime and Voice apps."
- "Help players outside my Wi-Fi connect to my server."
- "Install the compact public world, keeping private saves intact."
- "Help me add a GLB decoration or a music disc using the Buddy tools."
- "Check my server and explain what still needs headset testing."

You supply the source APK, your Photon app IDs and any database credentials.
Keep credentials in local configuration, not source files, public issues or
shared logs. There is no admin website; administration currently uses the
assistant and database tools.

## What is included

| Location | Purpose |
| --- | --- |
| `server/` | Python/FastAPI backend with MongoDB storage |
| `tools/configure_buddy_apk.py` | Configure, patch, align, sign and verify the inspected 2.32 APK |
| `tools/install_compact_world.py` | Explicit installation of Buddy's custom public rooms |
| `tools/data/` | Offline custom GLB block and music-disc tools |
| `tests/` | Backend, data and native-patch regression checks |
| `client/native/` | Historical 1.31 native source; not a replacement library for Buddy |

APKs, game assets, signing keys, databases and Android/emulator downloads are not
included. The patcher retains the supplied Unity 6000-compatible native library
and Explorer, changes host settings, and disables optional remote feed/video/
cosmetic downloads and remote-log uploads. The original APK stays unchanged.

## 1. Start your server

Install **Python 3.11+** and **MongoDB Community Server**, or use MongoDB Atlas.
On Windows, install Python from python.org with "Add python.exe to PATH" enabled.
Start MongoDB first. From the repository folder:

```bash
# Linux / macOS
bash start.sh
```

```bat
rem Windows Command Prompt
start.bat
```

First run creates `server/.venv`, installs dependencies and creates `server/.env`
with a random `DEVICE_KEY_PEPPER`. Keep the terminal open. If MongoDB connection
fails, edit `MONGO_URI` in `server/.env` and restart. Buddy defaults:

```ini
MONGO_URI=mongodb://127.0.0.1:27017
DB_NAME=Yeeps232Buddy
CLIENT_PROFILE=buddy232
REQUIRED_CLIENT_VERSION=2.32.0-buddy
```

For Atlas, use your own connection string. Use a separate Buddy database.
Existing `.env` files and records are preserved, so update old settings yourself.
Keep the generated pepper stable. Other settings are explained in
[server/.env.example](server/.env.example).

Open `http://127.0.0.1:8000/health` to check database connectivity and the selected
client version. Use your chosen port throughout if you changed `PORT`.

In a second terminal, activate the environment for subsequent Python commands:

```bash
# Linux / macOS
source server/.venv/bin/activate
```

```bat
rem Windows Command Prompt
server\.venv\Scripts\activate.bat
```

Check the running server:

```bash
python tools/smoke_test.py http://127.0.0.1:8000
```

Expect `ALL PASS`. This creates a test account and checks HTTP/backend behavior;
Photon joins, Voice and headset rendering need separate testing.

## 2. Install the compact public world

Startup seeds the base layout and catalogs without original production map
builds. For Buddy's custom public spaces, explicitly run against your Buddy
database after startup, using the activated environment:

```bash
python tools/install_compact_world.py
```

Public Hub connects to Welcome, Playground, Wilds and private worlds. The three
hub/play rooms contain generated builds. **Wilds is empty temporary creative
space (`noSave=1`); use private worlds for persistent builds.** These are custom
Buddy spaces, not copies of production maps.

The installer backs up affected public records under `build/backups/`, verifies
private data is unchanged, and preserves edits if already installed. It does not
migrate later generator changes into existing installs. Older installs with
incorrect Wilds flags need a targeted, backed-up update; see the project guide.
Reopen the game after changing room settings.

## 3. Configure Photon and your server address

Create your own **Photon Realtime/PUN** and **Photon Voice** apps. Configure the
Realtime app's Custom Authentication URL to your publicly reachable server base
URL, ending in `/`. Allow anonymous clients in both apps' authentication settings.
Use your own app IDs in the APK builder.

Photon Cloud needs a **public HTTPS** callback even when your headset uses LAN
HTTP. `127.0.0.1` is only for checks on the host PC; headsets need its reachable
LAN address or public URL. For remote players, use a stable HTTPS endpoint such
as a named tunnel or hosted reverse proxy. Keep MongoDB private.

With `cloudflared` installed, `bash start.sh --tunnel` or `start.bat --tunnel`
prints a temporary HTTPS address. Quick-tunnel addresses change on restart;
update Photon authentication and rebuild the APK when the address changes.
Allow Python through your firewall as needed.

## 4. Build your Buddy APK

Supply the **original inspected `live79-blaze.apk`**. It is not bundled or
downloaded by these scripts. Supported source SHA256:

```text
21e203194d07a20a68f37b4d34d74ab2c34e94077f38ae26d9caf90c8e37a905
```

Other builds, including already-patched Buddy APKs, are refused. Do not remove
fingerprint checks or inject the historical 1.31 native library.

Install a JDK (`keytool`) and Android SDK Build-Tools 35 (`zipalign`, `apksigner`).
Put the tools on PATH or set `ANDROID_HOME` to your SDK. In the active environment:

```bash
python -m pip install -r tools/requirements-buddy-apk.txt
```

The following single-line command works in Bash or Windows Command Prompt;
replace the path, server and app-ID placeholders:

```text
python tools/configure_buddy_apk.py --apk "/path/to/live79-blaze.apk" --server https://YOUR-SERVER-HOST --photon-app-id YOUR-REALTIME-APP-ID --photon-voice-app-id YOUR-VOICE-APP-ID
```

Default output: `build/Yeeps-2.32.0-buddy.apk`. Optional `--photon-region eu`
sets both apps to the same fixed region; blank selects automatically.
The builder patches native/Unity settings, package/version and network policy,
preserves ZIP compression, aligns, signs and verifies. A JSON sidecar records
its checksum and static verification state.

Back up `tools/.keys/yeeps-buddy.keystore` **and its `.pass` file** privately.
They identify your app for future updates and are ignored by Git. Losing them
can force an uninstall to change signers, removing local app data. Buddy does
not read the old `blaze-config.txt`; rebuild when endpoints or Photon IDs change.
`tools/configure_apk.py` is for the historical 1.31 kit only.

## 5. Install and test on Quest

Install with SideQuest, or enable developer mode/USB debugging and run:

```bash
adb install -r build/Yeeps-2.32.0-buddy.apk
```

Launch **Yeeps Buddy**. Leave the original `com.TrassGames.Yeeps` installation
alone. The separate package uses a sideload fallback when Meta user lookup fails;
this is a private-server identity, not proof of a Meta-authenticated user.

Keep the server console visible. Check login and spawning, public-room travel,
private-world placement/save/relaunch, then a second player's join and Voice.
The game may pause when the headset is not worn. Never automate a headset power
button press. Capture fresh logs before changing settings after a failure:

```bash
adb logcat -d -v threadtime -s Unity BNMExplorer MYDBG AndroidRuntime > headset-log.txt
adb pull /sdcard/Android/data/com.BuddyGames.Buddy/files/bnm_checkpoint.txt
```

Logs may contain login data, device keys and personal details; redact before
sharing. Refract can assist development but is not bundled or required, and
emulator testing does not replace headset testing.

## Current testing limits

- Regression checks exist, including APK checks requiring the source APK and
  an interaction-asset check requiring generated custom data.
- Startup has been observed in Refract. Private-world placement/save/load and
  return to Public Hub were reported working on Quest in this development setup.
- Wilds joins with actual Creative state in Refract; placement/removal there
  remains unverified.
- Fumo placement was reported working; pickup after its asset repair still
  needs headset testing. Custom-disc playback, all public routes, full cosmetics,
  moderator UI and multiplayer/Voice need further testing. Photon Voice
  authentication errors remain open.
- Modern remote-backed features are incomplete. Owning a pattern does not prove
  its associated feature works. See [public status](docs/STATUS.md).

## Custom blocks and music

See [tools/data/README.md](tools/data/README.md) for supported GLB inputs,
named block/music-disc commands, attribution and packaging with `--data`.
Players need matching updated APKs and explicit ownership of custom pattern keys.
Do not grant unknown custom keys to players using older APKs.

## Development and publishing

```bash
python -m pip install -r tests/requirements.txt
python -m unittest discover -s tests -v
```

Set `BUDDY_SOURCE_APK` to the original APK to enable APK-specific checks. Set
`BUDDY_CUSTOM_DATA` to the complete generated Fumo asset folder for its tag/collider
check. These local inputs are absent from Git. Run the HTTP smoke test with a
real Mongo-backed server after backend changes.

Read [the shared project guide](docs/PROJECT_GUIDE.md) and
[public status](docs/STATUS.md) before changing code. Technical evidence is in
[docs/apk-inspection/](docs/apk-inspection/). The
[original 1.31 guide](docs/legacy-1.31-README.md) is historical reference.
Legacy requires `CLIENT_PROFILE=legacy`, `DB_NAME=Yeeps131` and
`REQUIRED_CLIENT_VERSION=1.31.0-blaze` in a separate environment/database.

Before publishing, review `git status --short`, `git diff --check` and the exact
staged diff. `.gitignore` excludes local handoffs, credentials, signing files,
APKs/assets, environments, databases, logs and backups. It cannot hide files
already tracked by Git. Never publish private account allowlists or raw logs.

## Credits

Based on Blaze's Yeeps self-host kit and APK work. Thanks to **@Mixid** for the
AWS API work. Original community Discord: https://discord.gg/VhFmdAyXGk.
Third-party code retains its existing notices; model attribution is in the
content guide. Fan-made project; not affiliated with Trass Games or Yeeps.
