"""Tests for guardrails including portfolio-level controls."""

import pytest
from datetime import datetime, timedelta, timezone

from kindshot.config import Config
from kindshot.guardrails import (
    check_guardrails, GuardrailResult, GuardrailState,
    get_dynamic_stop_loss_pct, get_dynamic_tp_pct,
    apply_adv_confidence_adjustment, apply_market_confidence_adjustment,
    apply_delay_confidence_adjustment, apply_price_reaction_adjustment,
    apply_volume_confidence_adjustment, apply_volume_ratio_confidence_adjustment,
    apply_sector_momentum_confidence_adjustment,
    apply_dorg_confidence_adjustment,
    apply_time_session_confidence_adjustment,
    apply_trend_confidence_adjustment,
    apply_technical_confidence_adjustment,
    apply_headline_quality_adjustment,
    calculate_position_size,
    downgrade_size_hint, get_kill_switch_size_hint,
    resolve_dynamic_guardrail_profile,
    resolve_daily_loss_budget,
)
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
        adv_value_20d=0.3e9,
        ret_today=5.0,
    )
    assert r.passed is False
    assert r.reason == "ADV_TOO_LOW"


def test_adv_override_allows_pos_strong_mid_liquidity_name():
    cfg = _cfg(adv_threshold=5_000_000_000, pos_strong_adv_threshold=2_000_000_000)
    r = check_guardrails(
        ticker="005930",
        config=cfg,
        spread_bps=10.0,
        adv_value_20d=2.5e9,
        ret_today=2.0,
        adv_threshold=cfg.adv_threshold_for_bucket("POS_STRONG"),
    )
    assert r.passed is True


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
    return dict(adv_value_20d=10e9, ret_today=2.0, spread_bps=10.0)


