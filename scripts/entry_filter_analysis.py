#!/usr/bin/env python3
"""Analyze entry-filter cohorts for delay, liquidity, and orderbook imbalance."""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from kindshot.config import Config

KST = timezone(timedelta(hours=9))
ANALYSIS_DIR = PROJECT_ROOT / "logs" / "daily_analysis"
RUNTIME_CONTEXT_DIR = PROJECT_ROOT / "data" / "runtime" / "context_cards"
RUNTIME_SNAPSHOT_DIR = PROJECT_ROOT / "data" / "runtime" / "price_snapshots"
LOG_DIR = PROJECT_ROOT / "logs"


@dataclass
class CohortStats:
    count: int
    win_rate: float | None
    avg_pnl_pct: float | None
    total_pnl_pct: float | None


def _load_backtest_analysis_module() -> Any:
    path = PROJECT_ROOT / "scripts" / "backtest_analysis.py"
    spec = importlib.util.spec_from_file_location("kindshot_backtest_analysis", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load backtest_analysis.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    return dt


def _stats(rows: list[dict[str, Any]]) -> CohortStats:
    if not rows:
        return CohortStats(count=0, win_rate=None, avg_pnl_pct=None, total_pnl_pct=None)
    pnls = [float(row["pnl_pct"]) for row in rows]
    wins = sum(1 for pnl in pnls if pnl > 0)
    return CohortStats(
        count=len(rows),
        win_rate=round(wins / len(rows) * 100, 1),
        avg_pnl_pct=round(sum(pnls) / len(pnls), 3),
        total_pnl_pct=round(sum(pnls), 3),
    )


def _build_trade_rows() -> list[dict[str, Any]]:
    module = _load_backtest_analysis_module()
    config = module.ExitSimulationConfig.from_runtime_defaults()
    rows: list[dict[str, Any]] = []
    for path in sorted(LOG_DIR.glob("kindshot_*.jsonl")):
        date_str = path.stem.split("_")[-1]
        events, decisions, snapshots = module.load_day(path)
        module._append_runtime_snapshots(date_str, snapshots, RUNTIME_SNAPSHOT_DIR)
        event_by_id = {str(event.get("event_id", "")): event for event in events}
        trades = module.build_trades(events, decisions, snapshots, date_str, config)
        for trade in trades:
            event = event_by_id.get(trade.event_id, {})
            ctx = event.get("ctx") or {}
            detected_at = _parse_dt(event.get("detected_at"))
            disclosed_at = _parse_dt(event.get("disclosed_at"))
            delay_s = None
            if detected_at is not None and disclosed_at is not None:
                delay_s = round((detected_at - disclosed_at).total_seconds(), 1)
            rows.append(
                {
                    "event_id": trade.event_id,
                    "date": trade.date,
                    "ticker": trade.ticker,
                    "headline": trade.headline,
                    "pnl_pct": trade.exit_pnl_pct,
                    "delay_s": delay_s,
                    "intraday_value_vs_adv20d": ctx.get("intraday_value_vs_adv20d"),
                    "prior_volume_rate": ctx.get("prior_volume_rate"),
                    "decision_source": trade.decision_source,
                }
            )
    return rows


def _runtime_orderbook_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(RUNTIME_CONTEXT_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("run_id") == "test_run":
                continue
            if row.get("mode") != "paper":
                continue
            raw = row.get("raw") or {}
            ratio = raw.get("orderbook_bid_ask_ratio")
            if ratio is None:
                continue
            rows.append(
                {
                    "event_id": row.get("event_id"),
                    "date": str(row.get("detected_at", ""))[:10],
                    "ratio": float(ratio),
                }
            )
    return rows


def build_report() -> dict[str, Any]:
    cfg = Config()
    trade_rows = _build_trade_rows()
    orderbook_rows = _runtime_orderbook_rows()

    delay_limit_s = round(cfg.max_entry_delay_ms / 1000, 1) if cfg.max_entry_delay_ms > 0 else 0.0
    delay_kept = [row for row in trade_rows if row["delay_s"] is not None and row["delay_s"] <= delay_limit_s]
    delay_late = [row for row in trade_rows if row["delay_s"] is not None and row["delay_s"] > delay_limit_s]

    liquidity_limit = cfg.min_intraday_value_vs_adv20d
    liquidity_kept = [
        row
        for row in trade_rows
        if row["intraday_value_vs_adv20d"] is not None and row["intraday_value_vs_adv20d"] >= liquidity_limit
    ]
    liquidity_thin = [
        row
        for row in trade_rows
        if row["intraday_value_vs_adv20d"] is not None and row["intraday_value_vs_adv20d"] < liquidity_limit
    ]

    ratio_limit = cfg.orderbook_bid_ask_ratio_min
    ratio_kept = [row for row in orderbook_rows if row["ratio"] >= ratio_limit]
    ratio_weak = [row for row in orderbook_rows if row["ratio"] < ratio_limit]

    return {
        "generated_at": datetime.now(KST).isoformat(),
        "config": {
            "max_entry_delay_ms": cfg.max_entry_delay_ms,
            "min_intraday_value_vs_adv20d": cfg.min_intraday_value_vs_adv20d,
            "orderbook_bid_ask_ratio_min": cfg.orderbook_bid_ask_ratio_min,
        },
        "trade_sample_count": len(trade_rows),
        "delay_analysis": {
            "threshold_seconds": delay_limit_s,
            "kept": asdict(_stats(delay_kept)),
            "late": asdict(_stats(delay_late)),
        },
        "liquidity_analysis": {
            "threshold_intraday_value_vs_adv20d": liquidity_limit,
            "kept": asdict(_stats(liquidity_kept)),
            "thin": asdict(_stats(liquidity_thin)),
        },
        "orderbook_ratio_analysis": {
            "threshold_ratio": ratio_limit,
            "sample_count": len(orderbook_rows),
            "kept_count": len(ratio_kept),
            "weak_count": len(ratio_weak),
            "status": "insufficient_runtime_orderbook_history" if not orderbook_rows else "ok",
        },
        "recommendation": {
            "summary": (
                "Keep a 60s stale-entry block, raise participation to 0.15, and retain a conservative 0.8 bid/ask ratio floor."
            ),
            "caveat": (
                "Orderbook-ratio history is currently sparse outside tests, so the imbalance threshold should be revisited after fresh runtime accumulation."
            ),
        },
    }


def render_text(report: dict[str, Any]) -> str:
    delay = report["delay_analysis"]
    liquidity = report["liquidity_analysis"]
    orderbook = report["orderbook_ratio_analysis"]
    lines = [
        "# Entry Filter Analysis",
        "",
        f"Generated: {report['generated_at']}",
        f"Trade sample count: {report['trade_sample_count']}",
        "",
        "## Delay",
        f"- threshold_seconds={delay['threshold_seconds']}",
        f"- kept={delay['kept']}",
        f"- late={delay['late']}",
        "",
        "## Liquidity",
        f"- threshold_intraday_value_vs_adv20d={liquidity['threshold_intraday_value_vs_adv20d']}",
        f"- kept={liquidity['kept']}",
        f"- thin={liquidity['thin']}",
        "",
        "## Orderbook Ratio",
        f"- threshold_ratio={orderbook['threshold_ratio']}",
        f"- sample_count={orderbook['sample_count']}",
        f"- kept_count={orderbook['kept_count']}",
        f"- weak_count={orderbook['weak_count']}",
        f"- status={orderbook['status']}",
        "",
        "## Recommendation",
        f"- {report['recommendation']['summary']}",
        f"- caveat: {report['recommendation']['caveat']}",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    report = build_report()
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(KST).strftime("%Y%m%d")
    json_path = ANALYSIS_DIR / f"entry_filter_analysis_{stamp}.json"
    txt_path = ANALYSIS_DIR / f"entry_filter_analysis_{stamp}.txt"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    txt_path.write_text(render_text(report), encoding="utf-8")
    print(render_text(report).strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
