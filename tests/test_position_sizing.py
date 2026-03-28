"""Tests for calculate_position_size() in guardrails.py."""

import pytest
from kindshot.config import Config
from kindshot.guardrails import calculate_position_size


@pytest.fixture
def cfg():
    return Config(
        order_size=5_000_000,
        order_size_l=7_000_000,
        order_size_s=3_000_000,
        account_risk_pct=2.0,
        minute_volume_cap_pct=5.0,
        ask_depth_cap_pct=10.0,
        paper_stop_loss_pct=-1.5,
    )


def test_basic_hint_size(cfg):
    """기본: hint 기반 사이즈만 적용 (다른 제약 없음)."""
    result = calculate_position_size(cfg, "M")
    assert result == 5_000_000


def test_hint_l_size(cfg):
    """L hint는 7M 반환."""
    result = calculate_position_size(cfg, "L")
    assert result == 7_000_000


def test_account_risk_limits(cfg):
    """계좌 리스크 제한이 hint보다 작으면 account 기반 사이즈 사용."""
    # account_risk_pct=2%, SL=-1.5% → risk=200만, position=200만/0.015=133.3M
    # 이건 hint(5M)보다 크므로 hint가 적용됨
    result = calculate_position_size(cfg, "M", account_balance=100_000_000)
    assert result == 5_000_000

    # 작은 계좌: 1M → risk=2만, position=2만/0.015=1.33M < 5M
    result = calculate_position_size(cfg, "M", account_balance=1_000_000)
    assert result == pytest.approx(1_333_333.33, rel=1e-2)


def test_minute_volume_cap(cfg):
    """1분 거래대금 cap이 hint보다 작으면 그 값 사용."""
    # minute_volume=50M, cap=5% → 2.5M < 5M(hint)
    result = calculate_position_size(cfg, "M", minute_volume=50_000_000)
    assert result == 2_500_000


def test_ask_depth_cap(cfg):
    """매도 호가 잔량 cap이 hint보다 작으면 그 값 사용."""
    # ask_depth=20M, cap=10% → 2M < 5M(hint)
    result = calculate_position_size(cfg, "M", ask_depth_notional=20_000_000)
    assert result == 2_000_000


def test_composite_min_of_all(cfg):
    """모든 제약이 동시에 적용되면 최솟값 선택."""
    result = calculate_position_size(
        cfg,
        "L",  # 7M
        account_balance=1_000_000,  # → ~1.33M
        minute_volume=50_000_000,   # → 2.5M
        ask_depth_notional=20_000_000,  # → 2M
        macro_position_multiplier=0.8,  # 7M * 0.8 = 5.6M
    )
    # min(5.6M, 1.33M, 2.5M, 2M) = 1.33M
    assert result == pytest.approx(1_333_333.33, rel=1e-2)


def test_macro_multiplier_scales_hint(cfg):
    """macro_position_multiplier가 hint 사이즈에 적용."""
    result = calculate_position_size(cfg, "M", macro_position_multiplier=0.5)
    assert result == 2_500_000


def test_zero_account_balance_ignored(cfg):
    """account_balance=0이면 계좌 리스크 제약 미적용."""
    result = calculate_position_size(cfg, "M", account_balance=0)
    assert result == 5_000_000


# ── v76: ATR 기반 변동성 정규화 테스트 ──


def test_atr_high_volatility_shrinks(cfg):
    """ATR > base(2.0%)이면 포지션 축소."""
    # ATR=4.0%, base=2.0% → scale=0.5 → 5M * 0.5 = 2.5M
    result = calculate_position_size(cfg, "M", atr_pct=4.0)
    assert result == pytest.approx(2_500_000)


def test_atr_low_volatility_expands(cfg):
    """ATR < base(2.0%)이면 포지션 확대 (최대 1.3x)."""
    # ATR=1.0%, base=2.0% → scale=2.0 → capped at 1.3 → 5M * 1.3 = 6.5M
    result = calculate_position_size(cfg, "M", atr_pct=1.0)
    assert result == pytest.approx(6_500_000)


def test_atr_at_base_no_change(cfg):
    """ATR == base(2.0%)이면 scale=1.0 → 변화 없음."""
    result = calculate_position_size(cfg, "M", atr_pct=2.0)
    assert result == 5_000_000


def test_atr_none_backward_compatible(cfg):
    """atr_pct=None이면 기존 로직과 동일."""
    result = calculate_position_size(cfg, "M", atr_pct=None)
    assert result == 5_000_000


def test_atr_extreme_high_has_floor(cfg):
    """극단적 고변동성(ATR=10%)에도 50% 하한 적용."""
    # ATR=10%, base=2.0% → scale=0.2 → floored at 0.5 → 5M * 0.5 = 2.5M
    result = calculate_position_size(cfg, "M", atr_pct=10.0)
    assert result == pytest.approx(2_500_000)
