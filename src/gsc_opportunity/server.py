"""GSC Opportunity MCP server.

Self-hosted, read-only Google Search Console opportunity scorer. Auth via local
Application Default Credentials (google.auth) — headless-safe, no interactive
re-auth. Scope: webmasters.readonly. Revenue ranking is config-estimate driven.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastmcp import FastMCP

from . import gsc, raptive, scoring

load_dotenv(Path(__file__).parent / ".env")

mcp = FastMCP("gsc-opportunity")

WINDOW_FLOOR = 3  # minimum days so period/prior windows don't collide


def _resolve_site(site: str | None) -> str:
    """Resolve the property to analyse, or explain exactly what's missing."""
    resolved = (site or "").strip() or scoring.default_site()
    if not resolved:
        raise ValueError(
            "No Search Console property configured. Pass `site` "
            "(e.g. 'sc-domain:example.com') or set CREATOR_SEO_SITE in .env."
        )
    return resolved


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _windows(site: str, days: int) -> tuple[tuple[str, str], tuple[str, str] | None]:
    from datetime import date, timedelta

    end = gsc.get_latest_populated_date(site)
    days = max(days, WINDOW_FLOOR)
    start = end - timedelta(days=days - 1)
    cur = (gsc.iso(start), gsc.iso(end))
    prior_start = start - timedelta(days=days)
    prior_end = start - timedelta(days=1)
    prior = (gsc.iso(prior_start), gsc.iso(prior_end))
    return cur, prior


def _simple(rows: list[dict], keys: int = 1) -> list[dict]:
    """Flatten keys[0] and round floats for JSON-safety."""
    out = []
    for r in rows:
        item = {
            "key": "/".join(r["keys"][:keys]),
            "clicks": int(r.get("clicks", 0)),
            "impressions": int(r.get("impressions", 0)),
            "ctr": round(float(r.get("ctr", 0)), 4),
            "position": round(float(r.get("position", 0)), 2),
        }
        if keys > 1:
            item["keys"] = r["keys"]
        out.append(item)
    return out


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
@mcp.tool()
def health_check() -> dict[str, str]:
    """Auth + config status. Never hangs on expired tokens (headless-safe)."""
    return {
        "auth": gsc.token_status(),
        "site": scoring.default_site() or "unset (pass `site` or set CREATOR_SEO_SITE)",
        "ad_rpm_per_1000": str(scoring.rpm()),
        "affiliate_categories": str(scoring.affiliate_rates()),
    }


@mcp.tool()
def list_sites() -> list[str]:
    """List Search Console properties the credentials can read."""
    return [s.get("siteUrl", "") for s in gsc.list_sites()]


@mcp.tool()
def get_striking_distance_keywords(site: str | None = None, days: int = 28, limit: int = 25) -> list[dict[str, Any]]:
    """Queries ranking ~4-15 (page-2-to-top-3) — the most actionable growth band.

    Each row reports the ranking page and the gap to page 1 (position - 3).
    """
    site = _resolve_site(site)
    (s, e), _ = _windows(site, days)
    rows = gsc.search_analytics(site, s, e, ["query", "page"], row_limit=25000)
    band = [r for r in rows if 4 <= float(r.get("position", 99)) <= 15]
    scored = scoring.rank_opportunities(band)
    out = []
    for r in scored[:limit]:
        out.append(
            {
                "query": r["keys"][0],
                "page": r["keys"][1],
                "position": round(float(r["position"]), 2),
                "gap_to_page1": round(max(0, float(r["position"]) - 3), 2),
                "impressions": int(r["impressions"]),
                "clicks": int(r["clicks"]),
                "ctr": round(float(r["ctr"]), 4),
                "projected_clicks": r["projected_clicks"],
                "rev_potential_usd": r["rev_potential"],
            }
        )
    return out


@mcp.tool()
def get_page_performance(site: str | None = None, url: str = "", days: int = 28) -> dict[str, Any]:
    """Full GSC picture for one URL: totals + the queries driving it."""
    site = _resolve_site(site)
    (s, e), _ = _windows(site, days)
    agg = gsc.search_analytics(
        site, s, e, ["page"],
        dimension_filter={"dimension": "page", "operator": "equals", "expression": url},
    )
    queries = gsc.search_analytics(
        site, s, e, ["query"],
        dimension_filter={"dimension": "page", "operator": "equals", "expression": url},
    )
    row = agg[0] if agg else {}
    return {
        "url": url,
        "clicks": int(row.get("clicks", 0)),
        "impressions": int(row.get("impressions", 0)),
        "ctr": round(float(row.get("ctr", 0)), 4),
        "position": round(float(row.get("position", 0)), 2),
        "driving_queries": _simple(queries)[:25],
    }


