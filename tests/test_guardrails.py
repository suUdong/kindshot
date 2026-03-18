"""Tests for guardrails including portfolio-level controls."""

from kindshot.config import Config
from kindshot.guardrails import check_guardrails, GuardrailResult, GuardrailState, get_dynamic_stop_loss_pct
from kindshot.kis_client import OrderbookSnapshot, QuoteRiskState
from kindshot.models import Action


def _cfg(**kw) -> Config:
    return Config(**kw)


def test_all_pass():
    """All data within limits → pass."""
    r = check_guardrails(
        ticker="005930",
        config=_cfg(spread_check_enabled=True),
        spread_bps=10.0,
        adv_value_20d=10e9,
        ret_today=5.0,
    )
    assert r.passed is True
    assert r.reason is None


def test_spread_too_wide():
    r = check_guardrails(
        ticker="005930",
        config=_cfg(spread_check_enabled=True),
        spread_bps=55.0,
        adv_value_20d=10e9,
        ret_today=5.0,
    )
    assert r.passed is False
    assert r.reason == "SPREAD_TOO_WIDE"


def test_spread_missing_pass_default():
    """Default spread_missing_policy='pass' allows None spread."""
    r = check_guardrails(
        ticker="005930",
        config=_cfg(spread_check_enabled=True),
        spread_bps=None,
        adv_value_20d=10e9,
        ret_today=5.0,
    )
    assert r.passed is True


def test_spread_missing_fails_close_when_policy_fail():
    r = check_guardrails(
        ticker="005930",
        config=_cfg(spread_check_enabled=True, spread_missing_policy="fail"),
        spread_bps=None,
        adv_value_20d=10e9,
        ret_today=5.0,
    )
    assert r.passed is False
    assert r.reason == "SPREAD_DATA_MISSING"


def test_spread_check_disabled_skips():
    r = check_guardrails(
        ticker="005930",
        config=_cfg(spread_check_enabled=False),
        spread_bps=None,
        adv_value_20d=10e9,
        ret_today=5.0,
    )
    assert r.passed is True


def test_adv_too_low():
    r = check_guardrails(
        ticker="005930",
        config=_cfg(),
        spread_bps=10.0,
        adv_value_20d=0.5e9,
        ret_today=5.0,
    )
    assert r.passed is False
    assert r.reason == "ADV_TOO_LOW"


def test_adv_missing():
    r = check_guardrails(
        ticker="005930",
        config=_cfg(),
        spread_bps=10.0,
        adv_value_20d=None,
        ret_today=5.0,
    )
    assert r.passed is False
    assert r.reason == "ADV_DATA_MISSING"


def test_extreme_move():
    r = check_guardrails(
        ticker="005930",
        config=_cfg(),
        spread_bps=10.0,
        adv_value_20d=10e9,
        ret_today=25.0,
    )
    assert r.passed is False
    assert r.reason == "EXTREME_MOVE"


def test_ret_today_missing():
    r = check_guardrails(
        ticker="005930",
        config=_cfg(),
        spread_bps=10.0,
        adv_value_20d=10e9,
        ret_today=None,
    )
    assert r.passed is False
    assert r.reason == "RET_TODAY_DATA_MISSING"


def _base_args():
    return dict(adv_value_20d=10e9, ret_today=5.0, spread_bps=10.0)


def test_daily_loss_limit():
    cfg = _cfg(daily_loss_limit=500_000)
    state = GuardrailState(cfg)
    state.record_pnl(-600_000)
    r = check_guardrails("005930", cfg, state=state, **_base_args())
    assert r.passed is False
    assert r.reason == "DAILY_LOSS_LIMIT"


def test_same_stock_rebuy():
    cfg = _cfg()
    state = GuardrailState(cfg)
    state.record_buy("005930")
    r = check_guardrails("005930", cfg, state=state, **_base_args())
    assert r.passed is False
    assert r.reason == "SAME_STOCK_REBUY"


