"""
Yeeps v1 Private Server API
FastAPI + MongoDB replacement for the original AWS Lambda + DynamoDB backend.

The game client sends requests to three "services" all on the same host.
We detect which service by inspecting the path, headers, and host.
"""

import json
import logging
import re
import os
from datetime import datetime, timezone
from pathlib import Path

from starlette.concurrency import iterate_in_threadpool
from fastapi import FastAPI, Request
from fastapi.responses import Response, JSONResponse

from routers.lambda_router import router as lambda_router
from routers.dynamo_router import router as dynamo_router
from seed.load_world import load_world
from services.replay_log import lookup_replay
from services.db import accounts, globals_col
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
app = FastAPI(title="Yeeps Buddy Private Server")

from routers.editor_admin_232 import router as editor_admin_router
app.include_router(editor_admin_router, prefix="/editor")
app.include_router(editor_admin_router, prefix="/editor/api")
app.include_router(editor_admin_router)
@app.get("/editor-ui")
async def editor_ui():
    return FileResponse(
        Path(__file__).parent / "static" / "editor_ui.html"
    )


@app.get("/me")
async def me():
    from config import DB_NAME
    return {"name": "admin", "tier": "admin", "db": DB_NAME}

@app.get("/health")
async def health():
    from config import CLIENT_PROFILE, REQUIRED_CLIENT_VERSION
    from services.db import db
    try:
        await db.command("ping")
    except Exception:
        return JSONResponse({"status": "database unavailable", "profile": CLIENT_PROFILE,
                             "clientVersion": REQUIRED_CLIENT_VERSION}, status_code=503)
    return {"status": "ok", "profile": CLIENT_PROFILE, "clientVersion": REQUIRED_CLIENT_VERSION}

# ── Abuse protection ─────────────────────────────────────────────────────────
# Stop the MongoDB from being flooded by a public client spamming logins/saves:
# a per-IP request rate limit plus a request-body size cap. In-memory state
# (fine for a single Render instance); generous enough for normal play and the
# burst of calls at room-load.
import time as _time
_RL: dict = {}                          # ip -> [window_start, count]
_RL_MAX = 600                           # max requests per window per IP
_RL_WINDOW = 60                         # window seconds
_MAX_BODY = 12 * 1024 * 1024            # 12 MB max request body

# Hardcoded IP bans (other IP bans live in services/ip_bans.py).
BLOCKED_IPS: set = set()


@app.middleware("http")
async def abuse_guard(request: Request, call_next):
    # 0) hard IP ban (hardcoded set + persistent auto-bans from ip_bans)
    try:
        from services.ip_bans import client_ip, is_banned
        ip0 = client_ip(request)
        if ip0 in BLOCKED_IPS or await is_banned(ip0):
            # A player banned mid-session stays in their Photon room unless their
            # game hears about the ban, and it only does through its account poll
            # (DynamoDB GetItem -> shouldForceRefresh) and refreshAccountOnline
            # (-> isPermabanned). Let just those through so the ban lands and the
            # game leaves; everything else from a banned IP stays 403.
            # The header alone isn't enough: a raider's script sent X-Amz-Target
            # GetItem on its lambda calls too (Oct 10), so the GetItem pass is
            # only for the DynamoDB endpoint itself.
            tgt = request.headers.get("x-amz-target", "")
            _p = request.url.path
            _dynamo_root = _p == "/" or (_p.startswith(("/k/", "/c/")) and _p.rstrip("/").count("/") <= 4
                                         and "/functions/" not in _p and "/2015-03-31/" not in _p)
            if not ((tgt.endswith(".GetItem") and _dynamo_root) or "g2_refreshAccountOnline" in _p):
                return JSONResponse({"message": "Forbidden"}, status_code=403)
    except Exception:
        pass
    # 1) reject oversized bodies outright
    try:
        cl = request.headers.get("content-length")
        if cl and int(cl) > _MAX_BODY:
            return JSONResponse({"message": "Payload too large"}, status_code=413)
    except Exception:
        pass
    # 2) per-IP rate limit on game traffic
    try:
        path = request.scope.get("path", "") or ""
        if True:
            ip = request.client.host if request.client else "?"
            now = _time.time()
            w = _RL.get(ip)
            if w is None or now - w[0] >= _RL_WINDOW:
                _RL[ip] = [now, 1]
            else:
                w[1] += 1
                if w[1] > _RL_MAX:
                    return JSONResponse({"message": "Too Many Requests"}, status_code=429)
            if len(_RL) > 5000:          # bound memory: drop idle IPs
                cutoff = now - _RL_WINDOW
                for k in [k for k, v in list(_RL.items()) if v[0] < cutoff]:
                    _RL.pop(k, None)
    except Exception:
        pass
    return await call_next(request)


