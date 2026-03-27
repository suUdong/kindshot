from __future__ import annotations

import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "monthly_full_strategy_backtest.py"
    spec = spec_from_file_location("monthly_full_strategy_backtest", path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def test_build_report_handles_current_strategy_blocks_and_version_metrics(tmp_path: Path) -> None:
    mod = _load_module()

    log_path = tmp_path / "logs" / "kindshot_20260327.jsonl"
    rows = [
        {
            "type": "event",
            "event_id": "e1",
            "ticker": "005930",
            "corp_name": "삼성전자",
            "headline": "삼성전자 흑자 전환",
            "bucket": "POS_STRONG",
            "keyword_hits": ["흑자전환"],
            "decision_action": "BUY",
            "decision_confidence": 84,
            "decision_size_hint": "M",
            "decision_reason": "historical buy",
            "detected_at": "2026-03-27T10:00:00+09:00",
            "delay_ms": 0,
            "ctx": {
                "ret_today": 1.0,
                "adv_value_20d": 10000000000.0,
                "spread_bps": 10.0,
                "intraday_value_vs_adv20d": 0.2,
            },
            "market_ctx": {},
        },
        {
            "type": "decision",
            "event_id": "e1",
            "action": "BUY",
            "confidence": 84,
            "size_hint": "M",
            "reason": "buy",
            "decision_source": "LLM",
        },
        {
            "type": "price_snapshot",
            "event_id": "e1",
            "horizon": "t0",
            "px": 100.0,
            "ret_long_vs_t0": None,
        },
        {
            "type": "price_snapshot",
            "event_id": "e1",
            "horizon": "t+5m",
            "px": 100.6,
            "ret_long_vs_t0": 0.006,
        },
        {
            "type": "price_snapshot",
            "event_id": "e1",
            "horizon": "t+20m",
            "px": 101.2,
            "ret_long_vs_t0": 0.012,
        },
        {
            "type": "price_snapshot",
            "event_id": "e1",
            "horizon": "close",
            "px": 101.0,
            "ret_long_vs_t0": 0.01,
        },
        {
            "type": "event",
            "event_id": "e2",
            "ticker": "000660",
            "corp_name": "SK하이닉스",
            "headline": "SK하이닉스 흑자 전환",
            "bucket": "POS_STRONG",
            "keyword_hits": ["흑자전환"],
            "decision_action": "BUY",
            "decision_confidence": 84,
            "decision_size_hint": "M",
            "decision_reason": "historical buy",
            "detected_at": "2026-03-27T10:10:00+09:00",
            "delay_ms": 70000,
            "ctx": {
                "ret_today": 0.8,
                "adv_value_20d": 10000000000.0,
                "spread_bps": 10.0,
                "intraday_value_vs_adv20d": 0.2,
            },
            "market_ctx": {},
        },
        {
            "type": "decision",
            "event_id": "e2",
            "action": "BUY",
            "confidence": 84,
            "size_hint": "M",
            "reason": "buy",
            "decision_source": "LLM",
        },
        {
            "type": "price_snapshot",
            "event_id": "e2",
            "horizon": "t0",
            "px": 200.0,
            "ret_long_vs_t0": None,
        },
        {
            "type": "price_snapshot",
            "event_id": "e2",
            "horizon": "t+5m",
            "px": 200.4,
            "ret_long_vs_t0": 0.002,
        },
        {
            "type": "price_snapshot",
            "event_id": "e2",
            "horizon": "t+20m",
            "px": 201.0,
            "ret_long_vs_t0": 0.005,
        },
        {
            "type": "price_snapshot",
            "event_id": "e2",
            "horizon": "close",
            "px": 200.8,
            "ret_long_vs_t0": 0.004,
        },
    ]
    _write_jsonl(log_path, rows)

    context_rows = [
        {
            "type": "context_card",
            "event_id": "e1",
            "delay_ms": 0,
            "ctx": {
                "ret_today": 1.0,
                "adv_value_20d": 10000000000.0,
                "spread_bps": 10.0,
                "intraday_value_vs_adv20d": 0.2,
            },
            "raw": {
                "ret_today": 1.0,
                "adv_value_20d": 10000000000.0,
                "spread_bps": 10.0,
                "intraday_value_vs_adv20d": 0.2,
                "sector": "",
            },
        },
        {
            "type": "context_card",
            "event_id": "e2",
            "delay_ms": 70000,
            "ctx": {
                "ret_today": 0.8,
                "adv_value_20d": 10000000000.0,
                "spread_bps": 10.0,
                "intraday_value_vs_adv20d": 0.2,
            },
            "raw": {
                "ret_today": 0.8,
                "adv_value_20d": 10000000000.0,
                "spread_bps": 10.0,
                "intraday_value_vs_adv20d": 0.2,
                "sector": "",
            },
        },
    ]
    _write_jsonl(tmp_path / "data" / "runtime" / "context_cards" / "20260327.jsonl", context_rows)

    report = mod.build_report(tmp_path, lookback_days=30)

    assert report["meta"]["available_window"]["from"] == "20260327"
    assert report["current_strategy_estimate"]["candidate_trade_count"] == 2
    assert report["current_strategy_estimate"]["accepted_trade_count"] == 1
    assert report["current_strategy_estimate"]["blocked_by_reason"]["ENTRY_DELAY_TOO_LATE"] == 1
    assert len(report["version_comparison"]) == 7
    assert report["best_parameter_set"]["entry"]["max_entry_delay_ms"] == 60000


def test_render_text_mentions_limitations(tmp_path: Path) -> None:
    mod = _load_module()
    report = {
        "meta": {
            "available_window": {"from": "20260310", "to": "20260327", "log_count": 14},
            "llm_replay_status": "error",
        },
        "current_strategy_estimate": {
            "candidate_trade_count": 2,
            "accepted_trade_count": 1,
            "blocked_trade_count": 1,
            "blocked_by_reason": {"ENTRY_DELAY_TOO_LATE": 1},
            "summary": {
                "win_rate_pct": 100.0,
                "total_ret_pct": 1.0,
                "total_pnl_won": 50000.0,
            },
        },
        "version_comparison": [],
        "best_parameter_set": {
            "entry": {
                "max_entry_delay_ms": 60000,
                "min_intraday_value_vs_adv20d": 0.15,
                "orderbook_bid_ask_ratio_min": 0.8,
            },
            "exit": {},
            "risk_v2": {
                "max_positions": 4,
                "consecutive_loss_halt": 3,
                "recent_trade_window": 4,
            },
            "llm": {
                "historical_actual_accuracy": 0.625,
                "historical_actual_buy_precision": 1.0,
                "current_replay_status": "error",
            },
        },
        "limitations": ["local window only", "llm replay blocked"],
    }

    text = mod.render_text(report)

    assert "Current strategy estimate" in text
    assert "ENTRY_DELAY_TOO_LATE" in text
    assert "llm replay blocked" in text