def test_different_stock_allowed():
    cfg = _cfg()
    state = GuardrailState(cfg)
    state.record_buy("005930")
    r = check_guardrails("000660", cfg, state=state, **_base_args())
    assert r.passed is True


def test_max_positions():
    cfg = _cfg(max_positions=2)
    state = GuardrailState(cfg)
    state.record_buy("005930")
    state.record_buy("000660")
    r = check_guardrails("035420", cfg, state=state, **_base_args())
    assert r.passed is False
    assert r.reason == "MAX_POSITIONS"


def test_restricted_stock():
    cfg = _cfg()
    r = check_guardrails("005930", cfg, headline="삼성전자(005930) - 관리종목 지정", **_base_args())
    assert r.passed is False
    assert r.reason == "RESTRICTED_STOCK"


def test_temp_stop_blocks_trade():
    cfg = _cfg()
    r = check_guardrails(
        "005930",
        cfg,
        quote_risk_state=QuoteRiskState(temp_stop_yn="Y"),
        **_base_args(),
    )
    assert r.passed is False
    assert r.reason == "TEMP_STOP"


def test_liquidation_trade_blocks_trade():
    cfg = _cfg()
    r = check_guardrails(
        "005930",
        cfg,
        quote_risk_state=QuoteRiskState(sltr_yn="Y"),
        **_base_args(),
    )
    assert r.passed is False
    assert r.reason == "LIQUIDATION_TRADE"


def test_orderbook_top_level_liquidity_blocks_buy():
    cfg = _cfg(order_size=5_000_000, no_buy_after_kst_hour=23)
    r = check_guardrails(
        "005930",
        cfg,
        orderbook_snapshot=OrderbookSnapshot(
            ask_price1=50_000.0,
            bid_price1=49_900.0,
            ask_size1=50,
            bid_size1=100,
            total_ask_size=500,
            total_bid_size=800,
            spread_bps=20.0,
        ),
        decision_action=Action.BUY,
        **_base_args(),
    )
    assert r.passed is False
    assert r.reason == "ORDERBOOK_TOP_LEVEL_LIQUIDITY"


def test_orderbook_top_level_liquidity_does_not_block_skip():
    cfg = _cfg(order_size=5_000_000)
    r = check_guardrails(
        "005930",
        cfg,
        orderbook_snapshot=OrderbookSnapshot(
            ask_price1=50_000.0,
            bid_price1=49_900.0,
            ask_size1=50,
            bid_size1=100,
            total_ask_size=500,
            total_bid_size=800,
            spread_bps=20.0,
        ),
        decision_action=Action.SKIP,
        **_base_args(),
    )
    assert r.passed is True


def test_intraday_value_ratio_blocks_buy():
    cfg = _cfg(min_intraday_value_vs_adv20d=0.01, no_buy_after_kst_hour=23)
    r = check_guardrails(
        "005930",
        cfg,
        intraday_value_vs_adv20d=0.005,
        decision_action=Action.BUY,
        **_base_args(),
    )
    assert r.passed is False
    assert r.reason == "INTRADAY_VALUE_TOO_THIN"


def test_intraday_value_ratio_does_not_block_skip():
    cfg = _cfg(min_intraday_value_vs_adv20d=0.01)
    r = check_guardrails(
        "005930",
        cfg,
        intraday_value_vs_adv20d=0.005,
        decision_action=Action.SKIP,
        **_base_args(),
    )
    assert r.passed is True


def test_normalized_quote_temp_stop_blocks_without_raw_dataclass():
    cfg = _cfg()
    r = check_guardrails(
        "005930",
        cfg,
        quote_temp_stop=True,
        **_base_args(),
    )
    assert r.passed is False
    assert r.reason == "TEMP_STOP"


