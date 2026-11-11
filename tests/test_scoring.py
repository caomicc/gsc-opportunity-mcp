"""Pure scoring math — no network, no credentials."""
import os

import pytest

from gsc_opportunity import scoring


def test_incremental_clicks_toward_target_ctr():
    # 1000 impressions at 2% CTR, targeting 10% -> 80 extra clicks
    assert scoring.incremental_clicks(1000, 0.02) == pytest.approx(80.0)


def test_incremental_clicks_never_negative():
    assert scoring.incremental_clicks(1000, 0.25) == 0.0


def test_opportunity_value_uses_rate_per_thousand(monkeypatch):
    monkeypatch.setenv("CREATOR_SEO_SITE_RPM", "5")
    monkeypatch.delenv("CREATOR_SEO_AFFILIATE_CATEGORIES", raising=False)
    # 80 projected clicks / 1000 * $5 = $0.40
    assert scoring.opportunity_value(1000, 0.02, "some query") == pytest.approx(0.40)


def test_affiliate_premium_matches_substring_case_insensitively(monkeypatch):
    monkeypatch.setenv("CREATOR_SEO_SITE_RPM", "5")
    monkeypatch.setenv(
        "CREATOR_SEO_AFFILIATE_CATEGORIES", '{"smart ring": 40}'
    )
    # (5 + 40) per 1000 clicks * 80 clicks = $3.60
    assert scoring.opportunity_value(1000, 0.02, "Best Smart Ring 2026") == pytest.approx(3.60)


def test_malformed_affiliate_config_is_ignored(monkeypatch):
    monkeypatch.setenv("CREATOR_SEO_AFFILIATE_CATEGORIES", "not json")
    assert scoring.affiliate_rates() == {}


def test_rpm_override_beats_env(monkeypatch):
    monkeypatch.setenv("CREATOR_SEO_SITE_RPM", "1")
    assert scoring.opportunity_value(1000, 0.02, "q", rpm_override=10) == pytest.approx(0.80)


def test_rank_opportunities_sorts_by_value(monkeypatch):
    monkeypatch.setenv("CREATOR_SEO_SITE_RPM", "5")
    rows = [
        {"keys": ["low"], "impressions": 100, "ctr": 0.09},
        {"keys": ["high"], "impressions": 5000, "ctr": 0.01},
    ]
    ranked = scoring.rank_opportunities(rows)
    assert [r["keys"][0] for r in ranked] == ["high", "low"]


def test_default_site_is_empty_when_unset(monkeypatch):
    monkeypatch.delenv("CREATOR_SEO_SITE", raising=False)
    assert scoring.default_site() == ""
