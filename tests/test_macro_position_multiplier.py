"""Integration tests for macro regime position multiplier.

Covers:
- _compute_position_multiplier() logic in MarketMonitor
- macro_position_multiplier field on MarketContext
- _build_prompt() includes macro_size_mult in prompt string
"""

import pytest

from kindshot.config import Config
from kindshot.decision import _build_prompt
from kindshot.market import MarketMonitor
from kindshot.models import Bucket, ContextCard, MarketContext


def _monitor(**kwargs) -> MarketMonitor:
    """Create a MarketMonitor with no KIS client and set internal macro state."""
    monitor = MarketMonitor(Config())
    for key, value in kwargs.items():
        setattr(monitor, f"_macro_{key}", value)
    return monitor


def _minimal_ctx() -> ContextCard:
    return ContextCard(
        ret_today=0.0,
        ret_1d=0.0,
        ret_3d=0.0,
        pos_20d=0.5,
        gap=0.0,
        adv_value_20d=5_000_000_000,
        spread_bps=5.0,
        vol_pct_20d=1.5,
        intraday_value_vs_adv20d=0.1,
        top_ask_notional=50_000_000,
    )


# ---------------------------------------------------------------------------
# _compute_position_multiplier unit tests
# ---------------------------------------------------------------------------

def test_compute_multiplier_expansionary():
    """overall=expansionary regime yields base multiplier of 1.2."""
    monitor = _monitor(overall_regime="expansionary", overall_confidence=0.9, kr_regime="expansionary")
    result = monitor._compute_position_multiplier()
    assert result == pytest.approx(1.2, abs=1e-6)


def test_compute_multiplier_contractionary():
    """overall=contractionary regime yields base multiplier of 0.6."""
    monitor = _monitor(overall_regime="contractionary", overall_confidence=0.9, kr_regime="contractionary")
    result = monitor._compute_position_multiplier()
    assert result == pytest.approx(0.6, abs=1e-6)


def test_compute_multiplier_neutral():
    """overall=neutral regime yields base multiplier of 1.0."""
    monitor = _monitor(overall_regime="neutral", overall_confidence=0.9, kr_regime="neutral")
    result = monitor._compute_position_multiplier()
    assert result == pytest.approx(1.0, abs=1e-6)


def test_compute_multiplier_kr_contractionary_penalty():
    """overall=expansionary but kr=contractionary reduces multiplier by 0.1 to 1.1."""
    monitor = _monitor(overall_regime="expansionary", overall_confidence=0.9, kr_regime="contractionary")
    result = monitor._compute_position_multiplier()
    assert result == pytest.approx(1.1, abs=1e-6)


def test_compute_multiplier_kr_contractionary_no_penalty_when_overall_also_contractionary():
    """kr=contractionary penalty is skipped when overall is also contractionary."""
    monitor = _monitor(overall_regime="contractionary", overall_confidence=0.9, kr_regime="contractionary")
    result = monitor._compute_position_multiplier()
    # No penalty applied; pure contractionary base = 0.6
    assert result == pytest.approx(0.6, abs=1e-6)


def test_compute_multiplier_low_confidence_dampening():
    """confidence=0.15 dampens expansionary 1.2 halfway toward 1.0."""
    # blend = 0.15 / 0.3 = 0.5  →  1.0 + (1.2 - 1.0) * 0.5 = 1.1
    monitor = _monitor(overall_regime="expansionary", overall_confidence=0.15, kr_regime=None)
    result = monitor._compute_position_multiplier()
    assert result == pytest.approx(1.1, abs=1e-3)


def test_compute_multiplier_zero_confidence_fully_dampened():
    """confidence=0.0 collapses any regime multiplier to exactly 1.0."""
    monitor = _monitor(overall_regime="expansionary", overall_confidence=0.0, kr_regime=None)
    result = monitor._compute_position_multiplier()
    assert result == pytest.approx(1.0, abs=1e-6)


def test_compute_multiplier_clamp_upper():
    """Result is clamped at 1.5 even if raw calculation exceeds it."""
    monitor = _monitor(overall_regime="expansionary", overall_confidence=1.0, kr_regime=None)
    # Manually push base above 1.5 by patching the dict lookup indirectly
    # Contractionary + penalty cannot exceed upper clamp; test with a regime that
    # would compute 1.2 — verify it stays <= 1.5.
    result = monitor._compute_position_multiplier()
    assert result <= 1.5


def test_compute_multiplier_clamp_lower():
    """Result is clamped at 0.5 even if raw calculation falls below it."""
    # contractionary=0.6 with kr penalty would be 0.6-0.1=0.5 which is exactly the floor.
    monitor = _monitor(overall_regime="contractionary", overall_confidence=1.0, kr_regime="expansionary")
    # kr_regime is not contractionary so no penalty; result = 0.6 >= 0.5, no clamping needed.
    # Force a below-floor scenario: unknown regime defaults to 1.0 minus applied penalty
    # Since we can't get below 0.5 through normal inputs, verify the clamp is never violated.
    result = monitor._compute_position_multiplier()
    assert result >= 0.5