# Strip the APK patch prefix so routes still match
_PATCH_PREFIX = "/xxxxxxxxxxxxxxxxxx"

@app.middleware("http")
async def strip_apk_prefix(request: Request, call_next):
    if request.scope["path"].startswith(_PATCH_PREFIX):
        request.scope["path"] = request.scope["path"][len(_PATCH_PREFIX):]
        request.scope["raw_path"] = request.scope["path"].encode()
    # 131b14+ APKs add the headset's device key: /k/<64 hex>/... (device_auth.py)
    if request.scope["path"].startswith("/k/"):
        rest = request.scope["path"][3:]
        key, _, tail = rest.partition("/")
        request.scope.setdefault("state", {})["device_key"] = key[:64]
        request.scope["path"] = "/" + tail
        request.scope["raw_path"] = request.scope["path"].encode()
    # 1.31+ APKs add a build/integrity tag segment: /c/<tag>/... (build_gate.py)
    if request.scope["path"].startswith("/c/"):
        rest = request.scope["path"][3:]
        tag, _, tail = rest.partition("/")
        request.scope.setdefault("state", {})["client_build_tag"] = tag[:120]
        request.scope["path"] = "/" + tail
        request.scope["raw_path"] = request.scope["path"].encode()
    return await call_next(request)


from config import IS_V2 as _IS_V2
if _IS_V2:
    @app.middleware("http")
    async def force_connection_close(request: Request, call_next):
        """2.x only: the game's AWS SDK reuses a pooled HTTPS connection whose TLS
        stream was already disposed ("Cannot access a disposed object:
        MobileAuthenticatedStream") under the room-load burst; closing the
        connection after each response forces a fresh one."""
        response = await call_next(request)
        try:
            response.headers["Connection"] = "close"
            response.headers["Keep-Alive"] = "timeout=0, max=1"
        except Exception:
            pass
        return response


HTTP_LOG_FILE = Path("logs/http_traffic.log")


def _safe_decode(payload: bytes) -> str:
    """Decode bytes for logging without throwing on binary data."""
    if not payload:
        return ""
    return payload.decode("utf-8", errors="replace")


def _append_http_log(entry: str) -> None:
    HTTP_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with HTTP_LOG_FILE.open("a", encoding="utf-8") as log_file:
        log_file.write(entry)


# Full request+response body logging is OFF unless HTTP_TRAFFIC_LOG=1. Left on, it
# buffered every body in RAM (a 71 MB video upload -> ~800 MB, the APK download ->
# OOM) and wrote 19 GB to logs/http_traffic.log in ~1.5 days of self-hosting.
_TRAFFIC_LOG = os.getenv("HTTP_TRAFFIC_LOG", "") == "1"
_DIAG_DYNAMO = os.getenv("DIAG_DYNAMO", "") == "1"


class _QuietPolls(logging.Filter):
    """Drop uvicorn access lines for successful polls every player makes every
    few seconds (/client/video.txt alone was ~14k lines/hour)."""
    _PATHS = ("/client/video.txt", "/client/feed.txt", "/client/video/")

    def filter(self, record):
        a = record.args
        try:
            return not (isinstance(a, tuple) and len(a) >= 5 and int(a[4]) < 400
                        and str(a[2]).startswith(self._PATHS))
        except Exception:
            return True


logging.getLogger("uvicorn.access").addFilter(_QuietPolls())
_TRAFFIC_SKIP = ("/download",)


