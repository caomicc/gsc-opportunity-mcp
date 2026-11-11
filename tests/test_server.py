"""Server behaviour with no network and no credentials configured."""
import pytest

from gsc_opportunity import server


@pytest.fixture(autouse=True)
def _no_creds(monkeypatch):
    monkeypatch.delenv("RAPTIVE_CLIENT_ID", raising=False)
    monkeypatch.delenv("RAPTIVE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("CREATOR_SEO_SITE", raising=False)
    monkeypatch.setenv("CREATOR_SEO_SITE_RPM", "5")
    server._raptive_rpm_cache = None


def test_resolve_site_requires_configuration():
    with pytest.raises(ValueError, match="CREATOR_SEO_SITE"):
        server._resolve_site(None)


def test_resolve_site_prefers_explicit_argument(monkeypatch):
    monkeypatch.setenv("CREATOR_SEO_SITE", "sc-domain:env.example")
    assert server._resolve_site("sc-domain:arg.example") == "sc-domain:arg.example"


def test_page_rpm_returns_none_when_raptive_unavailable():
    # Raptive raises without credentials; _page_rpm must swallow it so ranking works.
    assert server._page_rpm("https://example.com/a") is None


def test_page_rpm_returns_zero_for_non_monetized_host(monkeypatch):
    monkeypatch.setattr(server.raptive, "site_host", lambda: "monetized.example")
    assert server._page_rpm("https://other.example/post") == 0.0


def test_get_top_opportunities_ranks_without_raptive(monkeypatch):
    cur_range = ("2026-01-01", "2026-01-28")
    prior_range = ("2025-12-04", "2025-12-31")
    monkeypatch.setattr(
        server, "_windows", lambda site, days: (cur_range, prior_range)
    )

    sd_rows = [  # query/page dimension, positions inside the 4-15 band
        {"keys": ["big query", "https://example.com/big"], "clicks": 2, "impressions": 4000,
         "ctr": 0.005, "position": 6.0},
        {"keys": ["small query", "https://example.com/small"], "clicks": 1, "impressions": 90,
         "ctr": 0.05, "position": 8.0},
    ]
    page_rows_cur = [  # page dimension, current window
        {"keys": ["https://example.com/big"], "clicks": 20, "impressions": 4000,
         "ctr": 0.01, "position": 9.0},
        {"keys": ["https://example.com/small"], "clicks": 1, "impressions": 90,
         "ctr": 0.01, "position": 8.0},
    ]
    page_rows_prior = [  # page dimension, prior window: /big has fallen
        {"keys": ["https://example.com/big"], "clicks": 60, "impressions": 4000,
         "ctr": 0.015, "position": 7.0},
        {"keys": ["https://example.com/small"], "clicks": 1, "impressions": 90,
         "ctr": 0.01, "position": 8.0},
    ]

    def fake(site, start, end, dimensions, **kw):
        if dimensions == ["query", "page"]:
            return sd_rows
        return page_rows_prior if (start, end) == prior_range else page_rows_cur

    monkeypatch.setattr(server.gsc, "search_analytics", fake)
    out = server.get_top_opportunities(site="sc-domain:example.com", days=28, limit=10)

    assert out, "should return ranked opportunities"
    values = [o["rev_potential_usd"] for o in out]
    assert values == sorted(values, reverse=True), "must be revenue-ordered"
    assert all(o["page_rpm"] == 5 for o in out), "no Raptive -> flat estimate"
    types = {o["type"] for o in out}
    assert types == {"striking_distance", "decay"}, types
