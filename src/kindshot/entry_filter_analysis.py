"""Entry filter analysis helpers and local evidence report builder."""

from __future__ import annotations

import importlib.util
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional

from kindshot.kis_client import OrderbookSnapshot
from kindshot.tz import KST as _KST

DELAY_BUCKETS_MS: tuple[int, ...] = (30_000, 60_000, 120_000)


@dataclass(frozen=True)
class EntryFilterTradeRow:
    event_id: str
    date: str
    ticker: str
    headline: str
    exit_pnl_pct: float
    close_ret_pct: float | None
    effective_entry_delay_ms: int | None
    prior_volume_rate: float | None
    intraday_value_vs_adv20d: float | None
    orderbook_level1_bid_ask_ratio: float | None
    orderbook_total_bid_ask_ratio: float | None


def _to_kst(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_KST)
    return dt.astimezone(_KST)


def compute_effective_entry_delay_ms(
    disclosed_at: datetime | None,
    entry_time: datetime | None,
) -> int | None:
    """Measure stale-entry delay relative to the later of disclosure or market open."""
    disclosed_kst = _to_kst(disclosed_at)
    entry_kst = _to_kst(entry_time)
    if disclosed_kst is None or entry_kst is None:
        return None
    market_open = entry_kst.replace(hour=9, minute=0, second=0, microsecond=0)
    effective_start = max(disclosed_kst, market_open)
    if entry_kst <= effective_start:
        return 0
    return int((entry_kst - effective_start).total_seconds() * 1000)


def compute_orderbook_bid_ask_ratios(
    orderbook_snapshot: OrderbookSnapshot | None,
) -> tuple[float | None, float | None]:
    if orderbook_snapshot is None:
        return None, None
    level1_ratio = None
    total_ratio = None
    if orderbook_snapshot.ask_size1 > 0:
        level1_ratio = orderbook_snapshot.bid_size1 / orderbook_snapshot.ask_size1
    if orderbook_snapshot.total_ask_size > 0:
        total_ratio = orderbook_snapshot.total_bid_size / orderbook_snapshot.total_ask_size
    return level1_ratio, total_ratio


def _safe_mean(values: Iterable[float]) -> float | None:
    items = list(values)
    if not items:
        return None
    return sum(items) / len(items)


def _stats(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "avg": round(avg, 4) if (avg := _safe_mean(values)) is not None else None,
        "min": round(min(values), 4) if values else None,
        "max": round(max(values), 4) if values else None,
    }


def summarize_delay_buckets(rows: list[EntryFilterTradeRow]) -> list[dict[str, Any]]:
    if not rows:
        return []
    summaries: list[dict[str, Any]] = []
    for threshold_ms in DELAY_BUCKETS_MS:
        cohort = [row for row in rows if row.effective_entry_delay_ms is not None and row.effective_entry_delay_ms >= threshold_ms]
        exit_values = [row.exit_pnl_pct for row in cohort]
        close_values = [row.close_ret_pct for row in cohort if row.close_ret_pct is not None]
        summaries.append(
            {
                "threshold_ms": threshold_ms,
                "threshold_s": threshold_ms / 1000,
                "exit_pnl_pct": _stats(exit_values),
                "close_ret_pct": _stats(close_values),
            }
        )
    return summaries


def recommend_max_entry_delay_ms(rows: list[EntryFilterTradeRow]) -> int | None:
    fast = [row.exit_pnl_pct for row in rows if row.effective_entry_delay_ms is not None and row.effective_entry_delay_ms < 60_000]
    stale = [row.exit_pnl_pct for row in rows if row.effective_entry_delay_ms is not None and row.effective_entry_delay_ms >= 60_000]
    if not stale:
        return None
    fast_avg = _safe_mean(fast)
    stale_avg = _safe_mean(stale)
    if fast_avg is None or stale_avg is None:
        return None
    if stale_avg < fast_avg:
        return 60_000
    return None


def _load_backtest_analysis_module() -> Any:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "backtest_analysis.py"
    spec = importlib.util.spec_from_file_location("kindshot_backtest_analysis_entry_filters", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_KST)
    return dt