async def log_http_traffic(request: Request, call_next):
    if not _TRAFFIC_LOG or request.scope.get("path", "").startswith(_TRAFFIC_SKIP):
        return await call_next(request)
    try:
        if int(request.headers.get("content-length") or 0) > 256 * 1024:
            return await call_next(request)
    except ValueError:
        pass
    request_body = await request.body()
    request_body_text = _safe_decode(request_body)

    response = await call_next(request)

    response_chunks = [chunk async for chunk in response.body_iterator]
    response_body = b"".join(response_chunks)
    response_body_text = _safe_decode(response_body)

    # Rebuild response because we consumed body_iterator while logging.
    response.body_iterator = iterate_in_threadpool(iter([response_body]))

    now = datetime.now(timezone.utc).isoformat()
    full_path = str(request.url.path)
    if request.url.query:
        full_path = f"{full_path}?{request.url.query}"

    log_entry = (
        f"[{now}]\n"
        f"Client: {request.client.host if request.client else 'unknown'}\n"
        f"Method: {request.method}\n"
        f"Path: {full_path}\n"
        f"RequestBody: {request_body_text}\n"
        f"StatusCode: {response.status_code}\n"
        f"ResponseBody: {response_body_text}\n"
        f"{'=' * 80}\n"
    )
    _append_http_log(log_entry)

    return response


if _TRAFFIC_LOG:
    app.middleware("http")(log_http_traffic)


# ─── Startup ─────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    """Load the 1.31.0-blaze world into an empty database (SEED_WORLD=1)."""
    import config as _cfg
    if getattr(_cfg, "SEED_DB", True):
        await load_world()
    else:
        print("[seed] Skipped (SEED_WORLD=0)")
    port = _cfg.SERVER_PORT
    print("=" * 60)
    print(f"  Yeeps {_cfg.REQUIRED_CLIENT_VERSION} self-host server is READY")
    print(f"  Listening on port {port}. Keep this window open while people play.")
    # Show the LAN address(es) to put in the APK's --server, so the host doesn't
    # have to go hunting for their IP.
    try:
        import socket
        ips = sorted({ai[4][0] for ai in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
                      if not ai[4][0].startswith("127.")})
        if not ips:
            s_ = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s_.connect(("8.8.8.8", 80)); ips = [s_.getsockname()[0]]; s_.close()
        for ip in ips:
            print(f"  Same Wi-Fi: build the APK with  --server http://{ip}:{port}")
    except Exception:
        pass
    print("  Over the internet: use a Cloudflare tunnel (see the README).")
    print("=" * 60)


# ─── Include routers ─────────────────────────────────────────────────

# Lambda routes (specific path pattern)
# player_editor router superseded by editor_admin_232 (fuller player endpoints)
app.include_router(lambda_router)


# ─── Catch-all route ─────────────────────────────────────────────────

async def _log_photon_auth(account_id: str, allowed: bool, request, extra: dict | None = None) -> None:
    """Record every Photon custom-auth call (both the game and any other Photon
    app pointed at this server), so we can compare Photon logins with who is
    actually online. Collection photon_auths, TTL 2 days."""
    try:
        from services.db import db as _db
        from services.ip_bans import client_ip as _cip
        col = _db["photon_auths"]
        await col.create_index("at", expireAfterSeconds=2 * 86400)
        # fileHashes is ~8.5 KB and identical per APK build: keep a 16-hex fingerprint
        # (photon_auths was 118 MB of the 512 MB Atlas quota storing it in full).
        import hashlib as _hl
        q = {}
        for k, v in request.query_params.items():
            if k.lower() in ("token", "summaryhash", "botkey"):
                continue
            if k.lower() == "filehashes":
                k, v = "fileHashesFp", _hl.sha256(v.encode()).hexdigest()[:16]
            q[k] = v[:200]
        await col.insert_one({"at": datetime.now(timezone.utc), "accountID": account_id,
                              "allowed": bool(allowed), "ip": _cip(request),
                              "query": q, **(extra or {})})
    except Exception as _e:
        print(f"[photon] auth log failed: {_e}")