def test_normalized_top_ask_notional_blocks_buy_without_raw_dataclass():
    cfg = _cfg(order_size=5_000_000, no_buy_after_kst_hour=23)
    r = check_guardrails(
        "005930",
        cfg,
        top_ask_notional=4_000_000.0,
        decision_action=Action.BUY,
        **_base_args(),
    )
    assert r.passed is False
    assert r.reason == "ORDERBOOK_TOP_LEVEL_LIQUIDITY"


def test_state_reset_daily():
    cfg = _cfg()
    state = GuardrailState(cfg)
    state.record_buy("005930")
    state.record_pnl(-100_000)
    state.reset_daily()
    assert state.position_count == 0
    assert len(state.bought_tickers) == 0
    assert state.daily_pnl == 0.0


def test_sector_concentration():
    cfg = _cfg(max_sector_positions=2)
    state = GuardrailState(cfg)
    state.record_buy("005930", sector="반도체")
    state.record_buy("000660", sector="반도체")
    r = check_guardrails("035420", cfg, state=state, sector="반도체", **_base_args())
    assert r.passed is False
    assert r.reason == "SECTOR_CONCENTRATION"


def test_sector_different_allowed():
    cfg = _cfg(max_sector_positions=2)
    state = GuardrailState(cfg)
    state.record_buy("005930", sector="반도체")
    state.record_buy("000660", sector="반도체")
    r = check_guardrails("035420", cfg, state=state, sector="바이오", **_base_args())
    assert r.passed is True


def test_record_sell_decrements():
    cfg = _cfg()
    state = GuardrailState(cfg)
    state.record_buy("005930", sector="반도체")
    assert state.position_count == 1
    state.record_sell("005930", sector="반도체")
    assert state.position_count == 0
    assert state.sector_positions.get("반도체", 0) == 0


def test_persistence_survives_restart(tmp_path):
    cfg = _cfg()
    state_dir = tmp_path / "state"
    s1 = GuardrailState(cfg, state_dir=state_dir)
    s1.record_buy("005930")
    s1.record_pnl(-200_000)

    s2 = GuardrailState(cfg, state_dir=state_dir)
    assert s2.daily_pnl == -200_000
    assert "005930" in s2.bought_tickers
    assert s2.position_count == 1


def test_persistence_resets_on_new_day(tmp_path):
    import json
    cfg = _cfg()
    state_dir = tmp_path / "state"
    s1 = GuardrailState(cfg, state_dir=state_dir)
    s1.record_buy("005930")

    # Tamper date to simulate yesterday
    path = state_dir / "guardrail_state.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["date"] = "2020-01-01"
    path.write_text(json.dumps(data), encoding="utf-8")

    s2 = GuardrailState(cfg, state_dir=state_dir)
    assert s2.position_count == 0  # not loaded from stale date


def test_chase_buy_blocked():
    """당일 5%+ 상승 종목 BUY → CHASE_BUY_BLOCKED."""
    r = check_guardrails(
        ticker="005930",
        config=_cfg(spread_check_enabled=True, no_buy_after_kst_hour=23),
        spread_bps=10.0,
        adv_value_20d=10e9,
        ret_today=6.0,
        decision_action=Action.BUY,
    )
    assert r.passed is False
    assert r.reason == "CHASE_BUY_BLOCKED"


def test_chase_buy_not_blocked_on_skip():
    """SKIP 결정은 추격 매수 체크 안 함."""
    r = check_guardrails(
        ticker="005930",
        config=_cfg(spread_check_enabled=True),
        spread_bps=10.0,
        adv_value_20d=10e9,
        ret_today=6.0,
        decision_action=Action.SKIP,
    )
    assert r.passed is True


def test_chase_buy_passes_under_threshold():
    """당일 4% 상승은 통과."""
    r = check_guardrails(
        ticker="005930",
        config=_cfg(spread_check_enabled=True, no_buy_after_kst_hour=23),
        spread_bps=10.0,
        adv_value_20d=10e9,
        ret_today=4.0,
        decision_action=Action.BUY,
    )
    assert r.passed is True