def _kst_dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 3, 24, hour, minute, 0, tzinfo=timezone(timedelta(hours=9)))


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
    cfg = _cfg(order_size=5_000_000, no_buy_after_kst_hour=24)
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
    cfg = _cfg(min_intraday_value_vs_adv20d=0.01, no_buy_after_kst_hour=24)
    r = check_guardrails(
        "005930",
        cfg,
        intraday_value_vs_adv20d=0.005,
        decision_action=Action.BUY,
        decision_time_kst=_kst_dt(10, 30),  # 장중 시간 고정
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


def test_entry_delay_too_late_blocks_buy():
    cfg = _cfg(max_entry_delay_ms=60_000, no_buy_after_kst_hour=24)
    r = check_guardrails(
        "005930",
        cfg,
        delay_ms=90_000,
        decision_action=Action.BUY,
        **_base_args(),
    )
    assert r.passed is False
    assert r.reason == "ENTRY_DELAY_TOO_LATE"


def test_orderbook_imbalance_blocks_buy():
    cfg = _cfg(orderbook_bid_ask_ratio_min=0.8, no_buy_after_kst_hour=24)
    r = check_guardrails(
        "005930",
        cfg,
        orderbook_snapshot=OrderbookSnapshot(
            ask_price1=50_000.0,
            bid_price1=49_900.0,
            ask_size1=200,
            bid_size1=100,
            total_ask_size=5_000,
            total_bid_size=3_000,
            spread_bps=20.0,
        ),
        decision_action=Action.BUY,
        decision_size_hint="S",
        **_base_args(),
    )
    assert r.passed is False
    assert r.reason == "ORDERBOOK_IMBALANCE"


def test_prior_volume_gate_blocks_buy_after_gate_start():
    cfg = _cfg(
        min_prior_volume_rate=70.0,
        prior_volume_gate_start_kst_hour=10,
        prior_volume_gate_start_kst_minute=0,
        no_buy_after_kst_hour=24,
    )
    r = check_guardrails(
        "005930",
        cfg,
        prior_volume_rate=45.0,
        decision_action=Action.BUY,
        decision_time_kst=_kst_dt(10, 5),
        **_base_args(),
    )
    assert r.passed is False
    assert r.reason == "PRIOR_VOLUME_TOO_THIN"


def test_prior_volume_gate_ignores_pre_gate_session():
    cfg = _cfg(
        min_prior_volume_rate=70.0,
        prior_volume_gate_start_kst_hour=10,
        prior_volume_gate_start_kst_minute=0,
        no_buy_after_kst_hour=24,
    )
    r = check_guardrails(
        "005930",
        cfg,
        prior_volume_rate=0.0,
        decision_action=Action.BUY,
        decision_time_kst=_kst_dt(9, 15),
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
    cfg = _cfg(order_size=5_000_000, no_buy_after_kst_hour=24)
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


def test_record_sell_recovers_persisted_sector_when_not_provided():
    cfg = _cfg()
    state = GuardrailState(cfg)
    state.record_buy("005930", sector="반도체")
    state.record_sell("005930")
    assert state.position_count == 0
    assert state.sector_positions == {}


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
    """당일 3%+ 상승 종목 BUY → CHASE_BUY_BLOCKED."""
    r = check_guardrails(
        ticker="005930",
        config=_cfg(spread_check_enabled=True, no_buy_after_kst_hour=24),
        spread_bps=10.0,
        adv_value_20d=10e9,
        ret_today=3.5,
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
    """당일 2% 상승은 통과."""
    r = check_guardrails(
        ticker="005930",
        config=_cfg(spread_check_enabled=True, no_buy_after_kst_hour=24),
        spread_bps=10.0,
        adv_value_20d=10e9,
        ret_today=2.5,
        decision_action=Action.BUY,
    )
    assert r.passed is True


def test_low_confidence_blocked():
    """confidence < 70 → LOW_CONFIDENCE."""
    r = check_guardrails(
        ticker="005930",
        config=_cfg(spread_check_enabled=True, min_buy_confidence=70, no_buy_after_kst_hour=24),
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
        config=_cfg(spread_check_enabled=True, min_buy_confidence=70, no_buy_after_kst_hour=24),
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
    cfg = _cfg(no_buy_after_kst_hour=24)
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
    cfg = _cfg(no_buy_after_kst_hour=24)
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
    cfg = _cfg(no_buy_after_kst_hour=24)
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
    """v65: confidence>=85 시 기본 SL의 2.0배 (V자 반등 허용). hold=20 (표준)."""
    cfg = _cfg(paper_stop_loss_pct=-1.5)
    sl = get_dynamic_stop_loss_pct(cfg, confidence=90, hold_minutes=20)
    assert sl == -1.5 * 2.0  # -3.0


def test_dynamic_stop_loss_mid_confidence():
    """v65: conf 80-84 → 기본 SL의 1.33배. hold=20 (표준)."""
    cfg = _cfg(paper_stop_loss_pct=-1.5)
    sl = get_dynamic_stop_loss_pct(cfg, confidence=82, hold_minutes=20)
    assert sl == pytest.approx(-1.5 * 1.33)  # -1.995


def test_dynamic_stop_loss_normal_confidence():
    """v65: conf 75-79 → 기본 SL 그대로 -1.5%. hold=20 (표준)."""
    cfg = _cfg(paper_stop_loss_pct=-1.5)
    sl = get_dynamic_stop_loss_pct(cfg, confidence=75, hold_minutes=20)
    assert sl == -1.5  # base 그대로


def test_dynamic_stop_loss_eod_hold():
    """v65: hold_minutes=0 (EOD, 자사주소각 등): SL 1.3배 완화."""
    cfg = _cfg(paper_stop_loss_pct=-1.5)
    # conf=85, hold=0 → base=-3.0 * 1.3 = -3.9
    sl = get_dynamic_stop_loss_pct(cfg, confidence=85, hold_minutes=0)
    assert sl == pytest.approx(-1.5 * 2.0 * 1.3)
    # conf=80, hold=0 → base=-1.995 * 1.3 = -2.5935
    sl_mid = get_dynamic_stop_loss_pct(cfg, confidence=80, hold_minutes=0)
    assert sl_mid == pytest.approx(-1.5 * 1.33 * 1.3)


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


def test_guardrail_state_persist_atomic(tmp_path):
    """State persists to disk via atomic write (tmp + rename)."""
    cfg = _cfg()
    state = GuardrailState(cfg, state_dir=tmp_path)
    state._last_kst_date = "2026-03-23"
    state.record_buy("005930")
    state.record_pnl(-50000)

    # File should exist and no .tmp remnant
    state_file = tmp_path / "guardrail_state.json"
    tmp_file = tmp_path / "guardrail_state.tmp"
    assert state_file.exists()
    assert not tmp_file.exists()

    import json
    data = json.loads(state_file.read_text())
    assert "005930" in data["bought_tickers"]
    assert data["daily_pnl"] == -50000
    assert data["position_count"] == 1


def test_guardrail_state_reload(tmp_path):
    """State reloads correctly from persisted file on same KST date."""
    import json
    from datetime import datetime, timedelta, timezone
    _KST = timezone(timedelta(hours=9))

    cfg = _cfg()
    today = datetime.now(_KST).strftime("%Y-%m-%d")

    # Write state file with today's date so it will be loaded
    state_file = tmp_path / "guardrail_state.json"
    data = {
        "date": today,
        "daily_pnl": -100000,
        "bought_tickers": ["005930", "035720"],
        "position_count": 2,
        "sector_positions": {},
        "ticker_sectors": {"005930": "반도체"},
        "consecutive_stop_losses": 1,
        "recent_trade_outcomes": [True, False],
    }
    state_file.write_text(json.dumps(data))

    # New instance should reload from disk
    state2 = GuardrailState(cfg, state_dir=tmp_path)
    assert state2.position_count == 2
    assert "005930" in state2.bought_tickers
    assert state2.daily_pnl == -100000
    assert state2.consecutive_stop_losses == 1
    assert state2.recent_trade_outcomes == [True, False]


def test_resolve_daily_loss_budget_tightens_after_low_recent_win_rate():
    cfg = _cfg(
        daily_loss_limit=1_000_000,
        dynamic_daily_loss_enabled=True,
        dynamic_daily_loss_recent_trade_window=4,
        dynamic_daily_loss_recent_trade_min_samples=3,
        dynamic_daily_loss_low_win_rate_threshold=0.5,
        dynamic_daily_loss_low_win_rate_multiplier=0.75,
        dynamic_daily_loss_zero_win_rate_multiplier=0.5,
    )
    state = GuardrailState(cfg)
    state.record_profitable_exit()
    state.record_stop_loss()
    state.record_stop_loss()
    budget = resolve_daily_loss_budget(cfg, state)
    assert budget.recent_closed_trades == 3
    assert budget.recent_win_rate == pytest.approx(1 / 3)
    assert budget.recent_win_rate_multiplier == pytest.approx(0.75)
    assert budget.effective_floor_won == pytest.approx(-750000)


def test_resolve_daily_loss_budget_tightens_harder_after_zero_recent_win_rate():
    cfg = _cfg(
        daily_loss_limit=1_000_000,
        dynamic_daily_loss_enabled=True,
        dynamic_daily_loss_recent_trade_window=4,
        dynamic_daily_loss_recent_trade_min_samples=3,
        dynamic_daily_loss_low_win_rate_threshold=0.5,
        dynamic_daily_loss_low_win_rate_multiplier=0.75,
        dynamic_daily_loss_zero_win_rate_multiplier=0.5,
    )
    state = GuardrailState(cfg)
    state.record_stop_loss()
    state.record_stop_loss()
    state.record_stop_loss()
    budget = resolve_daily_loss_budget(cfg, state)
    assert budget.recent_win_rate == pytest.approx(0.0)
    assert budget.recent_win_rate_multiplier == pytest.approx(0.5)
    assert budget.effective_floor_won == pytest.approx(-500000)


def test_resolve_daily_loss_budget_ignores_recent_win_rate_without_enough_samples():
    cfg = _cfg(
        daily_loss_limit=1_000_000,
        dynamic_daily_loss_enabled=True,
        dynamic_daily_loss_recent_trade_window=4,
        dynamic_daily_loss_recent_trade_min_samples=3,
        dynamic_daily_loss_low_win_rate_threshold=0.5,
        dynamic_daily_loss_low_win_rate_multiplier=0.75,
    )
    state = GuardrailState(cfg)
    state.record_stop_loss()
    state.record_profitable_exit()
    budget = resolve_daily_loss_budget(cfg, state)
    assert budget.recent_closed_trades == 2
    assert budget.recent_win_rate_multiplier == pytest.approx(1.0)
    assert budget.effective_floor_won == pytest.approx(-1_000_000)


# ── 킬 스위치 테스트 ──────────────────

def test_downgrade_size_hint():
    """L→M, M→S, S→S."""
    assert downgrade_size_hint("L") == "M"
    assert downgrade_size_hint("M") == "S"
    assert downgrade_size_hint("S") == "S"


def test_kill_switch_2_losses_downgrades():
    """2연패 시 size_hint 한단계 다운."""
    cfg = _cfg(consecutive_loss_size_down=2)
    state = GuardrailState(cfg)
    state.record_stop_loss()
    state.record_stop_loss()
    assert get_kill_switch_size_hint(cfg, state, "L") == "M"
    assert get_kill_switch_size_hint(cfg, state, "M") == "S"
    assert get_kill_switch_size_hint(cfg, state, "S") == "S"


def test_kill_switch_1_loss_no_downgrade():
    """1연패 시 다운그레이드 없음."""
    cfg = _cfg(consecutive_loss_size_down=2)
    state = GuardrailState(cfg)
    state.record_stop_loss()
    assert get_kill_switch_size_hint(cfg, state, "L") == "L"


def test_kill_switch_no_state():
    """state 없으면 원래 hint 유지."""
    cfg = _cfg()
    assert get_kill_switch_size_hint(cfg, None, "L") == "L"


def test_kill_switch_3_losses_blocks_buy():
    """3연패 시 BUY 차단 (configurable halt threshold)."""
    cfg = _cfg(consecutive_loss_halt=3, no_buy_after_kst_hour=24)
    state = GuardrailState(cfg)
    for _ in range(3):
        state.record_stop_loss()
    r = check_guardrails("005930", cfg, state=state, decision_action=Action.BUY, **_base_args())
    assert r.passed is False
    assert r.reason == "CONSECUTIVE_STOP_LOSS"


def test_midday_spread_blocks_buy():
    """11:00~14:00 비유동 시간대: spread 기준 70% 강화."""
    from unittest.mock import patch
    from datetime import datetime, timedelta, timezone
    _KST = timezone(timedelta(hours=9))
    midday = datetime(2026, 3, 24, 12, 0, 0, tzinfo=_KST)
    cfg = _cfg(spread_check_enabled=True, spread_bps_limit=50.0, no_buy_after_kst_hour=15)
    with patch("kindshot.guardrails.datetime") as mock_dt:
        mock_dt.now.return_value = midday
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        # spread 40bps: 정상이면 통과지만 midday 70% 적용 → 한도 35bps → 차단
        r = check_guardrails(
            "005930", cfg, spread_bps=40.0,
            adv_value_20d=10e9, ret_today=2.0,
            decision_action=Action.BUY,
        )
    assert r.passed is False
    assert r.reason == "MIDDAY_SPREAD_TOO_WIDE"


def test_midday_spread_passes_low_spread():
    """11:00~14:00 시간대라도 spread 낮으면 통과."""
    from unittest.mock import patch
    from datetime import datetime, timedelta, timezone
    _KST = timezone(timedelta(hours=9))
    midday = datetime(2026, 3, 24, 12, 0, 0, tzinfo=_KST)
    cfg = _cfg(spread_check_enabled=True, spread_bps_limit=50.0, no_buy_after_kst_hour=15)
    with patch("kindshot.guardrails.datetime") as mock_dt:
        mock_dt.now.return_value = midday
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        r = check_guardrails(
            "005930", cfg, spread_bps=30.0,
            adv_value_20d=10e9, ret_today=2.0,
            decision_action=Action.BUY,
        )
    assert r.passed is True


def test_non_midday_spread_normal_limit():
    """10:00 (비유동 시간대 아님): 정상 spread 한도 적용."""
    from unittest.mock import patch
    from datetime import datetime, timedelta, timezone
    _KST = timezone(timedelta(hours=9))
    morning = datetime(2026, 3, 24, 10, 0, 0, tzinfo=_KST)
    cfg = _cfg(spread_check_enabled=True, spread_bps_limit=50.0, no_buy_after_kst_hour=15)
    with patch("kindshot.guardrails.datetime") as mock_dt:
        mock_dt.now.return_value = morning
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        r = check_guardrails(
            "005930", cfg, spread_bps=40.0,
            adv_value_20d=10e9, ret_today=2.0,
            decision_action=Action.BUY,
        )
    assert r.passed is True


def test_kill_switch_configurable_halt_at_2():
    """consecutive_loss_halt=2 이면 2연패에서 차단."""
    cfg = _cfg(consecutive_loss_halt=2, no_buy_after_kst_hour=24)
    state = GuardrailState(cfg)
    state.record_stop_loss()
    state.record_stop_loss()
    r = check_guardrails("005930", cfg, state=state, decision_action=Action.BUY, **_base_args())
    assert r.passed is False
    assert r.reason == "CONSECUTIVE_STOP_LOSS"


# ── US-001: 동적 TP 테스트 ──────────────────

def test_dynamic_tp_high_confidence():
    """v65: conf>=85, hold=25 (표준, 20분 이상) → TP 3.0%."""
    cfg = _cfg()
    assert get_dynamic_tp_pct(cfg, 90, hold_minutes=25) == 3.0
    assert get_dynamic_tp_pct(cfg, 85, hold_minutes=25) == 3.0


def test_dynamic_tp_mid_confidence():
    """v65: conf 80-84 → TP 2.0%, conf 75-79 → TP 1.5%. hold=25 (표준, 20분 이상)."""
    cfg = _cfg()
    assert get_dynamic_tp_pct(cfg, 80, hold_minutes=25) == 2.0
    assert get_dynamic_tp_pct(cfg, 75, hold_minutes=25) == 1.5


def test_dynamic_tp_eod_hold():
    """v65: hold_minutes=0 (EOD, 자사주소각): TP 1.5배 — 트렌드 수익 극대화."""
    cfg = _cfg()
    assert get_dynamic_tp_pct(cfg, 85, hold_minutes=0) == 3.0 * 1.5  # 4.5
    assert get_dynamic_tp_pct(cfg, 80, hold_minutes=0) == 2.0 * 1.5  # 3.0


def test_dynamic_tp_short_hold():
    """v65: hold_minutes=20 (수주/공급계약): TP 0.9배 — 반전 리스크 대응."""
    cfg = _cfg()
    assert get_dynamic_tp_pct(cfg, 85, hold_minutes=20) == pytest.approx(3.0 * 0.9)  # 2.7
    assert get_dynamic_tp_pct(cfg, 80, hold_minutes=20) == pytest.approx(2.0 * 0.9)  # 1.8


def test_dynamic_tp_low_confidence():
    """v65: conf<75 → config 기본값 (2.0%). hold=25 (표준, 20분 이상)."""
    cfg = _cfg(paper_take_profit_pct=2.0)
    assert get_dynamic_tp_pct(cfg, 70, hold_minutes=25) == 2.0


# ── v74: 변동성 레짐 기반 TP/SL 조정 테스트 ──────────────


def test_dynamic_stop_loss_high_volatility():
    """HIGH volatility → SL 30% 넓게."""
    cfg = _cfg(paper_stop_loss_pct=-1.5)
    normal_sl = get_dynamic_stop_loss_pct(cfg, 80, hold_minutes=20, volatility_regime="normal")
    high_sl = get_dynamic_stop_loss_pct(cfg, 80, hold_minutes=20, volatility_regime="high")
    assert high_sl < normal_sl  # 더 큰 음수 = 더 넓은 SL
    assert high_sl == pytest.approx(normal_sl * 1.3, rel=1e-6)


def test_dynamic_stop_loss_low_volatility():
    """LOW volatility → SL 20% 타이트."""
    cfg = _cfg(paper_stop_loss_pct=-1.5)
    normal_sl = get_dynamic_stop_loss_pct(cfg, 80, hold_minutes=20, volatility_regime="normal")
    low_sl = get_dynamic_stop_loss_pct(cfg, 80, hold_minutes=20, volatility_regime="low")
    assert low_sl > normal_sl  # 덜 음수 = 타이트
    assert low_sl == pytest.approx(normal_sl * 0.8, rel=1e-6)


def test_dynamic_tp_high_volatility():
    """HIGH volatility → TP 30% 넓게."""
    cfg = _cfg(paper_take_profit_pct=2.0)
    normal_tp = get_dynamic_tp_pct(cfg, 85, hold_minutes=20, volatility_regime="normal")
    high_tp = get_dynamic_tp_pct(cfg, 85, hold_minutes=20, volatility_regime="high")
    assert high_tp > normal_tp
    assert high_tp == pytest.approx(normal_tp * 1.3, rel=1e-6)


def test_dynamic_tp_low_volatility():
    """LOW volatility → TP 20% 보수적."""
    cfg = _cfg(paper_take_profit_pct=2.0)
    normal_tp = get_dynamic_tp_pct(cfg, 85, hold_minutes=20, volatility_regime="normal")
    low_tp = get_dynamic_tp_pct(cfg, 85, hold_minutes=20, volatility_regime="low")
    assert low_tp < normal_tp
    assert low_tp == pytest.approx(normal_tp * 0.8, rel=1e-6)


def test_dynamic_tpsl_normal_regime_unchanged():
    """NORMAL volatility → 기존 값 그대로."""
    cfg = _cfg(paper_stop_loss_pct=-1.5, paper_take_profit_pct=2.0)
    sl_default = get_dynamic_stop_loss_pct(cfg, 80, hold_minutes=20)
    sl_normal = get_dynamic_stop_loss_pct(cfg, 80, hold_minutes=20, volatility_regime="normal")
    assert sl_default == sl_normal
    tp_default = get_dynamic_tp_pct(cfg, 80, hold_minutes=20)
    tp_normal = get_dynamic_tp_pct(cfg, 80, hold_minutes=20, volatility_regime="normal")
    assert tp_default == tp_normal


# ── US-003: ADV confidence 조정 테스트 ──────────────────

def test_adv_confidence_mega_cap():
    """ADV 5000억+ → cap 65 (sell the news)."""
    assert apply_adv_confidence_adjustment(85, 600_000_000_000) == 65
    assert apply_adv_confidence_adjustment(60, 600_000_000_000) == 60  # 이미 65 미만


def test_adv_confidence_large_cap():
    """ADV 2000~5000억 → -5, cap 76 (강한 촉매 통과 허용)."""
    assert apply_adv_confidence_adjustment(80, 300_000_000_000) == 75  # 80-5=75, cap 76 이내
    assert apply_adv_confidence_adjustment(85, 300_000_000_000) == 76  # 85-5=80, cap 76


def test_adv_confidence_mid_cap():
    """ADV 500~2000억 → +3 보너스 (소형주 최적 구간)."""
    assert apply_adv_confidence_adjustment(80, 100_000_000_000) == 83


def test_adv_confidence_mid_cap_cap_at_100():
    """ADV 500~2000억 보너스: confidence 98 + 3 = 100 (cap)."""
    assert apply_adv_confidence_adjustment(98, 100_000_000_000) == 100


def test_adv_confidence_micro_cap():
    """ADV <500억 → 조정 없음."""
    assert apply_adv_confidence_adjustment(80, 30_000_000_000) == 80


# ── Market confidence adjustment 테스트 ──────────────────

def test_market_confidence_flat():
    """지수 보합 → 조정 없음."""
    assert apply_market_confidence_adjustment(80, 0.1, 0.2) == 80

def test_market_confidence_mild_down():
    """지수 -0.5~-1% → -2."""
    assert apply_market_confidence_adjustment(80, -0.7, 0.1) == 78

def test_market_confidence_moderate_down():
    """지수 -1~-2% → -3."""
    assert apply_market_confidence_adjustment(80, -1.5, -0.3) == 77

def test_market_confidence_heavy_down():
    """지수 -2~-3% → -4."""
    assert apply_market_confidence_adjustment(80, -2.5, -1.8) == 76

def test_market_confidence_severe_down():
    """지수 -3% 이하 → -5 (기존 -8에서 완화)."""
    assert apply_market_confidence_adjustment(80, -3.5, -2.0) == 75

def test_market_confidence_worst_of_both():
    """두 지수 중 더 나쁜 쪽 기준."""
    assert apply_market_confidence_adjustment(80, -0.3, -1.5) == 77

def test_market_confidence_none_values():
    """지수 데이터 없으면 조정 없음."""
    assert apply_market_confidence_adjustment(80, None, None) == 80

def test_market_confidence_one_none():
    """한쪽만 있으면 해당 값 기준."""
    assert apply_market_confidence_adjustment(80, -1.5, None) == 77


# ── US-004: 포지션 사이징 테스트 ──────────────────

def test_position_size_hint_only():
    """제약 없을 때 hint size 반환."""
    cfg = _cfg(order_size=5_000_000)
    assert calculate_position_size(cfg, "M") == 5_000_000


def test_position_size_minute_volume_cap():
    """1분 거래대금 5% 제약."""
    cfg = _cfg(order_size=5_000_000, minute_volume_cap_pct=5.0)
    size = calculate_position_size(cfg, "M", minute_volume=50_000_000)
    assert size == 2_500_000  # 50M * 5% = 2.5M < 5M


def test_position_size_ask_depth_cap():
    """매도 호가 잔량 10% 제약."""
    cfg = _cfg(order_size=5_000_000, ask_depth_cap_pct=10.0)
    size = calculate_position_size(cfg, "M", ask_depth_notional=30_000_000)
    assert size == 3_000_000  # 30M * 10% = 3M < 5M


def test_position_size_min_of_all():
    """모든 제약 중 최소값 선택."""
    cfg = _cfg(order_size=5_000_000, minute_volume_cap_pct=5.0, ask_depth_cap_pct=10.0)
    size = calculate_position_size(
        cfg, "M",
        minute_volume=50_000_000,  # 2.5M
        ask_depth_notional=20_000_000,  # 2M
    )
    assert size == 2_000_000  # min(5M, 2.5M, 2M)


# ── US-005: 일일 손실 % 제한 테스트 ──────────────────

def test_daily_loss_limit_pct():
    """계좌 대비 -1% 도달 시 차단."""
    cfg = _cfg(daily_loss_limit=10_000_000, daily_loss_limit_pct=-1.0)
    state = GuardrailState(cfg, account_balance=10_000_000)
    state.record_pnl(-100_000)  # -1% of 10M
    r = check_guardrails("005930", cfg, state=state, **_base_args())
    assert r.passed is False
    assert r.reason == "DAILY_LOSS_LIMIT_PCT"


def test_daily_loss_limit_pct_passes_under():
    """-0.5%는 통과."""
    cfg = _cfg(daily_loss_limit=10_000_000, daily_loss_limit_pct=-1.0)
    state = GuardrailState(cfg, account_balance=10_000_000)
    state.record_pnl(-50_000)  # -0.5%
    r = check_guardrails("005930", cfg, state=state, **_base_args())
    assert r.passed is True


def test_resolve_daily_loss_budget_tightens_after_loss_streak():
    cfg = _cfg(
        daily_loss_limit=1_000_000,
        dynamic_daily_loss_enabled=True,
        dynamic_daily_loss_size_down_multiplier=0.75,
        dynamic_daily_loss_halt_multiplier=0.5,
        consecutive_loss_size_down=2,
        consecutive_loss_halt=3,
    )
    state = GuardrailState(cfg)
    state.record_stop_loss()
    state.record_stop_loss()
    budget = resolve_daily_loss_budget(cfg, state)
    assert budget.effective_floor_won == pytest.approx(-750000)
    assert budget.streak_multiplier == pytest.approx(0.75)


def test_resolve_daily_loss_budget_locks_part_of_profit():
    cfg = _cfg(
        daily_loss_limit=1_000_000,
        dynamic_daily_loss_enabled=True,
        dynamic_daily_loss_profit_lock_ratio=0.5,
    )
    state = GuardrailState(cfg)
    state.record_pnl(600_000)
    budget = resolve_daily_loss_budget(cfg, state)
    assert budget.effective_floor_won == pytest.approx(0.0)
    assert budget.remaining_budget_won == pytest.approx(600000)


# ── US-006: 시간대별 confidence 문턱 테스트 ──────────────────

def test_opening_low_confidence_blocked():
    """09:00-09:30: conf<80 → 차단."""
    from unittest.mock import patch
    from datetime import datetime, timedelta, timezone
    _KST = timezone(timedelta(hours=9))
    opening = datetime(2026, 3, 24, 9, 15, 0, tzinfo=_KST)
    cfg = _cfg(no_buy_after_kst_hour=15, opening_min_confidence=80)
    with patch("kindshot.guardrails.datetime") as mock_dt:
        mock_dt.now.return_value = opening
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        r = check_guardrails(
            "005930", cfg, spread_bps=10.0, adv_value_20d=10e9, ret_today=1.0,
            decision_action=Action.BUY, decision_confidence=79,
        )
    assert r.passed is False
    assert r.reason == "OPENING_LOW_CONFIDENCE"


def test_opening_high_confidence_passes():
    """09:00-09:30: conf>=80 → 통과."""
    from unittest.mock import patch
    from datetime import datetime, timedelta, timezone
    _KST = timezone(timedelta(hours=9))
    opening = datetime(2026, 3, 24, 9, 15, 0, tzinfo=_KST)
    cfg = _cfg(no_buy_after_kst_hour=15, opening_min_confidence=80)
    with patch("kindshot.guardrails.datetime") as mock_dt:
        mock_dt.now.return_value = opening
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        r = check_guardrails(
            "005930", cfg, spread_bps=10.0, adv_value_20d=10e9, ret_today=1.0,
            decision_action=Action.BUY, decision_confidence=82,
        )
    assert r.passed is True


def test_closing_low_confidence_blocked():
    """14:30-15:00: conf<85 → 차단."""
    from unittest.mock import patch
    from datetime import datetime, timedelta, timezone
    _KST = timezone(timedelta(hours=9))
    closing = datetime(2026, 3, 24, 14, 40, 0, tzinfo=_KST)
    cfg = _cfg(no_buy_after_kst_hour=15, closing_min_confidence=85)
    with patch("kindshot.guardrails.datetime") as mock_dt:
        mock_dt.now.return_value = closing
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        r = check_guardrails(
            "005930", cfg, spread_bps=10.0, adv_value_20d=10e9, ret_today=1.0,
            decision_action=Action.BUY, decision_confidence=80,
        )
    assert r.passed is False
    assert r.reason == "CLOSING_LOW_CONFIDENCE"


def test_fast_profile_late_entry_blocked_with_injected_time():
    cfg = _cfg(
        no_buy_after_kst_hour=15,
        fast_profile_hold_minutes=15,
        fast_profile_no_buy_after_kst_hour=14,
        fast_profile_no_buy_after_kst_minute=0,
    )
    r = check_guardrails(
        "005930",
        cfg,
        decision_action=Action.BUY,
        decision_confidence=82,
        decision_hold_minutes=15,
        decision_time_kst=_kst_dt(14, 5),
        **_base_args(),
    )
    assert r.passed is False
    assert r.reason == "FAST_PROFILE_LATE_ENTRY"


def test_fast_profile_before_cutoff_passes():
    cfg = _cfg(
        no_buy_after_kst_hour=15,
        fast_profile_hold_minutes=15,
        fast_profile_no_buy_after_kst_hour=14,
        fast_profile_no_buy_after_kst_minute=0,
    )
    r = check_guardrails(
        "005930",
        cfg,
        decision_action=Action.BUY,
        decision_confidence=82,
        decision_hold_minutes=15,
        decision_time_kst=_kst_dt(13, 59),
        **_base_args(),
    )
    assert r.passed is True


def test_fast_profile_exact_cutoff_blocked():
    cfg = _cfg(
        no_buy_after_kst_hour=15,
        fast_profile_hold_minutes=15,
        fast_profile_no_buy_after_kst_hour=14,
        fast_profile_no_buy_after_kst_minute=0,
    )
    r = check_guardrails(
        "005930",
        cfg,
        decision_action=Action.BUY,
        decision_confidence=82,
        decision_hold_minutes=15,
        decision_time_kst=_kst_dt(14, 0),
        **_base_args(),
    )
    assert r.passed is False
    assert r.reason == "FAST_PROFILE_LATE_ENTRY"


def test_fast_profile_reason_wins_over_low_confidence():
    cfg = _cfg(
        no_buy_after_kst_hour=15,
        min_buy_confidence=75,
        fast_profile_hold_minutes=15,
        fast_profile_no_buy_after_kst_hour=14,
        fast_profile_no_buy_after_kst_minute=0,
    )
    r = check_guardrails(
        "005930",
        cfg,
        decision_action=Action.BUY,
        decision_confidence=72,
        decision_hold_minutes=15,
        decision_time_kst=_kst_dt(14, 5),
        **_base_args(),
    )
    assert r.passed is False
    assert r.reason == "FAST_PROFILE_LATE_ENTRY"


def test_non_fast_profile_not_blocked_by_fast_profile_cutoff():
    cfg = _cfg(
        no_buy_after_kst_hour=15,
        fast_profile_hold_minutes=15,
        fast_profile_no_buy_after_kst_hour=14,
        fast_profile_no_buy_after_kst_minute=0,
    )
    r = check_guardrails(
        "005930",
        cfg,
        decision_action=Action.BUY,
        decision_confidence=82,
        decision_hold_minutes=30,
        decision_time_kst=_kst_dt(14, 5),
        **_base_args(),
    )
    assert r.passed is True


def test_fast_profile_uses_decision_time_even_if_event_arrived_earlier():
    cfg = _cfg(
        no_buy_after_kst_hour=15,
        fast_profile_hold_minutes=15,
        fast_profile_no_buy_after_kst_hour=14,
        fast_profile_no_buy_after_kst_minute=0,
    )
    r = check_guardrails(
        "005930",
        cfg,
        decision_action=Action.BUY,
        decision_confidence=90,
        decision_hold_minutes=15,
        decision_time_kst=_kst_dt(14, 5),
        **_base_args(),
    )
    assert r.passed is False
    assert r.reason == "FAST_PROFILE_LATE_ENTRY"


def test_midday_spread_uses_injected_time_without_datetime_patch():
    cfg = _cfg(spread_check_enabled=True, spread_bps_limit=50.0, no_buy_after_kst_hour=15)
    r = check_guardrails(
        "005930",
        cfg,
        spread_bps=40.0,
        adv_value_20d=10e9,
        ret_today=2.0,
        decision_action=Action.BUY,
        decision_time_kst=_kst_dt(12, 0),
    )
    assert r.passed is False
    assert r.reason == "MIDDAY_SPREAD_TOO_WIDE"


def test_closing_confidence_uses_decision_time_even_if_event_arrived_earlier():
    cfg = _cfg(no_buy_after_kst_hour=15, closing_min_confidence=85)
    r = check_guardrails(
        "005930",
        cfg,
        spread_bps=10.0,
        adv_value_20d=10e9,
        ret_today=1.0,
        decision_action=Action.BUY,
        decision_confidence=80,
        decision_time_kst=_kst_dt(14, 40),
    )
    assert r.passed is False
    assert r.reason == "CLOSING_LOW_CONFIDENCE"


# ── 포지션 사이징: account_balance 기반 산출 검증 ──────────────────

def test_position_size_account_risk():
    """계좌 리스크 기반 포지션 = (잔고 * risk%) / (SL%)."""
    cfg = _cfg(
        order_size=5_000_000,
        account_risk_pct=2.0,
        paper_stop_loss_pct=-0.7,
    )
    # risk_amount = 10M * 2% = 200_000
    # sl_pct = 0.7 / 100 = 0.007
    # account_based = 200_000 / 0.007 = 28_571_428
    # min(5M, 28.5M) = 5M (hint가 더 작음)
    size = calculate_position_size(cfg, "M", account_balance=10_000_000)
    assert size == 5_000_000


def test_position_size_account_risk_constrains():
    """소액 계좌에서 account_based가 hint보다 작을 때 제약."""
    cfg = _cfg(
        order_size=5_000_000,
        account_risk_pct=2.0,
        paper_stop_loss_pct=-0.7,
    )
    # risk_amount = 1M * 2% = 20_000
    # sl_pct = 0.007
    # account_based = 20_000 / 0.007 ≈ 2_857_142
    # min(5M, 2.857M) = 2.857M
    size = calculate_position_size(cfg, "M", account_balance=1_000_000)
    expected = 1_000_000 * 0.02 / 0.007
    assert abs(size - expected) < 1  # 부동소수점 허용


# ── Detection Delay confidence 조정 테스트 ──────────────────

def test_delay_confidence_fast_detection():
    """<30초: 조정 없음."""
    assert apply_delay_confidence_adjustment(80, 15_000) == 80
    assert apply_delay_confidence_adjustment(80, None) == 80


def test_delay_confidence_30s():
    """30~60초: -1."""
    assert apply_delay_confidence_adjustment(80, 45_000) == 79


def test_delay_confidence_60s():
    """60~120초: -2."""
    assert apply_delay_confidence_adjustment(80, 90_000) == 78


def test_delay_confidence_120s_plus():
    """120초+: -3."""
    assert apply_delay_confidence_adjustment(80, 180_000) == 77


# ── Price Reaction confidence 조정 테스트 ──────────────────

def test_price_reaction_confirmed():
    """ret_today 0.3~1.5%: 시장 반응 확인 → +2."""
    assert apply_price_reaction_adjustment(78, 0.5) == 80
    assert apply_price_reaction_adjustment(78, 1.0) == 80
    assert apply_price_reaction_adjustment(78, 1.5) == 80


def test_price_reaction_no_response():
    """ret_today < -0.5%: 시장 불신 → -2."""
    assert apply_price_reaction_adjustment(80, -1.0) == 78
    assert apply_price_reaction_adjustment(80, -0.6) == 78


def test_price_reaction_neutral():
    """ret_today -0.5~0.3%: 조정 없음."""
    assert apply_price_reaction_adjustment(80, 0.0) == 80
    assert apply_price_reaction_adjustment(80, 0.2) == 80
    assert apply_price_reaction_adjustment(80, -0.4) == 80


def test_price_reaction_none():
    """ret_today=None: 조정 없음."""
    assert apply_price_reaction_adjustment(80, None) == 80


# ── Volume confidence adjustment ──


def test_volume_spike_strong():
    """전일대비 300%+: +3 부스트."""
    assert apply_volume_confidence_adjustment(80, 350.0) == 83


def test_volume_spike_moderate():
    """전일대비 150~300%: +1 부스트."""
    assert apply_volume_confidence_adjustment(80, 200.0) == 81


def test_volume_normal():
    """전일대비 80~150%: 조정 없음."""
    assert apply_volume_confidence_adjustment(80, 100.0) == 80
    assert apply_volume_confidence_adjustment(80, 140.0) == 80


def test_volume_low():
    """전일대비 50~80%: -1."""
    assert apply_volume_confidence_adjustment(80, 60.0) == 79


def test_volume_very_low():
    """전일대비 50% 미만: -3."""
    assert apply_volume_confidence_adjustment(80, 30.0) == 77


def test_volume_none():
    """None: 조정 없음."""
    assert apply_volume_confidence_adjustment(80, None) == 80


def test_volume_cap_at_100():
    """상한 100 초과 방지."""
    assert apply_volume_confidence_adjustment(99, 400.0) == 100


# ── dorg confidence adjustment ──


def test_dorg_disclosure_source_no_penalty():
    """거래소/금감원 공시 출처 → 감점 없음."""
    assert apply_dorg_confidence_adjustment(80, "거래소공시") == 80
    assert apply_dorg_confidence_adjustment(80, "금감원전자공시") == 80
    assert apply_dorg_confidence_adjustment(80, "한국거래소") == 80
    assert apply_dorg_confidence_adjustment(80, "코스닥시장본부") == 80
    assert apply_dorg_confidence_adjustment(80, "KIND") == 80


def test_dorg_news_source_penalty():
    """뉴스 출처 → -5 감점."""
    assert apply_dorg_confidence_adjustment(80, "한국경제") == 75
    assert apply_dorg_confidence_adjustment(80, "매일경제") == 75
    assert apply_dorg_confidence_adjustment(80, "연합뉴스") == 75
    assert apply_dorg_confidence_adjustment(80, "이데일리") == 75


def test_dorg_empty_no_penalty():
    """dorg 비어있으면 조정 없음."""
    assert apply_dorg_confidence_adjustment(80, "") == 80


def test_dorg_floor_at_zero():
    """감점 후 0 미만 방지."""
    assert apply_dorg_confidence_adjustment(3, "매일경제") == 0


# ── time session confidence adjustment ──


def test_time_premarket_boost():
    """v65: 장전 공시 06:00~08:00 → +5 부스트, 08:00~08:30 → +2 부스트."""
    from kindshot.tz import KST
    t0700 = datetime(2026, 3, 27, 7, 0, tzinfo=KST)
    t0815 = datetime(2026, 3, 27, 8, 15, tzinfo=KST)
    assert apply_time_session_confidence_adjustment(80, t0700) == 85  # +5
    assert apply_time_session_confidence_adjustment(80, t0815) == 82  # +2 (v65: 08시대 축소)


def test_time_premarket_after_0830_no_boost():
    """v65: 08:30 이후 → 부스트 없음 (08시대 worst -0.49% 데이터)."""
    from kindshot.tz import KST
    t0830 = datetime(2026, 3, 27, 8, 30, tzinfo=KST)
    t0831 = datetime(2026, 3, 27, 8, 31, tzinfo=KST)
    assert apply_time_session_confidence_adjustment(80, t0830) == 80
    assert apply_time_session_confidence_adjustment(80, t0831) == 80


def test_time_midday_penalty():
    """v71: midday PF=2.25 부스트. 11시 +3, 12시 +2."""
    from kindshot.tz import KST
    t1130 = datetime(2026, 3, 27, 11, 30, tzinfo=KST)
    t1200 = datetime(2026, 3, 27, 12, 0, tzinfo=KST)
    assert apply_time_session_confidence_adjustment(80, t1130) == 83  # v71: 11시대 +3
    assert apply_time_session_confidence_adjustment(80, t1200) == 82  # v71: 12시대 +2


def test_time_normal_no_adjustment():
    """v72: 10시대 -2 페널티, 14시 조정 없음."""
    from kindshot.tz import KST
    t1000 = datetime(2026, 3, 27, 10, 0, tzinfo=KST)
    t1400 = datetime(2026, 3, 27, 14, 0, tzinfo=KST)
    assert apply_time_session_confidence_adjustment(80, t1000) == 78  # v72: 10시대 -2
    assert apply_time_session_confidence_adjustment(80, t1400) == 80


def test_time_none_no_adjustment():
    """시간 정보 없으면 조정 없음."""
    assert apply_time_session_confidence_adjustment(80, None) == 80


def test_time_premarket_cap_at_100():
    """부스트 후 100 초과 방지."""
    from kindshot.tz import KST
    t0700 = datetime(2026, 3, 27, 7, 0, tzinfo=KST)
    assert apply_time_session_confidence_adjustment(98, t0700) == 100


# ── market bullish boost ──


def test_market_bullish_boost():
    """지수 +1%+ and breadth_ratio>0.6 → +3."""
    assert apply_market_confidence_adjustment(80, 1.2, 0.8, breadth_ratio=0.65) == 83


def test_market_bullish_no_boost_low_breadth():
    """지수 상승이지만 breadth 낮으면 부스트 없음."""
    assert apply_market_confidence_adjustment(80, 1.2, 0.8, breadth_ratio=0.5) == 80


def test_market_bullish_no_boost_low_index():
    """breadth 높지만 지수 상승 미달이면 부스트 없음."""
    assert apply_market_confidence_adjustment(80, 0.5, 0.3, breadth_ratio=0.7) == 80


def test_market_bullish_cap_at_100():
    """상승장 부스트 100 캡."""
    assert apply_market_confidence_adjustment(99, 1.5, 1.2, breadth_ratio=0.7) == 100


def test_market_bearish_unchanged_with_breadth():
    """하락장은 breadth 무관하게 기존 로직 유지."""
    assert apply_market_confidence_adjustment(80, -1.5, -0.3, breadth_ratio=0.7) == 77


# ── trend confidence adjustment ──


def test_trend_overheated_penalty():
    """ret_3d > 10% → -10 감점."""
    assert apply_trend_confidence_adjustment(85, 15.0, 50.0) == 75


def test_trend_low_pos_penalty():
    """pos_20d < 20 → -5 감점."""
    assert apply_trend_confidence_adjustment(80, 2.0, 10.0) == 75


def test_trend_both_penalties():
    """과열 + 극저점 동시 → -15."""
    assert apply_trend_confidence_adjustment(85, 12.0, 15.0) == 70


def test_trend_normal_no_change():
    """정상 범위 → 조정 없음."""
    assert apply_trend_confidence_adjustment(80, 5.0, 50.0) == 80


def test_trend_none_no_change():
    """None → 조정 없음."""
    assert apply_trend_confidence_adjustment(80, None, None) == 80


def test_volume_zero_no_change():
    """volume_rate 0.0 → 조정 없음 (None과 동일 처리)."""
    from kindshot.guardrails import apply_volume_confidence_adjustment
    assert apply_volume_confidence_adjustment(80, 0.0) == 80


# ── Volume ratio vs avg20d confidence adjustment ──


def test_volume_ratio_surge_extreme():
    """avg20d 대비 300%+: +5 부스트."""
    assert apply_volume_ratio_confidence_adjustment(80, 3.5) == 85


def test_volume_ratio_surge_strong():
    """avg20d 대비 200~300%: +3 부스트."""
    assert apply_volume_ratio_confidence_adjustment(80, 2.5) == 83


def test_volume_ratio_above_avg():
    """avg20d 대비 100~200%: +1 부스트."""
    assert apply_volume_ratio_confidence_adjustment(80, 1.2) == 81


def test_volume_ratio_normal():
    """avg20d 대비 50~100%: 조정 없음."""
    assert apply_volume_ratio_confidence_adjustment(80, 0.7) == 80


def test_volume_ratio_low():
    """avg20d 대비 30~50%: -2."""
    assert apply_volume_ratio_confidence_adjustment(80, 0.4) == 78


def test_volume_ratio_very_low():
    """avg20d 대비 30% 미만: -3."""
    assert apply_volume_ratio_confidence_adjustment(80, 0.2) == 77


def test_volume_ratio_none():
    """None: 조정 없음."""
    assert apply_volume_ratio_confidence_adjustment(80, None) == 80


def test_volume_ratio_zero():
    """0.0: 조정 없음."""
    assert apply_volume_ratio_confidence_adjustment(80, 0.0) == 80


def test_volume_ratio_cap_at_100():
    """상한 100 초과 방지."""
    assert apply_volume_ratio_confidence_adjustment(98, 4.0) == 100


def test_volume_ratio_guardrail_skip():
    """10시 이후 volume_ratio < min_volume_ratio_vs_avg20d → VOLUME_RATIO_TOO_THIN."""
    cfg = _cfg(min_volume_ratio_vs_avg20d=0.05, no_buy_after_kst_hour=24)
    r = check_guardrails(
        "005930", cfg,
        volume_ratio_vs_avg20d=0.02,
        decision_action=Action.BUY,
        decision_time_kst=_kst_dt(10, 30),
        **_base_args(),
    )
    assert not r.passed
    assert r.reason == "VOLUME_RATIO_TOO_THIN"


def test_volume_ratio_guardrail_pass_before_10am():
    """10시 이전엔 volume ratio 체크 비활성."""
    cfg = _cfg(min_volume_ratio_vs_avg20d=0.05, no_buy_after_kst_hour=24)
    r = check_guardrails(
        "005930", cfg,
        volume_ratio_vs_avg20d=0.02,
        decision_action=Action.BUY,
        decision_time_kst=_kst_dt(9, 15),
        **_base_args(),
    )
    assert r.passed


def test_volume_ratio_guardrail_pass_above_threshold():
    """volume_ratio >= threshold → 통과."""
    cfg = _cfg(min_volume_ratio_vs_avg20d=0.05, no_buy_after_kst_hour=24)
    r = check_guardrails(
        "005930", cfg,
        volume_ratio_vs_avg20d=0.15,
        decision_action=Action.BUY,
        decision_time_kst=_kst_dt(11, 0),
        **_base_args(),
    )
    assert r.passed


# ── Sector momentum confidence adjustment ──


def test_sector_momentum_leading_boost():
    assert apply_sector_momentum_confidence_adjustment(80, "LEADING", 82.0) == 83


def test_sector_momentum_improving_small_boost():
    assert apply_sector_momentum_confidence_adjustment(80, "IMPROVING", 58.0) == 81


def test_sector_momentum_lagging_penalty():
    assert apply_sector_momentum_confidence_adjustment(80, "LAGGING", 22.0) == 77


def test_sector_momentum_neutral_extreme_scores():
    assert apply_sector_momentum_confidence_adjustment(80, "NEUTRAL", 72.0) == 81
    assert apply_sector_momentum_confidence_adjustment(80, "NEUTRAL", 25.0) == 79


# ── Technical confidence adjustment (RSI/MACD/BB/ATR) ──


def test_technical_rsi_overbought():
    """RSI > 75 → -5."""
    assert apply_technical_confidence_adjustment(85, rsi_14=80.0, macd_hist=None) == 80


def test_technical_rsi_oversold_with_catalyst():
    """RSI < 30 + catalyst → +3."""
    assert apply_technical_confidence_adjustment(80, rsi_14=25.0, macd_hist=None, has_catalyst=True) == 83


def test_technical_rsi_oversold_no_catalyst():
    """RSI < 30 without catalyst → no change."""
    assert apply_technical_confidence_adjustment(80, rsi_14=25.0, macd_hist=None, has_catalyst=False) == 80


def test_technical_macd_negative():
    """v66: MACD 세분화 — 약한 음수(-5) → -1, 강한 음수(-200 이하) → -3."""
    assert apply_technical_confidence_adjustment(85, rsi_14=None, macd_hist=-5.0) == 84   # 약한: -1
    assert apply_technical_confidence_adjustment(85, rsi_14=None, macd_hist=-100.0) == 83  # 중간: -2
    assert apply_technical_confidence_adjustment(85, rsi_14=None, macd_hist=-300.0) == 82  # 강한: -3


def test_technical_bb_overbought():
    """BB position > 95 → -3."""
    assert apply_technical_confidence_adjustment(85, rsi_14=None, macd_hist=None, bb_position=98.0) == 82


def test_technical_bb_oversold_with_catalyst():
    """BB position < 5 + catalyst → +2."""
    assert apply_technical_confidence_adjustment(80, rsi_14=None, macd_hist=None, bb_position=3.0, has_catalyst=True) == 82


def test_technical_bb_oversold_no_catalyst():
    """BB position < 5 without catalyst → no change."""
    assert apply_technical_confidence_adjustment(80, rsi_14=None, macd_hist=None, bb_position=3.0, has_catalyst=False) == 80


def test_technical_bb_normal_no_change():
    """BB position in normal range → no change."""
    assert apply_technical_confidence_adjustment(80, rsi_14=None, macd_hist=None, bb_position=50.0) == 80


def test_technical_atr_high_volatility():
    """ATR > 5% → -2."""
    assert apply_technical_confidence_adjustment(85, rsi_14=None, macd_hist=None, atr_14=6.5) == 83


def test_technical_atr_normal_no_change():
    """ATR <= 5% → no change."""
    assert apply_technical_confidence_adjustment(85, rsi_14=None, macd_hist=None, atr_14=3.0) == 85


def test_technical_combined_rsi_macd_bb_atr():
    """RSI overbought + MACD negative + BB overbought + high ATR = cumulative penalty."""
    # v66: 85 - 5(rsi) - 1(macd, 약한 음수) - 3(bb) - 2(atr) = 74
    result = apply_technical_confidence_adjustment(
        85, rsi_14=80.0, macd_hist=-5.0, bb_position=98.0, atr_14=7.0,
    )
    assert result == 74


def test_technical_all_none_no_change():
    """All indicators None → no change."""
    assert apply_technical_confidence_adjustment(85, rsi_14=None, macd_hist=None, bb_position=None, atr_14=None) == 85


# ── Headline quality adjustment 테스트 ──


def test_headline_quality_short_title_penalty():
    """15자 미만 짧은 제목 → -5 감점."""
    assert apply_headline_quality_adjustment(85, "A사 수주") == 77  # -5(short) -3(수주 without number)


def test_headline_quality_question_mark_penalty():
    """물음표 포함 추측성 기사 → -5, 수주+숫자없음 → -3, 합계 -8."""
    assert apply_headline_quality_adjustment(85, "삼성전자, 반도체 수주 대박 가능성 있을까?") == 77


def test_headline_quality_contract_without_amount():
    """수주/계약에 금액 미기재 → -3 감점."""
    assert apply_headline_quality_adjustment(85, "삼성전자, 대규모 공급계약 체결 발표") == 82


def test_headline_quality_contract_with_amount_no_penalty():
    """수주+금액 포함 → 감점 없음."""
    assert apply_headline_quality_adjustment(85, "삼성전자, 500억원 규모 수주 체결") == 85


def test_headline_quality_normal_headline_no_penalty():
    """일반적인 양호한 제목 → 감점 없음."""
    assert apply_headline_quality_adjustment(85, "삼성전자(005930) - 합병 결정 공시") == 85


def test_headline_quality_contract_commentary_gets_extra_penalty():
    assert apply_headline_quality_adjustment(85, "삼성전자 추가 상승 여력 충분 장기공급계약 요구 큰 폭 증가") == 78


def test_resolve_dynamic_guardrail_profile_relaxes_supportive_market():
    cfg = _cfg(
        min_buy_confidence=78,
        opening_min_confidence=82,
        afternoon_min_confidence=80,
        fast_profile_no_buy_after_kst_hour=14,
        fast_profile_no_buy_after_kst_minute=0,
        no_buy_after_kst_hour=15,
        no_buy_after_kst_minute=0,
        dynamic_guardrail_confidence_relaxation=2,
        dynamic_fast_profile_extension_minutes=60,
    )
    profile = resolve_dynamic_guardrail_profile(
        cfg,
        kospi_change_pct=0.4,
        kosdaq_change_pct=0.8,
        kospi_breadth_ratio=0.58,
        kosdaq_breadth_ratio=0.61,
    )
    assert profile.supportive_market is True
    assert profile.min_buy_confidence == 76
    assert profile.opening_min_confidence == 81
    assert profile.afternoon_min_confidence == 78
    assert (profile.fast_profile_no_buy_after_kst_hour, profile.fast_profile_no_buy_after_kst_minute) == (15, 0)


def test_dynamic_fast_profile_cutoff_never_exceeds_market_close():
    cfg = _cfg(
        fast_profile_no_buy_after_kst_hour=14,
        fast_profile_no_buy_after_kst_minute=0,
        no_buy_after_kst_hour=14,
        no_buy_after_kst_minute=45,
        dynamic_fast_profile_extension_minutes=60,
    )
    profile = resolve_dynamic_guardrail_profile(
        cfg,
        kospi_change_pct=0.5,
        kosdaq_change_pct=0.7,
        kospi_breadth_ratio=0.6,
        kosdaq_breadth_ratio=0.62,
    )
    assert (profile.fast_profile_no_buy_after_kst_hour, profile.fast_profile_no_buy_after_kst_minute) == (14, 45)


def test_dynamic_profile_relaxes_borderline_low_confidence_in_supportive_market():
    cfg = _cfg(min_buy_confidence=78)
    profile = resolve_dynamic_guardrail_profile(
        cfg,
        kospi_change_pct=0.5,
        kosdaq_change_pct=0.7,
        kospi_breadth_ratio=0.57,
        kosdaq_breadth_ratio=0.63,
    )
    decision_time = _kst_dt(12, 0)
    base = check_guardrails(
        "005930",
        cfg,
        decision_action=Action.BUY,
        decision_confidence=76,
        decision_time_kst=decision_time,
        decision_hold_minutes=15,
        **_base_args(),
    )
    relaxed = check_guardrails(
        "005930",
        cfg,
        decision_action=Action.BUY,
        decision_confidence=76,
        decision_time_kst=decision_time,
        decision_hold_minutes=15,
        dynamic_profile=profile,
        **_base_args(),
    )
    assert base.passed is False
    assert base.reason == "LOW_CONFIDENCE"
    assert relaxed.passed is True


def test_dynamic_profile_extends_fast_profile_window_in_supportive_market():
    cfg = _cfg(
        min_buy_confidence=78,
        afternoon_min_confidence=80,
        fast_profile_hold_minutes=20,
        fast_profile_no_buy_after_kst_hour=14,
        fast_profile_no_buy_after_kst_minute=0,
        no_buy_after_kst_hour=15,
        no_buy_after_kst_minute=0,
        dynamic_guardrail_confidence_relaxation=2,
        dynamic_fast_profile_extension_minutes=60,
    )
    profile = resolve_dynamic_guardrail_profile(
        cfg,
        kospi_change_pct=0.5,
        kosdaq_change_pct=0.7,
        kospi_breadth_ratio=0.57,
        kosdaq_breadth_ratio=0.63,
    )
    decision_time = _kst_dt(14, 20)
    base = check_guardrails(
        "005930",
        cfg,
        decision_action=Action.BUY,
        decision_confidence=78,
        decision_time_kst=decision_time,
        decision_hold_minutes=20,
        **_base_args(),
    )
    relaxed = check_guardrails(
        "005930",
        cfg,
        decision_action=Action.BUY,
        decision_confidence=78,
        decision_time_kst=decision_time,
        decision_hold_minutes=20,
        dynamic_profile=profile,
        **_base_args(),
    )
    assert base.passed is False
    assert base.reason == "FAST_PROFILE_LATE_ENTRY"
    assert relaxed.passed is True