async def _photon_auth_denied(account_id: str, request) -> str | None:
    """Return a deny MESSAGE if this connection must be rejected at Photon
    custom-auth, else None. Rejects a banned account / IP / name, a banned or
    unbound headset, and the "game closed" switch.

    Enforcing the build check HERE matters: a patched client can ignore the
    client-side login isolation (GetHasPermissionToJoinRoom), but it cannot join
    a Photon room if Photon's own auth webhook rejects it. The client reports its
    attestation `summaryHash` in the auth query (applicationVersion is a useless
    hardcoded "1.19.0" in every build); we deny on a POSITIVE mismatch against the
    current build's hash, and never when no hash has been seen (no false
    positives for not-yet-attested clients)."""
    from config import COVERT_DENY_MESSAGE, SHADOW_BAN_IDS, SHADOW_BAN_IPS
    try:
        from services.ip_bans import is_banned, client_ip
        ip = client_ip(request)
        # The game is closed (globals gameClosed, services/build_gate.py): no room joins.
        from services.build_gate import closed_message
        _closed = await closed_message()
        if _closed:
            return _closed
        # Covert targets first: a generic "can't connect" message, so they can't
        # tell they were banned or how. Env lists let you covert-ban without a DB
        # trace at all.
        if (account_id and account_id in SHADOW_BAN_IDS) or (ip and ip in SHADOW_BAN_IPS):
            print(f"[photon] shadow(list) DENIED {account_id} ip={ip}")
            return COVERT_DENY_MESSAGE
        if await is_banned(ip):
            return "You are banned."
        adoc = None
        if account_id and account_id != "o_0":
            adoc = await accounts.find_one(
                {"accountID": account_id},
                {"_id": 0, "adminBlocked": 1, "isPermabanned": 1, "shadowBanned": 1,
                 "displayName": 1, "oculusID": 1, "attestHash": 1, "clientBuildStatus": 1,
                 "clientBuildTag": 1, "discordID": 1})
            # Hidden per-account shadowban: no "banned" screen, just a dead connection.
            if adoc and adoc.get("shadowBanned"):
                print(f"[photon] shadow(flag) DENIED {account_id}")
                return COVERT_DENY_MESSAGE
            if adoc and (adoc.get("adminBlocked") or adoc.get("isPermabanned")):
                return "You are banned."
            # Headset key: 131b15+ adds dk=<key> to its Photon auth params, so a
            # room join must come from a headset bound to this account.
            from services import device_auth as _da
            _dk = str(request.query_params.get("dk") or getattr(request.state, "device_key", "") or "")
            _dok, _ = await _da.check_request(account_id, _dk, "photon join", ip,
                                              enforce=_da.photon_enforcing())
            if not _dok:
                return ("This account is locked to another headset. "
                        "Ask an admin to reset your headset lock.")
            # The game (131b14+) always sends its headset key and login binds it,
            # so a join with no key, or for an account with no headset bound yet,
            # is a script posing as that player (Oct 10 raider: "ima log in to
            # everyone's accounts").
            if _da.photon_enforcing():
                _bound = await _da._keys_of(account_id)
                if not _da.valid_key(_dk) or not _bound or _da.key_hash(_dk) not in _bound:
                    print(f"[photon] DENIED {account_id}: no matching bound headset key ip={ip}")
                    return ("This account is locked to another headset. "
                            "Ask an admin to reset your headset lock.")
            from services.device_bans import is_device_banned
            if await is_device_banned(_dk):
                print(f"[photon] banned headset DENIED {account_id} ip={ip}")
                return "You are banned."
            if _da.valid_key(_dk):
                _da._log(f"photon join {account_id} with headset key ok", every=600)
            if adoc:
                try:
                    from handlers.login import _is_banned_name
                    if _is_banned_name(adoc.get("displayName"), adoc.get("oculusID")):
                        return "You are banned."
                except Exception:
                    pass
    except Exception as e:
        print(f"[photon] auth check failed for {account_id}: {e}")
    return None


