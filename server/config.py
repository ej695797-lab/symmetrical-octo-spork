"""Server settings. Everything comes from environment variables, usually set in
server/.env (start.bat / start.sh create it on first run; see .env.example).
Nothing secret lives in this file."""
import os
import hmac
import hashlib
import time


def _load_dotenv() -> None:
    """Minimal .env reader (KEY=VALUE lines) so no extra package is needed.
    Real environment variables win over the file."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv()


def _set(name: str) -> set:
    return set(filter(None, (os.getenv(name) or "").replace(" ", "").split(",")))


# ─── Database ────────────────────────────────────────────────────────────────
# A local MongoDB (mongodb://127.0.0.1:27017) or a free MongoDB Atlas cluster.
MONGO_URI = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017")
CLIENT_PROFILE = os.getenv("CLIENT_PROFILE", "buddy232")
if CLIENT_PROFILE not in {"buddy232", "legacy"}:
    raise ValueError("CLIENT_PROFILE must be buddy232 or legacy")
IS_BUDDY = CLIENT_PROFILE == "buddy232"
DB_NAME = os.getenv("DB_NAME", "Yeeps232Buddy" if IS_BUDDY else "Yeeps131")

SERVER_PORT = int(os.getenv("PORT", "8000"))
# Load the 1.31 world (seed/world/*.json) into an empty database at startup.
SEED_DB = os.getenv("SEED_WORLD", "1") == "1"


# Version the 1.31.0-blaze client reports; other versions are told to update.
REQUIRED_CLIENT_VERSION = os.getenv("REQUIRED_CLIENT_VERSION", "2.32.0-yeeper920" if IS_BUDDY else "1.31.0-blaze")
try:
    IS_V2 = int(REQUIRED_CLIENT_VERSION.split(".")[0]) >= 2
except ValueError:
    IS_V2 = False

# Testing switch: every room's play/creative mode can be switched and anyone can
# edit official maps in-game.
UNLOCK_ALL_MODES = os.getenv("UNLOCK_ALL_MODES", "0") == "1"

# ─── Client checks ───────────────────────────────────────────────────────────
# 1.19-era attestation hashes (the 1.31 client sends none, so these stay empty).
CURRENT_CLIENT_ATTEST_HASH = os.getenv("CLIENT_ATTEST_HASH", "")
ALLOWED_ATTEST_HASHES = {h.upper() for h in _set("CLIENT_ATTEST_HASHES")}
if CURRENT_CLIENT_ATTEST_HASH:
    ALLOWED_ATTEST_HASHES.add(CURRENT_CLIENT_ATTEST_HASH.upper())
ENFORCE_ATTEST_HASH = os.getenv("ENFORCE_ATTEST_HASH", "0") != "0"
RETIRED_ATTEST_HASHES = {h.upper() for h in _set("RETIRED_ATTEST_HASHES")}

# APK build gate (services/build_gate.py). The APK sends /c/<buildId>.<hash>.<hash>/
# in its login path. OFF by default: hosts may rebuild the APK, which changes
# the hashes. Turn it on (ENFORCE_CLIENT_BUILD=1) to allow only listed builds.
ALLOWED_CLIENT_BUILDS = _set("ALLOWED_CLIENT_BUILDS") or ({"buddy1"} if IS_BUDDY else {"kit1"})
ALLOWED_CLIENT_TAGS = _set("ALLOWED_CLIENT_TAGS")
ENFORCE_CLIENT_BUILD = os.getenv("ENFORCE_CLIENT_BUILD", "0") != "0"

# Covert deny: what a covertly refused client sees (looks like a network error).
COVERT_DENY_MESSAGE = os.getenv(
    "COVERT_DENY_MESSAGE",
    "Couldn't reach the server. Check your connection and try again.")
SHADOW_BAN_IDS = _set("SHADOW_BAN_IDS")
SHADOW_BAN_IPS = _set("SHADOW_BAN_IPS")

# Rotating client token (only observed unless ENFORCE_CLIENT_TOKEN=1; the kit
# APK doesn't send one).
CLIENT_TOKEN_SECRET = os.getenv("CLIENT_TOKEN_SECRET", "change-me")
CLIENT_TOKEN_ROTATE_SECONDS = int(os.getenv("CLIENT_TOKEN_ROTATE_SECONDS", "3600"))
ENFORCE_CLIENT_TOKEN = os.getenv("ENFORCE_CLIENT_TOKEN", "0") == "1"


def client_token(window: int = None) -> str:
    """The 6-digit code valid for the given rotation window (default: now)."""
    if window is None:
        window = int(time.time() // CLIENT_TOKEN_ROTATE_SECONDS)
    mac = hmac.new(CLIENT_TOKEN_SECRET.encode(),
                   f"clienttoken|{window}".encode(), hashlib.sha256).hexdigest()
    return f"{int(mac, 16) % 1000000:06d}"


def valid_client_tokens() -> set:
    """Accept the current window plus ±1 for clock skew / hour boundaries."""
    w = int(time.time() // CLIENT_TOKEN_ROTATE_SECONDS)
    return {client_token(w - 1), client_token(w), client_token(w + 1)}


# The news board's Discord button.
DISCORD_INVITE_URL = os.getenv("DISCORD_INVITE_URL", "https://discord.gg/VhFmdAyXGk")

# Blocks owned by accounts that aren't in the database (official players in the
# copied world maps) are given to this account on map saves. Blank = leave them.
DEFAULT_MAP_OWNER = os.getenv("DEFAULT_MAP_OWNER", "")
EDITOR_PW_WEBHOOK = os.getenv("EDITOR_PW_WEBHOOK", "")
EDITOR_PW_ROTATE_SECONDS = max(60, int(os.getenv("EDITOR_PW_ROTATE_SECONDS", "3600")))