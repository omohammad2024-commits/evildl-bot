"""Cookie jar management for platforms that wall anonymous datacenter traffic.

Why this exists: YouTube and Instagram both serve bot challenges to this
server's IP regardless of client, UA, PO token, or Tor exit (all verified
empirically). The only remaining lever that works from userspace is presenting
a logged-in session, i.e. a Netscape-format cookies.txt.

Design notes:
- One file per platform under COOKIES_DIR, so a banned YouTube account cannot
  take Instagram down with it.
- Files are validated before being accepted: wrong format silently produces
  "no cookies" behaviour in yt-dlp, which is the worst failure mode because it
  looks like the cookies were applied.
- Freshness is tracked so an admin can be told "your YouTube cookies expired"
  instead of seeing generic download failures.
"""
import logging
import os
import re
import time
from typing import Dict, List, Optional, Tuple

from bot import config

logger = logging.getLogger(__name__)

# Cookies that must be present for the session to actually be logged in.
REQUIRED: Dict[str, List[str]] = {
    "youtube": ["SID", "HSID", "SSID"],
    "instagram": ["sessionid"],
    "tiktok": ["sessionid"],
    "twitter": ["auth_token"],
}

# First-party YouTube/Google session cookies. Any ONE of these proves the jar
# was exported from a signed-in youtube.com session and is an acceptable stand-in
# for the classic SID/HSID/SSID trio.
#
# Deliberately excludes every ``__Secure-3P*`` name: those are the THIRD-PARTY
# variants Google sets for embedded contexts. A jar containing only 3P cookies
# looks plausible (right domain, long expiry, ~15 cookies) but youtube.com treats
# it as anonymous — verified by fetching youtube.com with such a jar and finding
# ``LOGGED_IN false`` in the page.
YT_FIRST_PARTY_AUTH = frozenset({
    "SID",
    "__Secure-1PSID",
    "__Secure-1PAPISID",
    "LOGIN_INFO",
})

# Which domains we expect to see, used to reject a file pasted for the wrong site.
DOMAINS: Dict[str, Tuple[str, ...]] = {
    "youtube": ("youtube.com", "google.com"),
    "instagram": ("instagram.com",),
    "tiktok": ("tiktok.com",),
    "twitter": ("twitter.com", "x.com"),
}

HEADER = "# Netscape HTTP Cookie File\n"

# Short-lived cookies that Google/Meta rotate constantly. They expire within
# minutes-to-hours of an export, so they must NOT decide whether the jar is
# still usable — only the durable auth cookies do. Judging by the soonest
# expiry across all cookies made a fresh YouTube login look "expired" almost
# immediately, which silently turned YouTube (and Spotify, which bridges
# through it) off.
EPHEMERAL = frozenset({
    "__Secure-1PSIDTS",
    "__Secure-3PSIDTS",
    "__Secure-1PSIDCC",
    "__Secure-3PSIDCC",
    "__Secure-ROLLOUT_TOKEN",
    "VISITOR_INFO1_LIVE",
    "VISITOR_PRIVACY_METADATA",
    "DEVICE_INFO",
    "GPS",
    "YSC",
    "CONSISTENCY",
    "csrftoken",
    "rur",
    "mid",
})

# The only platform names that may become a cookie filename. Anything else is
# rejected by path_for() so untrusted input (e.g. a web dashboard path param)
# can never traverse out of the cookies directory.
KNOWN_PLATFORMS = frozenset({"youtube", "instagram", "tiktok", "twitter"})


def cookies_dir() -> str:
    path = getattr(config, "COOKIES_DIR", "") or os.path.join(
        os.path.dirname(os.path.abspath(config.__file__)), "..", "data", "cookies")
    path = os.path.abspath(path)
    os.makedirs(path, exist_ok=True)
    return path


def path_for(platform: str) -> str:
    """Resolve the on-disk jar path for a platform, safely.

    ``platform`` may arrive from an HTTP path parameter (web dashboard), so it
    must never be interpolated into a filesystem path unchecked — otherwise a
    value like ``../../../home/user/.ssh/authorized_keys`` would let a caller
    write/delete arbitrary ``.txt`` files. We hard-restrict it to a known set
    of lowercase alphanumeric platform names.
    """
    if platform not in KNOWN_PLATFORMS:
        raise ValueError(f"unknown cookie platform: {platform!r}")
    name = f"{platform}.txt"
    base = cookies_dir()
    dest = os.path.abspath(os.path.join(base, name))
    # Defence in depth: the resolved path must stay inside the cookies dir.
    if os.path.dirname(dest) != base:
        raise ValueError(f"illegal cookie platform: {platform!r}")
    return dest


