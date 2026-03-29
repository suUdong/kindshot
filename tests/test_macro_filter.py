"""Tests for MacroFilter — entry gating, confidence adjustment, KR defense, fail-open."""

from __future__ import annotations

import pytest

from kindshot.macro_filter import MacroFilter, MacroFilterResult, MacroSnapshot, _parse_signal_value


# ── Fixtures ──────────────────────────────────────────────

def _snap(
    overall_regime: str = "neutral",
    overall_confidence: float = 0.6,
    kr_regime: str = "neutral",
    kr_confidence: float = 0.5,
    kr_signals: dict | None = None,
    transition_watch: str = "stable",
    transition_probability: float = 0.0,
    strategy: dict | None = None,
) -> MacroSnapshot:
    return MacroSnapshot(
        overall_regime=overall_regime,
        overall_confidence=overall_confidence,
        kr_regime=kr_regime,
        kr_confidence=kr_confidence,
        kr_signals=kr_signals or {},
        transition_watch=transition_watch,
        transition_probability=transition_probability,
        strategy=strategy or {},
    )


@pytest.fixture
def macro_filter() -> MacroFilter:
    return MacroFilter()


# ── _parse_signal_value ───────────────────────────────────

class TestParseSignalValue:
    def test_normal(self):
        assert _parse_signal_value("1380 (neutral)") == 1380.0

    def test_positive(self):
        assert _parse_signal_value("+0.15 (bullish)") == 0.15

    def test_negative(self):
        assert _parse_signal_value("-1.25 (bearish)") == -1.25

    def test_comma(self):
        assert _parse_signal_value("1,400 (bearish)") == 1400.0

    def test_empty(self):
        assert _parse_signal_value("") is None

    def test_no_number(self):
        assert _parse_signal_value("N/A") is None


# ── from_downstream_payload ───────────────────────────────

class TestFromDownstreamPayload:
    def test_ok_payload(self):
        payload = {
            "status": "ok",
            "consumer": "kindshot",
            "date": "2026-03-29",
            "overall_regime": "contractionary",
            "overall_confidence": 0.75,
            "layers": {
                "kr": {
                    "regime": "contractionary",
                    "confidence": 0.8,
                    "signals": {"krw_usd": "1420 (bearish)", "yield_curve": "-0.8 (bearish)"},
                },
            },
            "transition": {"watch": "stable", "probability": 0.3},
            "strategy": {"stance": "defensive"},
        }
        snap = MacroSnapshot.from_downstream_payload(payload)
        assert snap is not None
        assert snap.overall_regime == "contractionary"
        assert snap.overall_confidence == 0.75
        assert snap.kr_regime == "contractionary"
        assert snap.kr_signals["krw_usd"] == "1420 (bearish)"
        assert snap.transition_watch == "stable"
        assert snap.strategy == {"stance": "defensive"}

    def test_error_payload_returns_none(self):
        assert MacroSnapshot.from_downstream_payload({"status": "error"}) is None

    def test_missing_layers_defaults(self):
        payload = {
            "status": "ok",
            "overall_regime": "expansionary",
            "overall_confidence": 0.6,
        }
        snap = MacroSnapshot.from_downstream_payload(payload)
        assert snap is not None
        assert snap.kr_regime == "neutral"
        assert snap.kr_signals == {}


# ── Entry blocking ────────────────────────────────────────

