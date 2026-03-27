from pathlib import Path

from kindshot.config import Config
from kindshot.pattern_profile import (
    _load_backtest_analysis_module,
    build_recent_pattern_profile_from_rows,
    match_loss_guardrail,
    match_profit_boost,
)


def test_build_recent_pattern_profile_prefers_stable_fallback_cohorts():
    cfg = Config(
        recent_pattern_min_trades=2,
        recent_pattern_profit_min_win_rate=0.5,
        recent_pattern_profit_min_total_pnl_pct=0.15,
        recent_pattern_loss_max_win_rate=0.25,
        recent_pattern_loss_max_total_pnl_pct=-0.5,
    )
    rows = [
        {"date": "20260320", "ticker": "111111", "headline": "A사 공급계약 체결", "keyword_hits": '["공급계약"]', "news_category": "", "hour_slot": 9, "exit_ret_pct": -0.7},
        {"date": "20260321", "ticker": "222222", "headline": "B사 수주 공시", "keyword_hits": '["수주"]', "news_category": "", "hour_slot": 9, "exit_ret_pct": -0.8},
        {"date": "20260322", "ticker": "333333", "headline": "C사 FDA 승인", "keyword_hits": '["FDA"]', "news_category": "", "hour_slot": 11, "exit_ret_pct": 0.2},
        {"date": "20260323", "ticker": "444444", "headline": "D사 임상 허가", "keyword_hits": '["허가"]', "news_category": "", "hour_slot": 11, "exit_ret_pct": 0.15},
        {"date": "20260324", "ticker": "555555", "headline": "E사 얀센 협업", "keyword_hits": "[]", "news_category": "", "hour_slot": 11, "exit_ret_pct": 0.1},
    ]

    profile = build_recent_pattern_profile_from_rows(rows, cfg)

    assert profile.enabled is True
    assert profile.total_trades == 5
    assert profile.top_profit_exact is not None
    assert profile.top_loss_exact is not None
    assert profile.top_profit_exact.count == 1
    assert profile.boost_patterns[0].pattern_type == "news_type_hour_bucket"
    assert profile.boost_patterns[0].news_type == "clinical_regulatory"
    assert profile.boost_patterns[0].hour_bucket == "midday"
    assert profile.boost_patterns[0].ticker is None
    assert profile.boost_patterns[0].confidence_delta == 3
    assert profile.loss_guardrail_patterns[0].pattern_type == "news_type_hour_bucket"
    assert profile.loss_guardrail_patterns[0].news_type == "contract"
    assert profile.loss_guardrail_patterns[0].hour_bucket == "open"


def test_recent_pattern_profile_matchers():
    cfg = Config(recent_pattern_min_trades=2)
    rows = [
        {"date": "20260320", "ticker": "111111", "headline": "A사 공급계약 체결", "keyword_hits": '["공급계약"]', "news_category": "", "hour_slot": 9, "exit_ret_pct": -0.7},
        {"date": "20260321", "ticker": "222222", "headline": "B사 수주 공시", "keyword_hits": '["수주"]', "news_category": "", "hour_slot": 9, "exit_ret_pct": -0.8},
        {"date": "20260322", "ticker": "333333", "headline": "C사 FDA 승인", "keyword_hits": '["FDA"]', "news_category": "", "hour_slot": 11, "exit_ret_pct": 0.2},
        {"date": "20260323", "ticker": "444444", "headline": "D사 임상 허가", "keyword_hits": '["허가"]', "news_category": "", "hour_slot": 11, "exit_ret_pct": 0.2},
    ]
    profile = build_recent_pattern_profile_from_rows(rows, cfg)

    assert match_profit_boost(profile, news_type="clinical_regulatory", ticker="333333", hour_bucket="midday") is not None
    fallback = match_profit_boost(profile, news_type="clinical_regulatory", ticker="999999", hour_bucket="midday")
    assert fallback is not None
    assert fallback.pattern_type == "news_type_hour_bucket"
    assert match_profit_boost(profile, news_type="clinical_regulatory", ticker="999999", hour_bucket="open") is None
    assert match_loss_guardrail(profile, news_type="contract", ticker="999999", hour_bucket="open") is not None
    assert match_loss_guardrail(profile, news_type="contract", ticker="999999", hour_bucket="midday") is None


def test_load_backtest_analysis_module_prefers_cwd(monkeypatch, tmp_path: Path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "backtest_analysis.py").write_text("SENTINEL = 123\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    module = _load_backtest_analysis_module()

    assert module is not None
    assert module.SENTINEL == 123
