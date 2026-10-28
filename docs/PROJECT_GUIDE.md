# Shared project guide for Claude Code and Codex

## Start here

Read this guide, `README.md` and `docs/STATUS.md`. An ignored local `HANDOFF.md`
may describe an existing deployment; it is optional for fresh clones and stays
private. Its historical permissions do not authorize new live changes. Inspect
the current working tree and services; preserve unrelated work and existing data.

This early-stage kit targets **2.32.0-buddy**. Help hosts in plain language and
run authorized setup for them. Ask only for information unavailable locally,
such as their own Photon IDs. Windows is supported: use `python`, quote paths
with spaces and adapt virtualenv paths. No personal plugins or paths are required.

## Setup workflow

1. Check Python 3.11+, MongoDB availability and the intended database. Run
   `bash start.sh` or `start.bat`; these create the environment and `.env`.
   Edit `server/.env` for private settings. Defaults: `CLIENT_PROFILE=buddy232`,
   `DB_NAME=Yeeps232Buddy`, `REQUIRED_CLIENT_VERSION=2.32.0-buddy`. Keep the
   generated pepper stable. Do not reuse or reset a legacy database.
2. Confirm `/health`, then run `python tools/smoke_test.py <base-url>` using
   `server/.venv` (Windows: `server\.venv\Scripts\python.exe`). The smoke test
   creates a test account; explain that when checking an existing deployment.
3. For new hosts wanting public spaces, explicitly run
   `python tools/install_compact_world.py`. It backs up affected public records
   and verifies private data. Startup alone does not install these builds.
4. Configure the host's Photon Realtime/PUN and Voice apps. Realtime Custom
   Authentication needs a public HTTPS server URL ending in `/`; both apps need
   anonymous clients allowed. A LAN-only callback is not reachable by Photon.
5. Install `tools/requirements-buddy-apk.txt`, a JDK and Android Build-Tools 35.
   Run `tools/configure_buddy_apk.py` with the original inspected APK, reachable
   host address and host's IDs. Preserve fingerprint checks, ZIP compression,
   16KB alignment, signing and verification. Back up signing files privately.
6. When installation is requested, use `adb install -r` for Buddy. Keep original
   Yeeps installed. Do not clear app data or uninstall as routine troubleshooting;
   explain data consequences before any destructive change.
7. Report the reachable address and concrete checks performed. Walk the host
   through headset testing and identify untested behavior explicitly.

## Architecture and compatibility

- `server/main.py` strips backend/device/build prefixes and routes direct API,
  Lambda (`routers/lambda_router.py`) and DynamoDB (`services/dynamo_ops.py`)
  requests. `services/db.py` maps game tables to Mongo collections.
- `seed/load_world.py` inserts missing base data. Do not make startup destructive.
  `server/seed/buddy232/` separates pattern ownership from block validation.
  Preserve roles, filters, custom ownership and equipped cosmetics.
- Modern supplemental room properties are keyed by room ID. Keep numeric
  session fields numeric, exact map session/offset chains, SPAM v3 payloads,
  private-world return transitions and official-room save protections.
- `tools/buddy_apk.py` and `tools/buddy_ovr.py` patch only fingerprinted live79.
  Keep successful SDK response behavior and dispatch-slot sideload hooks.
  Never inject `client/native/`'s historical 1.31 library or reuse old offsets.
- `tools/configure_apk.py`, `add_picture_block.py` and `bake_disk.py` are legacy.
  Use Buddy's builder, `add_model_block.py` and `add_music_disc.py` for current
  content; see `tools/data/README.md` for inputs and attribution.
- No original production maps ship here. Import compatible, host-provided maps
  with `tools/import_map.py`, checking `--dry-run` first. Always work on full
  block lists, never a capped preview. Do not contact the official backend.
- `UNLOCK_ALL_MODES=1` broadly changes public save protections. Do not enable
  it as routine setup or to repair Wilds; use the flags in `docs/STATUS.md`.

## Players and moderation

There is no admin website. Find accounts by accountID or displayName and make
targeted, backed-up changes within the current request. Never copy personal
grants from an earlier deployment or grant every role to everyone.

`MOD_STAFF` controls moderation allowlists; bound-headset proof still applies.
`MOD_UNTAGGED_STAFF` is an explicit Buddy-only exception for approved untagged
clients, not a blanket bypass. `UNRESTRICTED_COSMETIC_ACCOUNTS` is account-scoped.
Keep allowlists in `.env`. Blaze items need the `blaze` role plus holder
membership. `cwAdmin`, `cwStaff`, `cwVIP` are room-context roles, not global roles.
Preserve saved maps and equipped cosmetics during grants.

## Verification

Install `tests/requirements.txt` in the virtualenv, then run:

```bash
python -m unittest discover -s tests -v
git -c core.whitespace=cr-at-eol diff --check
```

The suite uses an isolated Mongo test double. `BUDDY_SOURCE_APK` enables actual
source-APK checks; `BUDDY_CUSTOM_DATA` enables the generated Fumo tag/collider
check. Missing inputs cause skips; report them. Static checks do not prove
gameplay. After backend changes run the HTTP smoke test with real Mongo on an
appropriate test deployment. Preserve Windows batch CRLF. Use plain host-facing
text, US spelling and no emojis.

## Private data and publishing

Never dump `.env`, passwords, raw device/auth logs, `/k/<device-key>` URLs,
account allowlists or signing passwords. Inspect only necessary fields; redact
captures before sharing. Keep generated assets, backups, SDK/emulators and
evidence under ignored `build/`, and keep database data/logs ignored.

Before a push inspect changed/untracked files, the exact staged diff and tracked
files matching ignore rules. `.gitignore` does not untrack secrets or clean
history. Do not commit or push until requested. Public technical status belongs
in `docs/STATUS.md`; local handoffs and deployment identities stay ignored.