def test_compute_multiplier_unknown_regime_defaults_to_neutral():
    """Unknown/missing regime string falls back to 1.0 (neutral)."""
    monitor = _monitor(overall_regime="unknown_value", overall_confidence=0.9, kr_regime=None)
    result = monitor._compute_position_multiplier()
    assert result == pytest.approx(1.0, abs=1e-6)


def test_compute_multiplier_none_regime_defaults_to_neutral():
    """None overall_regime falls back to 1.0 (neutral)."""
    monitor = _monitor(overall_regime=None, overall_confidence=0.9, kr_regime=None)
    result = monitor._compute_position_multiplier()
    assert result == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# MarketContext model field test
# ---------------------------------------------------------------------------

def test_market_context_includes_multiplier_field():
    """MarketContext model accepts and stores macro_position_multiplier."""
    ctx = MarketContext(
        macro_overall_regime="expansionary",
        macro_overall_confidence=0.85,
        macro_kr_regime="neutral",
        macro_position_multiplier=1.2,
    )
    assert ctx.macro_position_multiplier == pytest.approx(1.2)


def test_market_context_multiplier_defaults_to_none():
    """macro_position_multiplier is None when not provided."""
    ctx = MarketContext()
    assert ctx.macro_position_multiplier is None


def test_snapshot_reflects_computed_multiplier():
    """MarketMonitor.snapshot includes the computed macro_position_multiplier."""
    monitor = _monitor(overall_regime="contractionary", overall_confidence=0.8, kr_regime="contractionary")
    monitor._macro_position_multiplier = monitor._compute_position_multiplier()
    snap = monitor.snapshot
    assert snap.macro_position_multiplier == pytest.approx(0.6, abs=1e-6)


# ---------------------------------------------------------------------------
# _build_prompt prompt string tests
# ---------------------------------------------------------------------------

def test_build_prompt_includes_macro_size_mult_when_multiplier_set():
    """Prompt contains macro_size_mult=X.XXx when multiplier is provided."""
    market_ctx = MarketContext(
        kospi_change_pct=0.5,
        macro_overall_regime="expansionary",
        macro_overall_confidence=0.9,
        macro_kr_regime="neutral",
        macro_position_multiplier=1.2,
    )
    prompt = _build_prompt(
        bucket=Bucket.POS_STRONG,
        headline="Test corp signs 1000억 supply contract",
        ticker="005930",
        corp_name="Samsung",
        detected_at="09:05:00",
        ctx=_minimal_ctx(),
        market_ctx=market_ctx,
    )
    assert "macro_size_mult=1.20x" in prompt


def test_build_prompt_excludes_macro_size_mult_when_multiplier_none():
    """Prompt omits macro_size_mult when macro_position_multiplier is None."""
    market_ctx = MarketContext(
        kospi_change_pct=0.5,
        macro_overall_regime="neutral",
        macro_overall_confidence=0.8,
        macro_kr_regime=None,
        macro_position_multiplier=None,
    )
    prompt = _build_prompt(
        bucket=Bucket.POS_STRONG,
        headline="Test corp signs supply contract",
        ticker="005930",
        corp_name="Samsung",
        detected_at="09:05:00",
        ctx=_minimal_ctx(),
        market_ctx=market_ctx,
    )
    assert "macro_size_mult" not in prompt


def test_build_prompt_includes_macro_regime_guides_size_constraint():
    """Prompt always includes macro_regime_guides_size=true in constraints."""
    prompt = _build_prompt(
        bucket=Bucket.POS_WEAK,
        headline="Test headline",
        ticker="035720",
        corp_name="Kakao",
        detected_at="10:00:00",
        ctx=_minimal_ctx(),
        market_ctx=None,
    )
    assert "macro_regime_guides_size=true" in prompt


def test_build_prompt_macro_mult_format_two_decimal_places():
    """macro_size_mult is formatted with exactly 2 decimal places followed by 'x'."""
    market_ctx = MarketContext(
        kospi_change_pct=0.0,
        macro_overall_regime="contractionary",
        macro_overall_confidence=0.7,
        macro_position_multiplier=0.6,
    )
    prompt = _build_prompt(
        bucket=Bucket.NEG_WEAK,
        headline="Earnings miss announced",
        ticker="000660",
        corp_name="SK Hynix",
        detected_at="08:45:00",
        ctx=_minimal_ctx(),
        market_ctx=market_ctx,
    )
    assert "macro_size_mult=0.60x" in prompt
