"""Raptive Creator API client (read-only).

Credentials live in the local .env (RAPTIVE_CLIENT_ID / RAPTIVE_CLIENT_SECRET),
minted from dashboard -> Account Settings -> Raptive Creator API. NEVER in chat.
Headless-safe: OAuth client_credentials, token cached until near expiry.
Eligibility: Insider / Platinum / Platinum Elite / Luminary tiers only.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BASE = "https://publisher-api.raptive.com"
API = f"{BASE}/creator-api/v1"

_client_id = os.environ.get("RAPTIVE_CLIENT_ID", "")
_client_secret = os.environ.get("RAPTIVE_CLIENT_SECRET", "")

_token_cache: dict[str, Any] = {}


class RaptiveError(Exception):
    pass


def configured() -> bool:
    return bool(_client_id and _client_secret)


def _token() -> str:
    now = time.time()
    if _token_cache.get("exp", 0) > now + 60:
        return _token_cache["access_token"]
    if not configured():
        raise RaptiveError(
            "RAPTIVE_CLIENT_ID / RAPTIVE_CLIENT_SECRET not set in .env. "
            "Mint a token in Raptive dashboard -> Account Settings -> Raptive Creator API."
        )
    r = httpx.post(
        f"{BASE}/oauth/token",
        auth=(_client_id, _client_secret),
        data={"grant_type": "client_credentials"},
        timeout=30,
    )
    if r.status_code >= 400:
        raise RaptiveError(f"oauth token {r.status_code}: {r.text[:200]}")
    body = r.json()
    _token_cache["access_token"] = body["access_token"]
    _token_cache["exp"] = now + int(body.get("expires_in", 3600))
    return body["access_token"]


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    r = httpx.get(
        f"{API}{path}",
        headers={"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"},
        params=params,
        timeout=60,
    )
    if r.status_code >= 400:
        raise RaptiveError(f"GET {path} {r.status_code}: {r.text[:300]}")
    return r.json()


def sites() -> list[dict[str, Any]]:
    return _get("/sites").get("data", [])


def _resolve_site_id(site_id: str | None) -> str:
    if site_id:
        return str(site_id)
    found = sites()
    if not found:
        raise RaptiveError("No Raptive sites available to this credential.")
    return str(found[0]["id"])


def date_bounds(site_id: str) -> dict[str, Any]:
    return _get(f"/sites/{site_id}/date-bounds")


def window(site_id: str, days: int = 30) -> tuple[str, str]:
    """Best-effort (start, end) using Raptive's own last-N-days bounds; fallback safe."""
    try:
        db = date_bounds(site_id)
        suite = (db.get("earningsDateBounds") or {}).get("lastThirtyDays") or {}
        rng = suite.get("range") or {}
        start = rng.get("startDate")
        end = rng.get("endDate")
        if start and end:
            return start, end
    except Exception:
        pass
    from datetime import date, timedelta

    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


def earnings(site_id: str, start: str, end: str) -> list[dict[str, Any]]:
    return _get(f"/sites/{site_id}/earnings", {"startDate": start, "endDate": end}).get("data", [])


def pages_performance(site_id: str, start: str, end: str, size: int = 500, page: int = 1) -> list[dict[str, Any]]:
    return _get(
        f"/sites/{site_id}/pages/performance",
        {"startDate": start, "endDate": end, "page[size]": size, "page[number]": page},
    ).get("data", [])


def _norm(url: str) -> str:
    u = url.strip().lower()
    for p in ("https://www.", "http://www.", "https://", "http://"):
        if u.startswith(p):
            u = u[len(p) :]
            break
    return u.rstrip("/")


def path_of(url: str) -> str:
    """Path component of a URL (Raptive reports pageUrl as a path like '/pokemon')."""
    p = urlparse(url).path
    return p if p else "/"


def host_of(url: str) -> str:
    """Normalized hostname (no scheme, no www) for cross-site matching."""
    h = urlparse(url).hostname or ""
    return h.removeprefix("www.").lower()


_site_host_cache: str | None = None


def site_host() -> str:
    """Hostname of the Raptive site the credential can read (e.g. 'example.com')."""
    global _site_host_cache
    if _site_host_cache is None:
        found = sites()
        _site_host_cache = host_of(found[0]["url"]) if found else ""
    return _site_host_cache


def match_rpm(path: str, rpm_map: dict[str, float]) -> float | None:
    """Longest Raptive page-path prefix that matches this path (or None).

    '/pokemon/noctowl' -> '/pokemon' RPM. '/' only matches the exact home path,
    so unmatched subpages fall back to the flat CREATOR_SEO_SITE_RPM instead of
    inheriting the home page's RPM.
    """
    best: float | None = None
    best_len = -1
    for key, rpm in rpm_map.items():
        k = key if key else "/"
        if path == k or (k != "/" and path.startswith(k.rstrip("/") + "/")):
            if len(k) > best_len:
                best = rpm
                best_len = len(k)
    return best


def page_rpm_map(site_id: str | None = None, days: int = 30, min_pageviews: int = 1000) -> dict[str, float]:
    """page_path -> real Raptive RPM for the window. Empty dict if unavailable.

    Per-page RPM is statistically noisy on low-traffic pages (a few high-CPM
    impressions can spike RPM to 50-90+). Only pages with >= min_pageviews are
    trusted; everything else falls back to the flat CREATOR_SEO_SITE_RPM.
    """
    if not configured():
        return {}
    sid = _resolve_site_id(site_id)
    s, e = window(sid, days)
    out: dict[str, float] = {}
    page = 1
    while page <= 10:
        rows = pages_performance(sid, s, e, size=500, page=page)
        if not rows:
            break
        for row in rows:
            url = row.get("pageUrl")
            rpm = row.get("rpm")
            pv = float(row.get("pageviews", 0) or 0)
            if url and rpm is not None and pv >= min_pageviews:
                out[path_of(url)] = float(rpm)
        if len(rows) < 500:
            break
        page += 1
    return out


def summarize(site_id: str | None = None, days: int = 30) -> dict[str, Any]:
    sid = _resolve_site_id(site_id)
    s, e = window(sid, days)
    rows = earnings(sid, s, e)
    tot_earn = sum(float(r.get("earnings", 0) or 0) for r in rows)
    tot_pv = sum(float(r.get("pageviews", 0) or 0) for r in rows)
    rpm = (tot_earn / tot_pv * 1000.0) if tot_pv else 0.0
    return {
        "site_id": sid,
        "start": s,
        "end": e,
        "revenue_usd": round(tot_earn, 2),
        "pageviews": int(tot_pv),
        "rpm": round(rpm, 2),
    }