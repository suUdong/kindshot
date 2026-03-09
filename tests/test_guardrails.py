"""Tests for guardrails including portfolio-level controls."""

from kindshot.config import Config
from kindshot.guardrails import check_guardrails, GuardrailResult, GuardrailState


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
        spread_bps=30.0,
        adv_value_20d=10e9,
        ret_today=5.0,
    )
    assert r.passed is False
    assert r.reason == "SPREAD_TOO_WIDE"


def test_spread_missing_fails_close():
    r = check_guardrails(
        ticker="005930",
        config=_cfg(spread_check_enabled=True),
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
        adv_value_20d=1e9,
        ret_today=5.0,
    )
    assert r.passed is False
    assert r.reason == "ADV_TOO_LOW"


def test_adv_missing():
    r = check_guardrails(
        ticker="005930",
        config=_cfg(),
        adv_value_20d=None,
        ret_today=5.0,
    )
    assert r.passed is False
    assert r.reason == "ADV_DATA_MISSING"


def test_extreme_move():
    r = check_guardrails(
        ticker="005930",
        config=_cfg(),
        adv_value_20d=10e9,
        ret_today=25.0,
    )
    assert r.passed is False
    assert r.reason == "EXTREME_MOVE"


def test_ret_today_missing():
    r = check_guardrails(
        ticker="005930",
        config=_cfg(),
        adv_value_20d=10e9,
        ret_today=None,
    )
    assert r.passed is False
    assert r.reason == "RET_TODAY_DATA_MISSING"


def _base_args():
    return dict(adv_value_20d=10e9, ret_today=5.0)


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