@mcp.tool()
def analyze_content_decay(site: str | None = None, days: int = 28, limit: int = 20) -> list[dict[str, Any]]:
    """Pages losing clicks month-over-month (current window vs prior window)."""
    site = _resolve_site(site)
    (s, e), (ps, pe) = _windows(site, days)
    cur = {r["keys"][0]: r for r in gsc.search_analytics(site, s, e, ["page"], row_limit=25000)}
    prior = {r["keys"][0]: r for r in gsc.search_analytics(site, ps, pe, ["page"], row_limit=25000)}

    decliners = []
    for url, r in cur.items():
        prev = prior.get(url)
        if not prev or prev.get("clicks", 0) <= 5:
            continue
        d_clicks = int(r.get("clicks", 0)) - int(prev.get("clicks", 0))
        if d_clicks < 0:
            decliners.append(
                {
                    "url": url,
                    "clicks_now": int(r.get("clicks", 0)),
                    "clicks_prior": int(prev.get("clicks", 0)),
                    "delta_clicks": d_clicks,
                    "impressions_now": int(r.get("impressions", 0)),
                    "position_now": round(float(r.get("position", 0)), 2),
                    "position_prior": round(float(prev.get("position", 0)), 2),
                }
            )
    decliners.sort(key=lambda x: x["delta_clicks"])
    return decliners[:limit]


@mcp.tool()
def find_cannibalization(site: str | None = None, days: int = 28, min_impressions: int = 100, limit: int = 20) -> list[dict[str, Any]]:
    """Queries where two+ pages compete — authority is being split."""
    site = _resolve_site(site)
    (s, e), _ = _windows(site, days)
    rows = gsc.search_analytics(site, s, e, ["query", "page"], row_limit=25000)

    by_query: dict[str, list[dict]] = {}
    for r in rows:
        q = r["keys"][0]
        by_query.setdefault(q, []).append(r)

    out = []
    for q, pages in by_query.items():
        if len(pages) < 2:
            continue
        total_imp = sum(int(p.get("impressions", 0)) for p in pages)
        if total_imp < min_impressions:
            continue
        pages_sorted = sorted(pages, key=lambda p: int(p.get("clicks", 0)), reverse=True)
        out.append(
            {
                "query": q,
                "pages_tracking": len(pages_sorted),
                "total_impressions": int(total_imp),
                "split": [
                    {
                        "url": p["keys"][1],
                        "clicks": int(p.get("clicks", 0)),
                        "impressions": int(p.get("impressions", 0)),
                        "position": round(float(p.get("position", 0)), 2),
                    }
                    for p in pages_sorted
                ],
            }
        )
    out.sort(key=lambda x: x["total_impressions"], reverse=True)
    return out[:limit]


# --------------------------------------------------------------------------- #
# Raptive RPM blend (real per-page ad revenue when creds exist; else flat RPM)
# --------------------------------------------------------------------------- #
_raptive_rpm_cache: dict | None = None


def _raptive_rpm() -> dict:
    global _raptive_rpm_cache
    if _raptive_rpm_cache is None:
        try:
            _raptive_rpm_cache = raptive.page_rpm_map()
        except Exception:
            _raptive_rpm_cache = {}
    return _raptive_rpm_cache


def _page_rpm(url: str) -> float | None:
    """Real per-page ad RPM from Raptive, or None to use the configured estimate.

    Returns None whenever Raptive is unavailable (no credentials, API error, or
    no site on the account) so the caller falls back to CREATOR_SEO_SITE_RPM.
    Returns 0.0 only when Raptive IS configured but the page's host is not the
    monetized property — i.e. the page genuinely earns nothing from Raptive.

    Never raises: ad-network specifics must not be able to break opportunity
    ranking for users who don't have the network at all.
    """
    if not url:
        return None
    try:
        if raptive.host_of(url) != raptive.site_host():
            return 0.0
        return raptive.match_rpm(raptive.path_of(url), _raptive_rpm())
    except Exception:
        return None


