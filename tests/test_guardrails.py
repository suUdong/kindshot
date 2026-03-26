"""Tests for guardrails including portfolio-level controls."""

from datetime import datetime, timedelta, timezone

from kindshot.config import Config
from kindshot.guardrails import (
    check_guardrails, GuardrailResult, GuardrailState,
    get_dynamic_stop_loss_pct, get_dynamic_tp_pct,
    apply_adv_confidence_adjustment, apply_market_confidence_adjustment,
    calculate_position_size,
    downgrade_size_hint, get_kill_switch_size_hint,
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
    """confidence>=85 시 기본 SL의 1.5배로 완화."""
    cfg = _cfg(paper_stop_loss_pct=-1.5)
    sl = get_dynamic_stop_loss_pct(cfg, confidence=90)
    assert sl == -2.25


def test_dynamic_stop_loss_normal_confidence():
    """conf 75-79 → 타이트한 SL floor -0.5%."""
    cfg = _cfg(paper_stop_loss_pct=-1.5)
    sl = get_dynamic_stop_loss_pct(cfg, confidence=75)
    assert sl == -0.5


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
        "consecutive_stop_losses": 1,
    }
    state_file.write_text(json.dumps(data))

    # New instance should reload from disk
    state2 = GuardrailState(cfg, state_dir=tmp_path)
    assert state2.position_count == 2
    assert "005930" in state2.bought_tickers
    assert state2.daily_pnl == -100000
    assert state2.consecutive_stop_losses == 1


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
    """conf>=85 → TP 1.5%."""
    cfg = _cfg()
    assert get_dynamic_tp_pct(cfg, 90) == 1.5
    assert get_dynamic_tp_pct(cfg, 85) == 1.5


def test_dynamic_tp_mid_confidence():
    """conf 80-84 → TP 1.0%, conf 75-79 → TP 0.5%."""
    cfg = _cfg()
    assert get_dynamic_tp_pct(cfg, 80) == 1.0
    assert get_dynamic_tp_pct(cfg, 75) == 0.5


def test_dynamic_tp_low_confidence():
    """conf<75 → config 기본값."""
    cfg = _cfg(paper_take_profit_pct=1.0)
    assert get_dynamic_tp_pct(cfg, 70) == 1.0


# ── US-003: ADV confidence 조정 테스트 ──────────────────

def test_adv_confidence_mega_cap():
    """ADV 5000억+ → cap 65 (sell the news)."""
    assert apply_adv_confidence_adjustment(85, 600_000_000_000) == 65
    assert apply_adv_confidence_adjustment(60, 600_000_000_000) == 60  # 이미 65 미만


def test_adv_confidence_large_cap():
    """ADV 2000~5000억 → -5, cap 72."""
    assert apply_adv_confidence_adjustment(80, 300_000_000_000) == 72


def test_adv_confidence_mid_cap():
    """ADV 500~2000억 → 조정 없음."""
    assert apply_adv_confidence_adjustment(80, 100_000_000_000) == 80


# ── Market confidence adjustment 테스트 ──────────────────

def test_market_confidence_flat():
    """지수 보합 → 조정 없음."""
    assert apply_market_confidence_adjustment(80, 0.1, 0.2) == 80

def test_market_confidence_mild_down():
    """지수 -0.5~-1% → -2."""
    assert apply_market_confidence_adjustment(80, -0.7, 0.1) == 78

def test_market_confidence_moderate_down():
    """지수 -1~-2% → -5."""
    assert apply_market_confidence_adjustment(80, -1.5, -0.3) == 75

def test_market_confidence_severe_down():
    """지수 -2% 이하 → -8."""
    assert apply_market_confidence_adjustment(80, -2.5, -1.8) == 72

def test_market_confidence_worst_of_both():
    """두 지수 중 더 나쁜 쪽 기준."""
    assert apply_market_confidence_adjustment(80, -0.3, -1.5) == 75

def test_market_confidence_none_values():
    """지수 데이터 없으면 조정 없음."""
    assert apply_market_confidence_adjustment(80, None, None) == 80

def test_market_confidence_one_none():
    """한쪽만 있으면 해당 값 기준."""
    assert apply_market_confidence_adjustment(80, -1.5, None) == 75


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
            decision_action=Action.BUY, decision_confidence=75,
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