class TestEntryBlocking:
    def test_contractionary_high_confidence_blocks(self, macro_filter):
        snap = _snap(overall_regime="contractionary", overall_confidence=0.6)
        result = macro_filter.evaluate(snap)
        assert result.blocked
        assert "macro_gate" in result.reason
        assert "contractionary" in result.reason

    def test_contractionary_low_confidence_passes(self, macro_filter):
        """confidence < 0.5 이면 contractionary라도 통과 (불확실한 레짐)"""
        snap = _snap(overall_regime="contractionary", overall_confidence=0.4)
        result = macro_filter.evaluate(snap)
        assert not result.blocked

    def test_watch_contractionary_high_prob_blocks(self, macro_filter):
        snap = _snap(
            transition_watch="watch_contractionary",
            transition_probability=0.75,
        )
        result = macro_filter.evaluate(snap)
        assert result.blocked
        assert "watch_contractionary" in result.reason

    def test_watch_contractionary_low_prob_passes(self, macro_filter):
        snap = _snap(
            transition_watch="watch_contractionary",
            transition_probability=0.5,
        )
        result = macro_filter.evaluate(snap)
        assert not result.blocked

    def test_krw_usd_extreme_blocks(self, macro_filter):
        snap = _snap(kr_signals={"krw_usd": "1420 (bearish)"})
        result = macro_filter.evaluate(snap)
        assert result.blocked
        assert "krw_usd" in result.reason

    def test_krw_usd_normal_passes(self, macro_filter):
        snap = _snap(kr_signals={"krw_usd": "1300 (neutral)"})
        result = macro_filter.evaluate(snap)
        assert not result.blocked


# ── Confidence adjustments ────────────────────────────────

class TestConfidenceAdjustment:
    def test_expansionary_boost(self, macro_filter):
        snap = _snap(overall_regime="expansionary")
        result = macro_filter.evaluate(snap)
        assert result.confidence_adj == 3

    def test_neutral_no_change(self, macro_filter):
        snap = _snap(overall_regime="neutral")
        result = macro_filter.evaluate(snap)
        assert result.confidence_adj == 0

    def test_contractionary_below_block_threshold(self, macro_filter):
        """confidence < 0.5: 차단 안 되지만 감점은 적용"""
        snap = _snap(overall_regime="contractionary", overall_confidence=0.3)
        result = macro_filter.evaluate(snap)
        assert not result.blocked
        assert result.confidence_adj == -5

    def test_watch_contractionary_additional_penalty(self, macro_filter):
        snap = _snap(
            overall_regime="neutral",
            transition_watch="watch_contractionary",
            transition_probability=0.5,  # below block threshold
        )
        result = macro_filter.evaluate(snap)
        assert not result.blocked
        assert result.confidence_adj == -3  # neutral(0) + watch(-3)

    def test_krw_weakness_penalty(self, macro_filter):
        """KRW/USD 1350~1400 구간: 차단은 안 되지만 -3 감점"""
        snap = _snap(kr_signals={"krw_usd": "1370 (bearish)"})
        result = macro_filter.evaluate(snap)
        assert not result.blocked
        assert result.confidence_adj == -3  # neutral(0) + krw_weakness(-3)

    def test_yield_curve_inversion_penalty(self, macro_filter):
        snap = _snap(kr_signals={"yield_curve": "-0.8 (bearish)"})
        result = macro_filter.evaluate(snap)
        assert not result.blocked
        assert result.confidence_adj == -3  # neutral(0) + yield_inversion(-3)

    def test_stacked_penalties(self, macro_filter):
        """모든 감점 동시 적용: contractionary(-5) + watch(-3) + krw(-3) + yield(-3)"""
        snap = _snap(
            overall_regime="contractionary",
            overall_confidence=0.3,  # below block threshold
            transition_watch="watch_contractionary",
            transition_probability=0.5,
            kr_signals={
                "krw_usd": "1370 (bearish)",
                "yield_curve": "-0.8 (bearish)",
            },
        )
        result = macro_filter.evaluate(snap)
        assert not result.blocked
        assert result.confidence_adj == -14  # -5 + -3 + -3 + -3


# ── Fail-open ─────────────────────────────────────────────

class TestFailOpen:
    def test_none_snapshot_passes(self, macro_filter):
        result = macro_filter.evaluate(None)
        assert not result.blocked
        assert result.confidence_adj == 0
        assert result.position_multiplier == 1.0

    def test_default_result_is_passthrough(self):
        result = MacroFilterResult(blocked=False)
        assert result.confidence_adj == 0
        assert result.position_multiplier == 1.0