@mcp.tool()
def get_top_opportunities(site: str | None = None, days: int = 28, limit: int = 20) -> list[dict[str, Any]]:
    """The headline: combine striking-distance, decay, and cannibalization into
    one ranked action list. Revenue-weighted with REAL per-page Raptive RPM when
    Raptive creds are configured in .env, else the flat CREATOR_SEO_SITE_RPM."""
    site = _resolve_site(site)
    (s, e), (ps, pe) = _windows(site, days)
    rpm_map = _raptive_rpm()

    # 1. Striking distance (4-15)
    sd = [
        r for r in gsc.search_analytics(site, s, e, ["query", "page"])
        if 4 <= float(r.get("position", 99)) <= 15
    ]
    opps = []
    for r in sd:
        page = r["keys"][1]
        prpm = _page_rpm(page)
        opps.append(
            {
                "type": "striking_distance",
                "target": r["keys"][0],
                "page": page,
                "impressions": int(r["impressions"]),
                "ctr": float(r["ctr"]),
                "position": float(r["position"]),
                "page_rpm": prpm if prpm is not None else scoring.rpm(),
                "projected_clicks": scoring.incremental_clicks(float(r["impressions"]), float(r["ctr"])),
                "rev_potential_usd": scoring.opportunity_value(
                    float(r["impressions"]), float(r["ctr"]), r["keys"][0], rpm_override=prpm
                ),
            }
        )

    # 2. Content decay
    cur = {r["keys"][0]: r for r in gsc.search_analytics(site, s, e, ["page"], row_limit=25000)}
    prior = {r["keys"][0]: r for r in gsc.search_analytics(site, ps, pe, ["page"], row_limit=25000)}
    for url, r in cur.items():
        prev = prior.get(url)
        if prev and prev.get("clicks", 0) > 5 and int(r.get("clicks", 0)) < int(prev.get("clicks", 0)):
            prpm = _page_rpm(url)
            opps.append(
                {
                    "type": "decay",
                    "target": url,
                    "impressions": int(r.get("impressions", 0)),
                    "ctr": float(r.get("ctr", 0)),
                    "position": float(r.get("position", 0)),
                    "page_rpm": prpm if prpm is not None else scoring.rpm(),
                    "clicks_lost": int(prev.get("clicks", 0)) - int(r.get("clicks", 0)),
                    "rev_potential_usd": scoring.opportunity_value(
                        float(r.get("impressions", 0)), float(r.get("ctr", 0)), url, rpm_override=prpm
                    ),
                }
            )

    # 3. Cannibalization: consolidating competes points at target page = recoverable clicks
    cann = {}
    for row in gsc.search_analytics(site, s, e, ["query", "page"], row_limit=25000):
        q = row["keys"][0]
        cann.setdefault(q, []).append(row)
    for q, pages in cann.items():
        if len(pages) < 2:
            continue
        pages = sorted(pages, key=lambda p: int(p.get("clicks", 0)), reverse=True)
        winner = pages[0]
        lost = int(sum(int(p.get("clicks", 0)) for p in pages[1:]))
        if lost < 2:
            continue
        tpage = winner["keys"][1]
        prpm = _page_rpm(tpage)
        opps.append(
            {
                "type": "cannibalization",
                "target": tpage,
                "query": q,
                "impressions": int(sum(int(p.get("impressions", 0)) for p in pages)),
                "ctr": float(winner.get("ctr", 0)),
                "position": float(winner.get("position", 0)),
                "page_rpm": prpm if prpm is not None else scoring.rpm(),
                "clicks_split_away": lost,
                "rev_potential_usd": scoring.opportunity_value(
                    float(sum(int(p.get("impressions", 0)) for p in pages)),
                    float(winner.get("ctr", 0)),
                    q,
                    rpm_override=prpm,
                ),
            }
        )

    opps.sort(key=lambda x: x["rev_potential_usd"], reverse=True)
    return opps[:limit]


@mcp.tool()
def get_raptive_revenue(site_id: str | None = None, days: int = 30) -> dict[str, Any]:
    """Real Raptive revenue for the window: total $, pageviews, and blended RPM.

    Requires RAPTIVE_CLIENT_ID/SECRET in .env; raises a clear error otherwise.
    """
    try:
        sid = raptive._resolve_site_id(site_id)
        s, e = raptive.window(sid, days)
        rows = raptive.earnings(sid, s, e)
    except Exception as exc:
        raise ValueError(f"Raptive is not configured or unreachable: {exc}") from exc
    tot = sum(float(r.get("earnings", 0) or 0) for r in rows)
    pv = sum(float(r.get("pageviews", 0) or 0) for r in rows)
    return {
        "site_id": sid,
        "start": s,
        "end": e,
        "revenue_usd": round(tot, 2),
        "pageviews": int(pv),
        "rpm": round((tot / pv * 1000.0) if pv else 0.0, 2),
    }


@mcp.tool()
def get_raptive_page_performance(site_id: str | None = None, days: int = 30, limit: int = 25) -> list[dict[str, Any]]:
    """Per-page Raptive RPM + earnings for the window (highest revenue first)."""
    try:
        sid = raptive._resolve_site_id(site_id)
        s, e = raptive.window(sid, days)
        rows = raptive.pages_performance(sid, s, e)
    except Exception as exc:
        raise ValueError(f"Raptive is not configured or unreachable: {exc}") from exc
    rows.sort(key=lambda r: float(r.get("earnings", 0) or 0), reverse=True)
    out = []
    for r in rows[:limit]:
        out.append(
            {
                "pageUrl": r.get("pageUrl", ""),
                "rpm": round(float(r.get("rpm", 0) or 0), 2),
                "cpm": round(float((r.get("cpm") or {}).get("value", 0) or 0), 2),
                "earnings_usd": round(float(r.get("earnings", 0) or 0), 2),
                "pageviews": int(r.get("pageviews", 0) or 0),
                "impressions": int(r.get("impressions", 0) or 0),
            }
        )
    return out


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()