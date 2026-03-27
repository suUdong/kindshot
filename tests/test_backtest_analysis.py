from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "backtest_analysis.py"
    spec = spec_from_file_location("backtest_analysis", path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_classify_news_type_is_deterministic():
    mod = _load_module()
    assert mod.classify_news_type("A사 공급계약 체결", ["공급계약"]) == "contract"
    assert mod.classify_news_type("B사 자사주 소각 결정", ["자사주 소각"]) == "shareholder_return"
    assert mod.classify_news_type("C사 FDA 허가", ["FDA"]) == "clinical_regulatory"
    assert mod.classify_news_type("D사 경쟁사 인수", ["인수"]) == "mna"


def test_analyze_paths_builds_matrices_and_recommendations(tmp_path):
    mod = _load_module()
    log_path = tmp_path / "kindshot_20260327.jsonl"
    log_path.write_text(
        "\n".join(
            [
                '{"type":"event","event_id":"e1","ticker":"111111","headline":"A사 공급계약 체결","bucket":"POS_STRONG","keyword_hits":["공급계약"],"decision_action":"BUY","skip_stage":null,"detected_at":"2026-03-27T08:40:00+09:00","source":"KIS","dorg":"연합뉴스"}',
                '{"type":"decision","event_id":"e1","action":"BUY","confidence":84,"size_hint":"L","reason":"strong contract","decision_source":"LLM"}',
                '{"type":"price_snapshot","event_id":"e1","horizon":"t0","px":100.0,"ret_long_vs_t0":0.0}',
                '{"type":"price_snapshot","event_id":"e1","horizon":"t+30s","px":101.0,"ret_long_vs_t0":0.01}',
                '{"type":"price_snapshot","event_id":"e1","horizon":"t+5m","px":103.0,"ret_long_vs_t0":0.03}',
                '{"type":"event","event_id":"e2","ticker":"222222","headline":"B사 저스트플레이 인수","bucket":"POS_STRONG","keyword_hits":["인수"],"decision_action":"BUY","skip_stage":null,"detected_at":"2026-03-27T09:10:00+09:00","source":"KIS","dorg":"딜사이트"}',
                '{"type":"decision","event_id":"e2","action":"BUY","confidence":77,"size_hint":"M","reason":"mna bet","decision_source":"LLM"}',
                '{"type":"price_snapshot","event_id":"e2","horizon":"t0","px":100.0,"ret_long_vs_t0":0.0}',
                '{"type":"price_snapshot","event_id":"e2","horizon":"t+30s","px":99.0,"ret_long_vs_t0":-0.01}',
                '{"type":"price_snapshot","event_id":"e2","horizon":"t+5m","px":97.0,"ret_long_vs_t0":-0.03}',
                '{"type":"event","event_id":"e3","ticker":"333333","headline":"C사 FDA 품목허가 승인","bucket":"POS_STRONG","keyword_hits":["FDA"],"decision_action":"BUY","skip_stage":null,"detected_at":"2026-03-27T11:05:00+09:00","source":"KIS","dorg":"머니투데이"}',
                '{"type":"decision","event_id":"e3","action":"BUY","confidence":88,"size_hint":"L","reason":"approval","decision_source":"LLM"}',
                '{"type":"price_snapshot","event_id":"e3","horizon":"t0","px":100.0,"ret_long_vs_t0":0.0}',
                '{"type":"price_snapshot","event_id":"e3","horizon":"t+30s","px":100.5,"ret_long_vs_t0":0.005}',
                '{"type":"price_snapshot","event_id":"e3","horizon":"t+10m","px":102.5,"ret_long_vs_t0":0.025}',
            ]
        ),
        encoding="utf-8",
    )

    config = mod.ExitSimulationConfig()
    stats, trades, _shadow = mod.analyze_paths([log_path], snapshot_dir=None, runtime_defaults=config)

    assert len(trades) == 3
    assert stats["matrices"]["by_ticker"]["111111"]["count"] == 1
    assert stats["matrices"]["by_hour"]["08"]["count"] == 1
    assert stats["matrices"]["by_news_type"]["contract"]["count"] == 1
    assert stats["condition_scores"]["entry"]
    assert stats["condition_scores"]["exit"]["candidates"]
    assert stats["recommended_conditions"]["exit"]["params"]["paper_take_profit_pct"] >= 1.5


def test_render_report_includes_new_sections():
    mod = _load_module()
    trade = mod.Trade(
        event_id="e1",
        date="20260327",
        ticker="111111",
        headline="A사 공급계약 체결",
        bucket="POS_STRONG",
        confidence=84,
        size_hint="L",
        reason="strong contract",
        decision_source="LLM",
        detected_at="2026-03-27T08:40:00+09:00",
        source="KIS",
        dorg="연합뉴스",
        keyword_hits=["공급계약"],
        entry_price=100.0,
        snapshots={"t+30s": 1.0, "t+5m": 3.0},
        exit_type="TP",
        exit_horizon="t+5m",
        exit_pnl_pct=3.0,
        max_gain_pct=3.0,
        max_drawdown_pct=0.0,
        hold_minutes=5.0,
        hold_profile_minutes=20,
        hold_profile_keyword="공급계약",
        news_type="contract",
        hour=8,
        hour_bucket="pre_open",
    )
    stats = {
        "analysis_window": {"log_count": 1, "dates": ["20260327"]},
        "total_trades": 1,
        "wins": 1,
        "losses": 0,
        "win_rate": 100.0,
        "avg_pnl": 3.0,
        "total_pnl_pct": 3.0,
        "avg_win": 3.0,
        "avg_loss": 0.0,
        "profit_factor": None,
        "mdd_pct": 0.0,
        "by_news_type": {"contract": {"count": 1, "win_rate": 100.0, "avg_pnl": 3.0, "total_pnl": 3.0, "avg_win": 3.0, "avg_loss": 0.0, "profit_factor": None, "median_pnl": 3.0, "mdd_pct": 0.0}},
        "by_hour": {"08": {"count": 1, "win_rate": 100.0, "avg_pnl": 3.0, "total_pnl": 3.0, "avg_win": 3.0, "avg_loss": 0.0, "profit_factor": None, "median_pnl": 3.0, "mdd_pct": 0.0}},
        "by_hour_bucket": {"pre_open": {"count": 1, "win_rate": 100.0, "avg_pnl": 3.0, "total_pnl": 3.0, "avg_win": 3.0, "avg_loss": 0.0, "profit_factor": None, "median_pnl": 3.0, "mdd_pct": 0.0}},
        "by_confidence": {"81-85": {"count": 1, "win_rate": 100.0, "avg_pnl": 3.0, "total_pnl": 3.0, "avg_win": 3.0, "avg_loss": 0.0, "profit_factor": None, "median_pnl": 3.0, "mdd_pct": 0.0}},
        "by_ticker": {"111111": {"count": 1, "win_rate": 100.0, "avg_pnl": 3.0, "total_pnl": 3.0, "avg_win": 3.0, "avg_loss": 0.0, "profit_factor": None, "median_pnl": 3.0, "mdd_pct": 0.0}},
        "condition_scores": {"entry": [{"category": "news_type", "label": "contract", "count": 1, "win_rate": 100.0, "avg_pnl": 3.0, "total_pnl": 3.0, "avg_win": 3.0, "avg_loss": 0.0, "profit_factor": None, "median_pnl": 3.0, "mdd_pct": 0.0, "score": 1.0}], "exit": {"candidates": [{"params": {"paper_take_profit_pct": 2.0, "paper_stop_loss_pct": -1.5, "trailing_stop_activation_pct": 0.5, "max_hold_minutes": 15, "t5m_loss_exit_enabled": True}, "win_rate": 100.0, "avg_pnl": 3.0, "total_pnl": 3.0, "score": 1.0}]}},
        "by_exit_type": {"TP": {"count": 1, "win_rate": 100.0, "avg_pnl": 3.0, "total_pnl": 3.0, "avg_win": 3.0, "avg_loss": 0.0, "profit_factor": None, "median_pnl": 3.0, "mdd_pct": 0.0}},
        "horizon_returns": {"t+30s": {"count": 1, "win_rate": 100.0, "avg": 1.0, "median": 1.0}},
        "profit_leakage": [],
    }

    rendered = mod.render_report(stats, [trade])
    assert "By News Type" in rendered
    assert "Top Entry Conditions" in rendered
    assert "Exit Optimization Candidates" in rendered
    assert "Trades Detail" in rendered