def parse(text: str) -> List[Dict[str, str]]:
    """Parse Netscape cookie lines. Tolerates the '#HttpOnly_' prefix."""
    out: List[Dict[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            if not line.startswith("#HttpOnly_"):
                continue
            line = line[len("#HttpOnly_"):]
        parts = line.split("\t")
        if len(parts) < 7:
            # Some exporters use spaces; fall back to whitespace splitting.
            parts = re.split(r"\s+", line)
            if len(parts) < 7:
                continue
        out.append({
            "domain": parts[0].lstrip("."),
            "expires": parts[4],
            "name": parts[5],
            "value": "\t".join(parts[6:]),
        })
    return out


def validate(text: str, platform: str) -> Tuple[bool, str, Dict[str, object]]:
    """Check a cookie file before trusting it.

    Returns (ok, human_reason, details). ``details`` carries counts and the
    soonest expiry so callers can warn ahead of time.
    """
    cookies = parse(text)
    if not cookies:
        return False, "no_cookies", {}

    expected = DOMAINS.get(platform, ())
    if expected:
        matching = [c for c in cookies
                    if any(d in c["domain"] for d in expected)]
        if not matching:
            found = sorted({c["domain"] for c in cookies})[:3]
            return False, "wrong_domain", {"found": found}
        cookies = matching

    names = {c["name"] for c in cookies}
    missing = [n for n in REQUIRED.get(platform, []) if n not in names]
    # YouTube accepts either the classic SID trio or the newer __Secure-* set.
    #
    # CAREFUL: the accepted substitute must be a FIRST-PARTY auth cookie.
    # A previous version accepted any name matching ``__Secure-*PSID*``, which
    # let a jar containing only ``__Secure-3PSID`` pass. The ``3P`` cookies are
    # the *third-party* variants: Google sends them to embedded players, and a
    # jar holding only those is anonymous as far as youtube.com is concerned.
    # The result was the worst possible failure mode — /cookies reported a
    # healthy green jar with 179 days left, while every YouTube download died
    # with "Sign in to confirm you're not a bot" and nothing pointed at the
    # cookies. Require a real first-party session cookie instead.
    if platform == "youtube" and missing:
        if names & YT_FIRST_PARTY_AUTH:
            missing = []

    now = time.time()
    # Google rotates a handful of short-lived cookies (``__Secure-1PSIDTS`` is
    # refreshed roughly hourly, the visitor/device ones daily). Judging the jar
    # by the SOONEST expiry therefore marks a perfectly good login as "expired"
    # within an hour of export, which silently disabled YouTube downloads. Only
    # the durable auth cookies decide validity.
    expiries = []
    for c in cookies:
        if c["name"] in EPHEMERAL:
            continue
        try:
            exp = float(c["expires"])
        except ValueError:
            continue
        if exp > 0:
            expiries.append(exp)
    soonest = min(expiries) if expiries else 0
    expired = bool(soonest and soonest < now)

    details = {
        "count": len(cookies),
        "names": sorted(names)[:12],
        "soonest_expiry": int(soonest),
        "days_left": int((soonest - now) / 86400) if soonest else 0,
    }
    if missing:
        details["missing"] = missing
        return False, "missing_session", details
    if expired:
        return False, "expired", details
    return True, "ok", details


def save(text: str, platform: str) -> Tuple[bool, str, Dict[str, object]]:
    """Validate then persist. Never overwrites a good file with a bad one."""
    ok, reason, details = validate(text, platform)
    if not ok:
        return False, reason, details

    body = text if text.startswith("# Netscape") else HEADER + text
    dest = path_for(platform)
    tmp = dest + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(body if body.endswith("\n") else body + "\n")
    os.replace(tmp, dest)
    os.chmod(dest, 0o600)  # session cookies are credentials
    logger.info("cookies stored for %s (%s cookies)", platform, details.get("count"))
    return True, "ok", details


def remove(platform: str) -> bool:
    try:
        os.remove(path_for(platform))
        return True
    except (OSError, ValueError):
        return False


def get(platform: str) -> Optional[str]:
    """Path to a usable cookie file, or None. Never returns a stale file."""
    try:
        p = path_for(platform)
    except ValueError:
        return None
    if not os.path.exists(p):
        # A single global cookies file remains supported as a fallback.
        return config.COOKIES_FILE or None
    try:
        with open(p, encoding="utf-8") as fh:
            ok, _reason, _d = validate(fh.read(), platform)
    except OSError:
        return None
    if not ok:
        logger.warning("cookie file for %s is no longer valid", platform)
        return None
    return p


def cookie_header(platform: str) -> str:
    """Return a 'name=value; ...' Cookie header for httpx-based scrapers.

    yt-dlp reads the jar file directly, but the embed/GraphQL fallbacks use
    httpx and need the cookies inline. Empty string when no valid jar exists.
    """
    jar = get(platform)
    if not jar:
        return ""
    try:
        with open(jar, encoding="utf-8") as fh:
            pairs = [(c["name"], c["value"]) for c in parse(fh.read()) if c.get("name")]
    except OSError:
        return ""
    return "; ".join(f"{n}={v}" for n, v in pairs)


def status() -> Dict[str, Dict[str, object]]:
    """Per-platform cookie state for the admin panel."""
    out: Dict[str, Dict[str, object]] = {}
    for platform in ("youtube", "instagram", "tiktok", "twitter"):
        p = path_for(platform)
        if not os.path.exists(p):
            out[platform] = {"present": False}
            continue
        try:
            with open(p, encoding="utf-8") as fh:
                text = fh.read()
            ok, reason, details = validate(text, platform)
        except OSError as exc:
            out[platform] = {"present": True, "ok": False, "reason": str(exc)[:40]}
            continue
        out[platform] = {
            "present": True, "ok": ok, "reason": reason,
            "count": details.get("count", 0),
            "days_left": details.get("days_left", 0),
            "mtime": int(os.path.getmtime(p)),
        }
    return out
