"""Tests for dart_earnings_strategy: PEAD 시그널 생성, YoY 스코어링, 공시 감지."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kindshot.dart_earnings_strategy import (
    DartEarningsStrategy,
    compute_yoy,
    is_turnaround,
    score_earnings,
    size_hint_from_confidence,
    _infer_prior_period,
    _parse_op_from_title,
)
from kindshot.dart_enricher import EarningsInfo
from kindshot.feed import RawDisclosure, _is_earnings_report
from kindshot.models import Action, SizeHint
from kindshot.strategy import SignalSource


# ── _is_earnings_report ────────────────────────────────


class TestIsEarningsReport:
    def test_provisional_result(self):
        assert _is_earnings_report("잠정실적") is True

    def test_wrapped_in_report(self):
        assert _is_earnings_report("주요사항보고서(잠정실적)") is True

    def test_30pct_change(self):
        assert _is_earnings_report("매출액또는손익구조30%이상변경") is True

    def test_30pct_change_with_spaces(self):
        assert _is_earnings_report("매출액 또는 손익구조 30%이상 변경") is True

    def test_operating_provisional(self):
        assert _is_earnings_report("영업(잠정)실적") is True

    def test_unrelated(self):
        assert _is_earnings_report("주요사항보고서(수주공시)") is False

    def test_buyback_not_earnings(self):
        assert _is_earnings_report("자기주식취득결정") is False

    def test_empty(self):
        assert _is_earnings_report("") is False


# ── compute_yoy ────────────────────────────────────────


class TestComputeYoy:
    def test_positive_growth(self):
        assert compute_yoy(200, 100) == pytest.approx(100.0)

    def test_negative_growth(self):
        assert compute_yoy(50, 100) == pytest.approx(-50.0)

    def test_zero_prior(self):
        assert compute_yoy(100, 0) is None

    def test_same(self):
        assert compute_yoy(100, 100) == pytest.approx(0.0)

    def test_large_growth(self):
        assert compute_yoy(500, 100) == pytest.approx(400.0)

    def test_negative_to_positive(self):
        # -100 → 100: ((100 - (-100)) / 100) * 100 = 200%
        assert compute_yoy(100, -100) == pytest.approx(200.0)


# ── is_turnaround ──────────────────────────────────────


class TestIsTurnaround:
    def test_turnaround(self):
        assert is_turnaround(100, -50) is True

    def test_still_loss(self):
        assert is_turnaround(-20, -50) is False

    def test_both_positive(self):
        assert is_turnaround(100, 50) is False

    def test_turn_to_loss(self):
        assert is_turnaround(-10, 50) is False


# ── _infer_prior_period ────────────────────────────────


class TestInferPriorPeriod:
    def test_q1(self):
        year, code = _infer_prior_period("2025년 1분기 잠정실적")
        assert year == "2024"
        assert code == "11013"

    def test_q2_half(self):
        year, code = _infer_prior_period("2025년 반기 잠정실적")
        assert year == "2024"
        assert code == "11012"

    def test_q3(self):
        year, code = _infer_prior_period("2025년 3분기 잠정실적")
        assert year == "2024"
        assert code == "11014"

    def test_annual(self):
        year, code = _infer_prior_period("2025년 사업보고서(잠정실적)")
        assert year == "2024"
        assert code == "11011"

    def test_no_quarter(self):
        # 분기 미명시 → 전기 사업보고서
        year, code = _infer_prior_period("2025년 잠정실적")
        assert year == "2024"
        assert code == "11011"


# ── _parse_op_from_title ───────────────────────────────


class TestParseOpFromTitle:
    def test_basic(self):
        assert _parse_op_from_title("영업이익 1,500억원") == 150_000_000_000

    def test_million(self):
        assert _parse_op_from_title("영업이익 500백만원") == 500_000_000

    def test_no_match(self):
        assert _parse_op_from_title("매출액 증가") is None

    def test_colon_format(self):
        assert _parse_op_from_title("영업이익: 200억원") == 20_000_000_000


# ── score_earnings ─────────────────────────────────────


def _make_config(**overrides):
    config = MagicMock()
    config.dart_earnings_base_confidence = overrides.get("base", 60)
    config.dart_earnings_yoy_bonus_30 = overrides.get("yoy_30", 10)
    config.dart_earnings_yoy_bonus_50 = overrides.get("yoy_50", 15)
    config.dart_earnings_yoy_bonus_100 = overrides.get("yoy_100", 20)
    config.dart_earnings_turnaround_bonus = overrides.get("turnaround", 15)
    config.dart_earnings_negative_skip = overrides.get("negative_skip", True)
    return config


def _make_prior(*, operating_profit=100, revenue=1000, net_income=80):
    return EarningsInfo(
        corp_code="00126380",
        corp_name="삼성전자",
        ticker="005930",
        bsns_year="2024",
        reprt_code="11013",
        revenue=revenue,
        operating_profit=operating_profit,
        net_income=net_income,
    )


class TestScoreEarnings:
    def test_yoy_100_plus(self):
        """YoY 100%+ → 60 + 20 = 80."""
        config = _make_config()
        prior = _make_prior(operating_profit=100)
        score, reason = score_earnings(250, prior, config)
        assert score == 80
        assert "100%+" in reason

    def test_yoy_50_plus(self):
        """YoY 50-99% → 60 + 15 = 75."""
        config = _make_config()
        prior = _make_prior(operating_profit=100)
        score, reason = score_earnings(160, prior, config)
        assert score == 75
        assert "50%+" in reason

    def test_yoy_30_plus(self):
        """YoY 30-49% → 60 + 10 = 70."""
        config = _make_config()
        prior = _make_prior(operating_profit=100)
        score, reason = score_earnings(135, prior, config)
        assert score == 70
        assert "30%+" in reason

    def test_yoy_small_increase(self):
        """YoY 0-29% → base only = 60."""
        config = _make_config()
        prior = _make_prior(operating_profit=100)
        score, reason = score_earnings(120, prior, config)
        assert score == 60
        assert "소폭 증가" in reason

    def test_yoy_decrease(self):
        """YoY negative → base only = 60, 감소."""
        config = _make_config()
        prior = _make_prior(operating_profit=100)
        score, reason = score_earnings(80, prior, config)
        assert score == 60
        assert "감소" in reason

    def test_turnaround(self):
        """흑자전환 → 60 + 15 = 75."""
        config = _make_config()
        prior = _make_prior(operating_profit=-100)
        score, reason = score_earnings(50, prior, config)
        assert score == 75
        assert "흑자전환" in reason

    def test_prior_zero(self):
        """전기 0 → YoY 산출 불가, base only."""
        config = _make_config()
        prior = _make_prior(operating_profit=0)
        score, reason = score_earnings(100, prior, config)
        assert score == 60
        assert "산출 불가" in reason

    def test_cap_at_100(self):
        """스코어 100 상한."""
        config = _make_config(base=90, yoy_100=20)
        prior = _make_prior(operating_profit=10)
        score, _ = score_earnings(1000, prior, config)
        assert score == 100


# ── size_hint_from_confidence ──────────────────────────


class TestSizeHint:
    def test_high(self):
        assert size_hint_from_confidence(90) == SizeHint.L

    def test_medium(self):
        assert size_hint_from_confidence(78) == SizeHint.M

    def test_low(self):
        assert size_hint_from_confidence(65) == SizeHint.S


# ── DartEarningsStrategy integration ──────────────────


def _make_disclosure(*, ticker="005930", corp_name="삼성전자", title="삼성전자(005930) 2025년 1분기 잠정실적", rcept_no="20260329000001"):
    return RawDisclosure(
        title=title,
        link=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
        rss_guid=rcept_no,
        published="20260329",
        ticker=ticker,
        corp_name=corp_name,
        detected_at=datetime(2026, 3, 29, 10, 0, 0, tzinfo=timezone.utc),
    )


class TestDartEarningsStrategyProperties:
    def test_name(self):
        config = MagicMock()
        config.dart_earnings_enabled = True
        config.dart_api_key = "test"
        config.data_dir = Path("/tmp")
        session = MagicMock()
        queue = asyncio.Queue()
        strategy = DartEarningsStrategy(config, session, queue)
        assert strategy.name == "dart_earnings"
        assert strategy.source == SignalSource.NEWS
        assert strategy.enabled is True

    def test_disabled(self):
        config = MagicMock()
        config.dart_earnings_enabled = False
        config.dart_api_key = "test"
        config.data_dir = Path("/tmp")
        session = MagicMock()
        queue = asyncio.Queue()
        strategy = DartEarningsStrategy(config, session, queue)
        assert strategy.enabled is False
