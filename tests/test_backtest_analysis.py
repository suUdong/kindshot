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
                '{"type":"event","event_id":"e4","ticker":"444444","headline":"D사 자사주 소각 결정","bucket":"POS_STRONG","keyword_hits":["자사주 소각"],"decision_action":"BUY","skip_stage":"GUARDRAIL","skip_reason":"LOW_CONFIDENCE","decision_confidence":77,"detected_at":"2026-03-27T14:40:00+09:00","source":"KIS","dorg":"연합뉴스"}',
                '{"type":"price_snapshot","event_id":"shadow_e4","horizon":"t0","px":100.0,"ret_long_vs_t0":0.0}',
                '{"type":"price_snapshot","event_id":"shadow_e4","horizon":"t+30s","px":101.0,"ret_long_vs_t0":0.01}',
                '{"type":"price_snapshot","event_id":"shadow_e4","horizon":"t+5m","px":102.0,"ret_long_vs_t0":0.02}',
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
    assert stats["guardrail_review"]["blocked_buy_count"] == 1
    assert stats["guardrail_review"]["shadow_blocked_buy_count"] == 1
    assert stats["guardrail_review"]["by_reason"]["LOW_CONFIDENCE"]["count"] == 1
    assert "recent_pattern_profile" in stats
    assert "boost_patterns" in stats["recent_pattern_profile"]
    assert "loss_guardrail_patterns" in stats["recent_pattern_profile"]


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
        "guardrail_review": {
            "inline_buy_total": 2,
            "passed_buy_count": 1,
            "blocked_buy_count": 1,
            "block_rate_pct": 50.0,
            "replayed_passed_buy_count": 1,
            "shadow_blocked_buy_count": 1,
            "shadow_coverage_pct": 100.0,
            "blocked_shadow_summary": {"count": 1, "win_rate": 100.0, "avg_pnl": 2.0, "total_pnl": 2.0},
            "by_reason": {"LOW_CONFIDENCE": {"count": 1, "share_pct": 100.0, "shadow_count": 1, "shadow_summary": {"avg_pnl": 2.0}}},
            "by_confidence_band": {"75-77": 1},
            "by_hour_bucket": {"afternoon": 1},
        },
        "recent_pattern_profile": {
            "enabled": True,
            "analysis_dates": ["20260327"],
            "total_trades": 1,
            "boost_patterns": [
                {
                    "pattern_type": "hour_bucket",
                    "key": "pre_open",
                    "count": 1,
                    "win_rate": 1.0,
                    "total_pnl_pct": 3.0,
                    "confidence_delta": 3,
                }
            ],
            "loss_guardrail_patterns": [],
            "top_profit_exact": {
                "pattern_type": "news_type_ticker_hour_bucket",
                "key": "contract|111111|pre_open",
                "count": 1,
                "win_rate": 1.0,
                "total_pnl_pct": 3.0,
            },
            "top_loss_exact": None,
        },
        "by_exit_type": {"TP": {"count": 1, "win_rate": 100.0, "avg_pnl": 3.0, "total_pnl": 3.0, "avg_win": 3.0, "avg_loss": 0.0, "profit_factor": None, "median_pnl": 3.0, "mdd_pct": 0.0}},
        "horizon_returns": {"t+30s": {"count": 1, "win_rate": 100.0, "avg": 1.0, "median": 1.0}},
        "profit_leakage": [],
    }

    rendered = mod.render_report(stats, [trade])
    assert "Guardrail Review" in rendered
    assert "Recent Pattern Profile" in rendered
    assert "Blockers By Reason" in rendered
    assert "By News Type" in rendered
    assert "Top Entry Conditions" in rendered
    assert "Exit Optimization Candidates" in rendered
    assert "Trades Detail" in rendered


def test_recent_pattern_profile_normalizes_news_category_strings():
    from kindshot.config import Config
    from kindshot.pattern_profile import build_recent_pattern_profile_from_rows

    profile = build_recent_pattern_profile_from_rows(
        [
            {
                "date": "20260327",
                "ticker": "068270",
                "headline": "셀트리온, 품목허가 승인",
                "keyword_hits": '["허가"]',
                "news_category": "임상허가",
                "hour_slot": 9,
                "exit_ret_pct": -0.4,
            },
            {
                "date": "20260328",
                "ticker": "068270",
                "headline": "셀트리온, 품목허가 승인",
                "keyword_hits": '["허가"]',
                "news_category": "임상허가",
                "hour_slot": 10,
                "exit_ret_pct": -0.3,
            },
            {
                "date": "20260328",
                "ticker": "358570",
                "headline": "마이크론 최대 매출… 32만전자 보인다",
                "keyword_hits": [],
                "news_category": "",
                "hour_slot": 11,
                "exit_ret_pct": 0.2,
            },
            {
                "date": "20260329",
                "ticker": "358570",
                "headline": "마이크론 최대 매출… 32만전자 보인다",
                "keyword_hits": [],
                "news_category": "",
                "hour_slot": 12,
                "exit_ret_pct": 0.16,
            },
        ],
        Config(
            recent_pattern_min_trades=2,
            recent_pattern_profit_min_win_rate=0.5,
            recent_pattern_profit_min_total_pnl_pct=0.15,
            recent_pattern_loss_max_win_rate=0.25,
            recent_pattern_loss_max_total_pnl_pct=-0.5,
        ),
    )

    assert any(
        row.pattern_type == "news_type_ticker_hour_bucket"
        and row.news_type == "other"
        and row.ticker == "358570"
        and row.hour_bucket == "midday"
        for row in profile.boost_patterns
    )
    assert any(
        row.pattern_type == "news_type_ticker"
        and row.news_type == "clinical_regulatory"
        and row.ticker == "068270"
        for row in profile.loss_guardrail_patterns
    )
