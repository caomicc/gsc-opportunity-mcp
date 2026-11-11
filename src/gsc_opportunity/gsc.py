"""Search Console read-only client using local Application Default Credentials.

Headless-safe: ADC auto-refreshes, no interactive re-auth, no separate
credentials.json. Scope is strictly webmasters.readonly. Never prints tokens.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import httpx
from google.auth import default
from google.auth.transport.requests import Request as AuthRequest

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
_API = "https://www.googleapis.com/webmasters/v3"
_UA = "gsc-opportunity-mcp/0.1"

_creds = None
_project = None


class GSCError(Exception):
    pass


def _ensure_creds():
    global _creds, _project
    if _creds is None:
        _creds, _project = default(scopes=SCOPES)
    if not _creds.valid:
        try:
            _creds.refresh(AuthRequest())
        except Exception:
            # The ADC file may have been re-authed (new refresh token) since this
            # process started, which leaves the cached credential holding a dead
            # token. Drop it and reload from disk, then retry once.
            _creds, _project = default(scopes=SCOPES)
            if not _creds.valid:
                _creds.refresh(AuthRequest())
    return _creds


def _token() -> str:
    return _ensure_creds().token


def _headers() -> dict[str, str]:
    _ensure_creds()
    h = {
        "Authorization": f"Bearer {_creds.token}",
        "Content-Type": "application/json",
        "User-Agent": _UA,
    }
    # Attribute API usage to the quota project (where the API is enabled),
    # rather than the OAuth client's project. Required now that the default
    # ADC client is blocked for non-cloud-platform scopes.
    quota_project = getattr(_creds, "quota_project_id", None)
    if quota_project:
        h["X-Goog-User-Project"] = quota_project
    return h


def _site(site: str) -> str:
    return site.replace(":", "%3A")


def token_status() -> str:
    """Return a short, non-secret auth status for health checks (never hangs)."""
    try:
        token = _token()
        if not token:
            return "missing"
        return "valid"
    except Exception as e:
        return f"invalid: {type(e).__name__}"


def list_sites() -> list[dict[str, Any]]:
    r = httpx.get(f"{_API}/sites", headers=_headers(), timeout=30)
    if r.status_code >= 400:
        raise GSCError(f"list_sites {r.status_code}: {r.text[:300]}")
    return r.json().get("siteEntry", [])


def search_analytics(
    site: str,
    start_date: str,
    end_date: str,
    dimensions: list[str],
    row_limit: int = 25000,
    max_rows: int = 60000,
    dimension_filter: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Query Search Analytics with pagination. Returns raw rows."""
    body: dict[str, Any] = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": dimensions,
        "rowLimit": row_limit,
        "type": "web",
        "dataState": "final",
    }
    if dimension_filter:
        body["dimensionFilterGroups"] = [
            {
                "filters": [
                    {
                        "dimension": dimension_filter["dimension"],
                        "operator": dimension_filter.get("operator", "equals"),
                        "expression": dimension_filter["expression"],
                    }
                ]
            }
        ]

    rows: list[dict[str, Any]] = []
    start_row = 0
    while start_row < max_rows:
        body["startRow"] = start_row
        r = httpx.post(
            f"{_API}/sites/{_site(site)}/searchAnalytics/query",
            headers=_headers(),
            json=body,
            timeout=60,
        )
        if r.status_code >= 400:
            raise GSCError(f"query {start_date}..{end_date} {r.status_code}: {r.text[:300]}")
        batch = r.json().get("rows", [])
        rows.extend(batch)
        if len(batch) < row_limit:
            break
        start_row += len(batch)
    return rows


def iso(d: date) -> str:
    return d.isoformat()


def get_latest_populated_date(site: str, lookback_days: int = 10) -> date:
    """Anchor on the most recent day with clicks > 0 (Search Console data lags)."""
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=lookback_days)
    rows = search_analytics(site, iso(start), iso(end), ["date"], row_limit=lookback_days + 2)
    best = None
    for row in rows:
        if row.get("clicks", 0) > 0:
            d = row["keys"][0]
            if best is None or d > best:
                best = d
    return date.fromisoformat(best) if best else end


def perf_rows_for(site: str, start_date: str, end_date: str, dimension: str) -> dict[str, Any]:
    rows = search_analytics(site, start_date, end_date, [dimension])
    return rows