def _coerce_orderbook_snapshot(payload: object) -> OrderbookSnapshot | None:
    if not isinstance(payload, dict):
        return None
    try:
        return OrderbookSnapshot(
            ask_price1=float(payload["ask_price1"]),
            bid_price1=float(payload["bid_price1"]),
            ask_size1=int(payload["ask_size1"]),
            bid_size1=int(payload["bid_size1"]),
            total_ask_size=int(payload["total_ask_size"]),
            total_bid_size=int(payload["total_bid_size"]),
            spread_bps=float(payload["spread_bps"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def build_entry_filter_report(
    *,
    log_dir: Path,
    runtime_context_dir: Path | None = None,
    runtime_snapshot_dir: Path | None = None,
) -> dict[str, Any]:
    backtest_analysis = _load_backtest_analysis_module()
    default_config = backtest_analysis.ExitSimulationConfig.from_runtime_defaults()

    context_rows: dict[str, dict[str, Any]] = {}
    if runtime_context_dir is not None and runtime_context_dir.exists():
        for path in sorted(runtime_context_dir.glob("*.jsonl")):
            with path.open(encoding="utf-8") as fh:
                for raw_line in fh:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    event_id = str(row.get("event_id", ""))
                    if not event_id:
                        continue
                    context_rows[event_id] = row

    trade_rows: list[EntryFilterTradeRow] = []
    orderbook_coverage_rows = 0
    volume_coverage_rows = 0

    for path in sorted(log_dir.glob("kindshot_*.jsonl")):
        date = path.stem.split("_")[-1]
        events, decisions, snapshots = backtest_analysis.load_day(path)
        backtest_analysis._append_runtime_snapshots(date, snapshots, runtime_snapshot_dir)
        trades = backtest_analysis.build_trades(events, decisions, snapshots, date, default_config)
        event_map = {str(event.get("event_id", "")): event for event in events}
        decision_map = {str(decision.get("event_id", "")): decision for decision in decisions}

        for trade in trades:
            event = event_map.get(trade.event_id)
            if event is None:
                continue
            decision = decision_map.get(trade.event_id, {})
            ctx = event.get("ctx") or {}
            context_payload = context_rows.get(trade.event_id, {})
            raw_context = context_payload.get("raw") or {}
            ctx_orderbook_ratio = ctx.get("orderbook_bid_ask_ratio")
            orderbook_snapshot = _coerce_orderbook_snapshot(raw_context.get("orderbook_snapshot"))
            level1_ratio, total_ratio = compute_orderbook_bid_ask_ratios(orderbook_snapshot)
            if total_ratio is None and isinstance(ctx_orderbook_ratio, (int, float)):
                total_ratio = float(ctx_orderbook_ratio)
            if level1_ratio is not None or total_ratio is not None:
                orderbook_coverage_rows += 1
            if ctx.get("prior_volume_rate") is not None:
                volume_coverage_rows += 1
            trade_rows.append(
                EntryFilterTradeRow(
                    event_id=trade.event_id,
                    date=trade.date,
                    ticker=trade.ticker,
                    headline=trade.headline,
                    exit_pnl_pct=round(trade.exit_pnl_pct, 4),
                    close_ret_pct=round(trade.snapshots.get("close"), 4) if trade.snapshots.get("close") is not None else None,
                    effective_entry_delay_ms=compute_effective_entry_delay_ms(
                        _parse_timestamp(event.get("disclosed_at")),
                        _parse_timestamp(decision.get("decided_at") or event.get("detected_at")),
                    ),
                    prior_volume_rate=ctx.get("prior_volume_rate"),
                    intraday_value_vs_adv20d=ctx.get("intraday_value_vs_adv20d"),
                    orderbook_level1_bid_ask_ratio=level1_ratio,
                    orderbook_total_bid_ask_ratio=total_ratio,
                )
            )

    delay_summary = summarize_delay_buckets(trade_rows)
    recommendation = recommend_max_entry_delay_ms(trade_rows)
    warnings: list[str] = []
    if orderbook_coverage_rows == 0:
        warnings.append("ORDERBOOK_RATIO_OUTCOME_COVERAGE_LOW")
    if volume_coverage_rows < 3:
        warnings.append("PRIOR_VOLUME_OUTCOME_COVERAGE_LOW")

    prior_volume_values = [row.prior_volume_rate for row in trade_rows if row.prior_volume_rate is not None]
    intraday_value_values = [row.intraday_value_vs_adv20d for row in trade_rows if row.intraday_value_vs_adv20d is not None]

    return {
        "generated_at": datetime.now(_KST).isoformat(),
        "trade_count": len(trade_rows),
        "delay_summary": delay_summary,
        "recommendations": {
            "max_entry_delay_ms": recommendation,
            "min_entry_prior_volume_rate": 70.0 if prior_volume_values else None,
            "orderbook_bid_ask_ratio_min": 0.8,
        },
        "coverage": {
            "orderbook_ratio_trade_count": orderbook_coverage_rows,
            "prior_volume_trade_count": volume_coverage_rows,
            "intraday_value_trade_count": len(intraday_value_values),
        },
        "current_trade_feature_ranges": {
            "prior_volume_rate": _stats(prior_volume_values),
            "intraday_value_vs_adv20d": _stats(intraday_value_values),
        },
        "warnings": warnings,
        "sample_trades": [asdict(row) for row in trade_rows[:10]],
    }


def render_entry_filter_report(report: dict[str, Any]) -> str:
    lines = [
        f"trade_count={report['trade_count']}",
        f"recommend_max_entry_delay_ms={report['recommendations']['max_entry_delay_ms']}",
        f"prior_volume_trade_count={report['coverage']['prior_volume_trade_count']}",
        f"orderbook_ratio_trade_count={report['coverage']['orderbook_ratio_trade_count']}",
    ]
    for bucket in report["delay_summary"]:
        lines.append(
            "delay>={threshold_s:.0f}s count={count} avg_exit={avg}".format(
                threshold_s=bucket["threshold_s"],
                count=bucket["exit_pnl_pct"]["count"],
                avg=bucket["exit_pnl_pct"]["avg"],
            )
        )
    if report["warnings"]:
        lines.append("warnings=" + ",".join(report["warnings"]))
    return "\n".join(lines) + "\n"