def test_low_confidence_blocked():
    """confidence < 70 → LOW_CONFIDENCE."""
    r = check_guardrails(
        ticker="005930",
        config=_cfg(spread_check_enabled=True, min_buy_confidence=70, no_buy_after_kst_hour=23),
        spread_bps=10.0,
        adv_value_20d=10e9,
        ret_today=2.0,
        decision_action=Action.BUY,
        decision_confidence=65,
    )
    assert r.passed is False
    assert r.reason == "LOW_CONFIDENCE"


def test_high_confidence_passes():
    """confidence >= 70 → 통과."""
    r = check_guardrails(
        ticker="005930",
        config=_cfg(spread_check_enabled=True, min_buy_confidence=70, no_buy_after_kst_hour=23),
        spread_bps=10.0,
        adv_value_20d=10e9,
        ret_today=2.0,
        decision_action=Action.BUY,
        decision_confidence=75,
    )
    assert r.passed is True


# ── v3 guardrails 테스트 ──────────────────

def test_consecutive_stop_loss_blocks_buy():
    """3연속 손절 시 BUY 차단."""
    cfg = _cfg(no_buy_after_kst_hour=23)
    state = GuardrailState(cfg)
    state.record_stop_loss()
    state.record_stop_loss()
    state.record_stop_loss()
    assert state.consecutive_stop_losses == 3
    r = check_guardrails(
        "005930", cfg, state=state, decision_action=Action.BUY, **_base_args()
    )
    assert r.passed is False
    assert r.reason == "CONSECUTIVE_STOP_LOSS"


def test_consecutive_stop_loss_allows_under_threshold():
    """2연속 손절은 BUY 허용."""
    cfg = _cfg(no_buy_after_kst_hour=23)
    state = GuardrailState(cfg)
    state.record_stop_loss()
    state.record_stop_loss()
    assert state.consecutive_stop_losses == 2
    r = check_guardrails(
        "005930", cfg, state=state, decision_action=Action.BUY, **_base_args()
    )
    assert r.passed is True


def test_consecutive_stop_loss_resets_on_profit():
    """수익 청산 시 연속 손절 카운터 리셋."""
    cfg = _cfg(no_buy_after_kst_hour=23)
    state = GuardrailState(cfg)
    state.record_stop_loss()
    state.record_stop_loss()
    state.record_stop_loss()
    state.record_profitable_exit()
    assert state.consecutive_stop_losses == 0
    r = check_guardrails(
        "005930", cfg, state=state, decision_action=Action.BUY, **_base_args()
    )
    assert r.passed is True


def test_consecutive_stop_loss_resets_daily():
    """일일 리셋 시 연속 손절 카운터도 리셋."""
    cfg = _cfg()
    state = GuardrailState(cfg)
    state.record_stop_loss()
    state.record_stop_loss()
    state.record_stop_loss()
    state.reset_daily()
    assert state.consecutive_stop_losses == 0


def test_dynamic_stop_loss_high_confidence():
    """confidence>=85 시 SL -2.0% (완화)."""
    cfg = _cfg(paper_stop_loss_pct=-1.5)
    sl = get_dynamic_stop_loss_pct(cfg, confidence=90)
    assert sl == -2.0


def test_dynamic_stop_loss_normal_confidence():
    """confidence<85 시 기본 SL."""
    cfg = _cfg(paper_stop_loss_pct=-1.5)
    sl = get_dynamic_stop_loss_pct(cfg, confidence=75)
    assert sl == -1.5


def test_consecutive_stop_loss_does_not_block_skip():
    """SKIP 결정은 연속 손절 체크 안 함."""
    cfg = _cfg()
    state = GuardrailState(cfg)
    state.record_stop_loss()
    state.record_stop_loss()
    state.record_stop_loss()
    r = check_guardrails(
        "005930", cfg, state=state, decision_action=Action.SKIP, **_base_args()
    )
    assert r.passed is True
