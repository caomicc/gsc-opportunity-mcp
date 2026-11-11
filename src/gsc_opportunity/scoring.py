"""Revenue-weighted opportunity scoring.

All monetary inputs are CONFIG ESTIMATES used to rank opportunities, not
guarantees. Set the actual RPM / affiliate rates in the local .env once you have
real numbers. The math is transparent so the ranking is explainable.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)


def default_site() -> str:
    """Configured Search Console property, or "" when the user hasn't set one.

    Deliberately empty by default: a hardcoded property would silently score the
    wrong site, and shipping someone else's domain in a public repo is confusing.
    """
    return (os.environ.get("CREATOR_SEO_SITE") or "").strip()


def default_days() -> int:
    return int(os.environ.get("CREATOR_SEO_DAYS", "28"))


def rpm() -> float:
    """Display-ad revenue per 1000 pageviews (estimate until a real RPM exists)."""
    return float(os.environ.get("CREATOR_SEO_SITE_RPM", "5") or 5)


def affiliate_rates() -> dict[str, float]:
    """Map of query/page substring -> $ per 1000 clicks from that term."""
    raw = os.environ.get("CREATOR_SEO_AFFILIATE_CATEGORIES", "{}")
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    out: dict[str, float] = {}
    for k, v in data.items():
        try:
            out[str(k).lower()] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def revenue_rate_for(text: str) -> float:
    """$ per 1000 clicks for a query/page, ad RPM + any affiliate match."""
    t = text.lower()
    for key, rate in affiliate_rates().items():
        if key and key in t:
            return round(rpm() + rate, 4)
    return round(rpm(), 4)


def affiliate_premium(text: str) -> float:
    """Extra $ per 1000 clicks from a matching affiliate category (0 if none)."""
    t = text.lower()
    for key, rate in affiliate_rates().items():
        if key and key in t:
            return round(rate, 4)
    return 0.0


def incremental_clicks(impressions: float, ctr: float, target_ctr: float = 0.10) -> float:
    """Estimated extra clicks if a row lifts CTR toward a top-3-like rate."""
    return max(0.0, impressions * max(0.0, target_ctr - ctr))


def opportunity_value(
    impressions: float,
    ctr: float,
    text: str,
    rpm_override: float | None = None,
    target_ctr: float = 0.10,
) -> float:
    """Estimated $ value of the projected incremental clicks for a row.

    rpm_override lets callers use REAL per-page RPM (e.g. from Raptive) instead of
    the flat CREATOR_SEO_SITE_RPM estimate.
    """
    ic = incremental_clicks(impressions, ctr, target_ctr)
    base = rpm_override if rpm_override is not None else rpm()
    rate = base + affiliate_premium(text)
    return round(ic / 1000.0 * rate, 2)


def rank_opportunities(rows: list[dict]) -> list[dict]:
    """Attach rev_potential + projected_clicks and sort descending by value."""
    scored = []
    for r in rows:
        impressions = float(r.get("impressions", 0))
        ctr = float(r.get("ctr", 0))
        key = r["keys"][0]
        r["rev_potential"] = opportunity_value(impressions, ctr, key)
        r["projected_clicks"] = round(incremental_clicks(impressions, ctr), 1)
        scored.append(r)
    return sorted(scored, key=lambda r: r["rev_potential"], reverse=True)