"""Standalone FastAPI admin dashboard for the Downloader bot.

This is a *separate process* from the bot. It shares state only through the
bot's SQLite database and the ``settings`` table, so it can be started, stopped
and restarted independently without touching the running bot. Actions that need
the Telegram API (broadcast, channel verification) create their own short-lived
``telegram.Bot`` from the same token.

Security model
--------------
* One password (``WEB_PASSWORD``). No account system — this is a single-admin
  tool. Login issues a signed, HttpOnly session cookie (itsdangerous), so the
  password is never stored client-side and the cookie cannot be forged without
  ``WEB_SECRET``.
* Login is rate-limited per client IP to blunt brute force.
* Every API route except /login and /health requires a valid session.
* If ``WEB_PASSWORD`` is empty the dashboard refuses to serve anything except a
  setup notice — it never runs wide open by accident.
"""
import asyncio
import io
import logging
import os
import time
from typing import Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer

from bot import config
from bot.database import db

logger = logging.getLogger("web")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

SESSION_COOKIE = "dlweb_session"
SESSION_MAX_AGE = 7 * 24 * 3600  # a week
_serializer = URLSafeTimedSerializer(config.WEB_SECRET, salt="dlweb-session")

app = FastAPI(title="Downloader Dashboard", docs_url=None, redoc_url=None, openapi_url=None)

# ── login brute-force guard ───────────────────────────────────────────
_login_fails: dict = {}  # ip -> (count, first_ts)
LOGIN_WINDOW = 300
LOGIN_MAX = 8


def _client_ip(request: Request) -> str:
    """Best-effort client IP for rate limiting.

    We only trust ``X-Forwarded-For`` when ``WEB_TRUST_PROXY`` is set (i.e. a
    known reverse proxy / tunnel really is in front of us). Otherwise an
    attacker could send a unique XFF per request and sail past the brute-force
    guard, so we fall back to the real socket peer.
    """
    if getattr(config, "WEB_TRUST_PROXY", False):
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _login_blocked(ip: str) -> bool:
    rec = _login_fails.get(ip)
    if not rec:
        return False
    count, first = rec
    if time.time() - first > LOGIN_WINDOW:
        _login_fails.pop(ip, None)
        return False
    return count >= LOGIN_MAX


def _login_fail(ip: str) -> None:
    count, first = _login_fails.get(ip, (0, time.time()))
    if time.time() - first > LOGIN_WINDOW:
        count, first = 0, time.time()
    _login_fails[ip] = (count + 1, first)


# ── session helpers ───────────────────────────────────────────────────
def _issue_session() -> str:
    return _serializer.dumps({"ok": True, "t": int(time.time())})


def _valid_session(token: Optional[str]) -> bool:
    if not token:
        return False
    try:
        _serializer.loads(token, max_age=SESSION_MAX_AGE)
        return True
    except (BadSignature, Exception):
        return False


async def require_auth(request: Request) -> bool:
    """Dependency: 401 unless a valid session cookie is present."""
    if not config.WEB_PASSWORD:
        raise HTTPException(status_code=503, detail="dashboard not configured")
    if not _valid_session(request.cookies.get(SESSION_COOKIE)):
        raise HTTPException(status_code=401, detail="unauthorized")
    return True


# ── lifecycle ─────────────────────────────────────────────────────────
@app.on_event("startup")
async def _startup() -> None:
    await db.connect()
    logger.info("Dashboard DB ready")


@app.on_event("shutdown")
async def _shutdown() -> None:
    await db.close()


# ── pages ─────────────────────────────────────────────────────────────
def _read_template(name: str) -> str:
    with open(os.path.join(TEMPLATES_DIR, name), encoding="utf-8") as fh:
        return fh.read()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not config.WEB_PASSWORD:
        return HTMLResponse(
            "<h1>Dashboard not configured</h1><p>Set <code>WEB_PASSWORD</code> "
            "in the bot's .env and restart the web service.</p>", status_code=503)
    if not _valid_session(request.cookies.get(SESSION_COOKIE)):
        return RedirectResponse("/login", status_code=302)
    return HTMLResponse(_read_template("dashboard.html"))


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if _valid_session(request.cookies.get(SESSION_COOKIE)):
        return RedirectResponse("/", status_code=302)
    return HTMLResponse(_read_template("login.html"))


@app.post("/login")
async def login_submit(request: Request, password: str = Form(...)):
    ip = _client_ip(request)
    if _login_blocked(ip):
        return JSONResponse({"ok": False, "error": "too many attempts, wait a few minutes"},
                            status_code=429)
    if not config.WEB_PASSWORD or password != config.WEB_PASSWORD:
        _login_fail(ip)
        return JSONResponse({"ok": False, "error": "wrong password"}, status_code=401)
    _login_fails.pop(ip, None)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        SESSION_COOKIE, _issue_session(), max_age=SESSION_MAX_AGE,
        httponly=True, samesite="lax",
        # The dashboard is meant to be reached over HTTPS (Cloudflare tunnel),
        # so mark the session cookie Secure unless explicitly serving plain
        # HTTP locally. Prevents the cookie leaking on an accidental HTTP hop.
        secure=getattr(config, "WEB_COOKIE_SECURE", True),
    )
    return resp


@app.get("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.get("/health", response_class=PlainTextResponse)
async def health():
    return "ok"


# ── PWA: service worker must be served from root scope to control "/" ──
@app.get("/sw.js")
async def service_worker():
    """Serve the service worker from the site root.

    A service worker can only control pages at or below its own URL path. The
    file physically lives in /static, but it must be *served* from "/" so its
    default scope covers the whole dashboard.
    """
    path = os.path.join(STATIC_DIR, "sw.js")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="not found")
    with open(path, encoding="utf-8") as fh:
        body = fh.read()
    return PlainTextResponse(
        body, media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


# Import the API router last so it can use the helpers above.
from web import api as _api  # noqa: E402

_api.register(app, require_auth)

if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