@app.api_route("/", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def catch_all(request: Request, path: str = ""):
    """
    Catch-all route that detects which AWS service the game client
    is trying to reach and dispatches accordingly.
    """
    host = request.headers.get("host", "")
    target = request.headers.get("x-amz-target", "")
    auth = request.headers.get("authorization", "")
    method = request.method

    print(f"\n{'='*60}")
    print(f"{method} /{path}")
    print(f"Host: {host}")

    # ── 1. Photon Auth: GET / with query string ──
    if method == "GET" and path == "" and str(request.query_params):
        q = request.query_params
        account_id = q.get("UserId") or q.get("userId") or q.get("accountID") or "o_0"
        # The client's integrity/attestation heartbeat arrives here too, carrying
        # summaryHash — a per-BUILD signature (the current build's libBNMExplorer.so
        # differs from the old one, so the hash differs). Unlike applicationVersion
        # (hardcoded "1.19.0" in every build), this reliably distinguishes the
        # current build from old ones. Record it so /online can flag stale builds.
        _summary = q.get("summaryHash")
        if _summary and account_id and account_id != "o_0":
            try:
                await accounts.update_one({"accountID": account_id},
                                          {"$set": {"attestHash": _summary}})
            except Exception as _e:
                print(f"[attest] record failed for {account_id}: {_e}")
        username = q.get("UserName") or q.get("userName") or q.get("username")
        if not username and account_id:
            account_doc = await accounts.find_one({"accountID": account_id}, {"_id": 0, "displayName": 1})
            if account_doc and isinstance(account_doc.get("displayName"), str) and account_doc["displayName"].strip():
                username = account_doc["displayName"].strip()
        if not username:
            username = account_id
        _deny = await _photon_auth_denied(account_id, request)
        await _log_photon_auth(account_id, not _deny, request, {"via": "GET"})
        if _deny:
            print(f"Service: Photon Auth DENIED ({_deny}) -> {account_id}")
            print(f"{'='*60}\n")
            return JSONResponse(content={"ResultCode": 2, "Message": _deny})
        payload = {"ResultCode": 1, "UserId": f"{account_id}$VR${username}"}
        print(f"Service: Photon Auth -> {payload}")
        print(f"{'='*60}\n")
        return JSONResponse(content=payload)

    # Parse body for POST/PUT requests
    raw = await request.body()
    raw_text = _safe_decode(raw)

    # ── 1b. Photon Auth: POST / (Photon Cloud's custom-auth webhook shape) ──
    if method == "POST" and path == "" and not target:
        try:
            body_peek = json.loads(raw) if raw else {}
        except Exception:
            body_peek = {}
        if isinstance(body_peek, dict):
            account_id = (
                request.query_params.get("UserId")
                or body_peek.get("UserId")
                or body_peek.get("userId")
                or body_peek.get("accountID")
            )
            if account_id:
                username = body_peek.get("UserName") or body_peek.get("userName") or body_peek.get("username")
                if not username:
                    account_doc = await accounts.find_one({"accountID": account_id}, {"_id": 0, "displayName": 1})
                    if account_doc and isinstance(account_doc.get("displayName"), str) and account_doc["displayName"].strip():
                        username = account_doc["displayName"].strip()
                if not username:
                    username = account_id
                _deny = await _photon_auth_denied(account_id, request)
                await _log_photon_auth(account_id, not _deny, request, {"via": "POST"})
                if _deny:
                    print(f"Service: Photon Auth (POST) DENIED ({_deny}) -> {account_id}")
                    print(f"{'='*60}\n")
                    return JSONResponse(content={"ResultCode": 2, "Message": _deny})
                payload = {"ResultCode": 1, "UserId": f"{account_id}$VR${username}"}
                print(f"Service: Photon Auth (POST) -> {payload}")
                print(f"{'='*60}\n")
                return JSONResponse(content=payload)

    # Replay exact responses captured in server.log when possible.
    replay = lookup_replay(method, f"/{path}" if path else "/", raw_text)
    if replay is not None:
        print("Service: Replay | source: server.log")
        print(f"-> {replay['status_code']} {replay['content_type']}")
        print(f"{'='*60}\n")
        return Response(
            content=replay["response_body"],
            status_code=replay["status_code"],
            media_type=replay["content_type"],
        )

    try:
        body = json.loads(raw) if raw else {}
    except Exception:
        body = {}

    # ── 2. Lambda: path matches invocation pattern ──
    m = re.match(r"^2015-03-31/functions/([^/]+)/invocations/?$", path)
    if m or "/lambda/" in auth or host.startswith("lambda"):
        fn = m.group(1) if m else (path or "unknown")
        print(f"Service: Lambda | function: {fn}")
        # Forward to the lambda router handler directly
        from routers.lambda_router import lambda_invoke
        return await lambda_invoke(fn, request)

    # ── 3. DynamoDB: X-Amz-Target header present ──
    if (target and target.startswith("DynamoDB_")) or "/dynamodb/" in auth or host.startswith("dynamodb"):
        op = target.rsplit(".", 1)[-1] if "." in target else "?"
        tbl = body.get("TableName", "")
        if not tbl and isinstance(body, dict):
            ri = body.get("RequestItems", {})
            if isinstance(ri, dict) and ri:
                tbl = ",".join(ri.keys())
        print(f"Service: DynamoDB | op: {op} | table: {tbl}")

        # TEMP DIAGNOSTIC: dump request/response for the tables involved in
        # the main-instance / build-block decision, so the exact fields the
        # client reads and writes are visible in the logs.
        # Opt-in (DIAG_DYNAMO=1): ~5000 heartbeat dumps/hour otherwise fill the journal.
        _diag = _DIAG_DYNAMO and ("G2_Rooms" in tbl or "G2_PlayerStatuses" in tbl)
        if _diag:
            print(f"[DIAG] REQ {op} {tbl}: {json.dumps(body)[:1200]}")

        from services.dynamo_ops import dispatch_dynamo
        from services.ip_bans import client_ip as _dyn_ip
        payload_text, headers = await dispatch_dynamo(target, body, _dyn_ip(request),
                                                      getattr(request.state, "device_key", ""))
        if _diag:
            print(f"[DIAG] RES {op} {tbl}: {payload_text[:1200]}")
        print(f"-> 200 application/x-amz-json-1.0")
        print(f"{'='*60}\n")
        return Response(
            content=payload_text,
            media_type="application/x-amz-json-1.0",
            headers=headers,
        )

    # ── 4. Direct API: path matches known handler ──
    # Direct HTTPS routes like /questLogIntoAccount go through API Gateway's
    # Lambda proxy integration, which UNWRAPS the Lambda's
    # {"statusCode","headers","body"} envelope - the client receives only
    # the inner body ({"data": {...}}) as the HTTP response. The envelope is
    # only seen by AWS SDK Lambda.Invoke callers (step 2 above).
    from routers.lambda_router import HANDLER_TABLE
    fn_lower = path.lower()
    try:
        from services.ip_bans import client_ip as _cip
        if isinstance(body, dict):
            body["_clientIP"] = _cip(request)
            body["_clientBuildTag"] = getattr(request.state, "client_build_tag", "")
            body["_deviceKey"] = getattr(request.state, "device_key", "")
            body["_fn"] = path.rstrip("/").rsplit("/", 1)[-1]
    except Exception:
        pass
    for match_str, handler in HANDLER_TABLE:
        if match_str in fn_lower:
            print(f"Service: Direct API | route: {path} | handler: {handler.__name__}")
            if handler.__name__ != "handle_login" and isinstance(body, dict):
                from services import device_auth
                _ok, _ = await device_auth.check_request(body.get("accountID"), body.get("_deviceKey", ""),
                                                         path, body.get("_clientIP", ""))
                if not _ok:
                    return JSONResponse(content={"data": {"success": 0, "error": "NOT_AUTHORIZED"}})
            result = await handler(body)
            _err = (result.get("_lambda_error") or (result.get("data") or {}).get("_lambda_error")) \
                if isinstance(result, dict) else None
            if _err:
                return JSONResponse(status_code=400, content={
                    "statusCode": 400, "err_type": _err, "body": {"err_type": _err}})
            if isinstance(result, dict) and "data" not in result:
                result = {"data": result}
            print(f"-> 200 application/json")
            print(f"{'='*60}\n")
            return JSONResponse(content=result)

    # ── 5. Unknown — the 1.19.0 client treats an empty 200 as "nothing to do"
    # (an error status makes its SDK retry, e.g. the Talsec log posts).
    if _IS_V2:
        # 2.x: mimic real API Gateway for an unmatched route; its AWS SDK treats
        # this canonical shape as "this is really API Gateway, keep going".
        print(f"Service: (unknown) — returning API Gateway-style 403")
        print(f"{'='*60}\n")
        return JSONResponse(content={"message": "Missing Authentication Token"}, status_code=403)
    print(f"Service: (unknown) — returning empty 200")
    print(f"{'='*60}\n")
    return JSONResponse(content={})


# ─── Entry point ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    from config import SERVER_PORT
    uvicorn.run("main:app", host="0.0.0.0", port=SERVER_PORT, reload=True)
