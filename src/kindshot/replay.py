"""Replay mode: re-run LLM decisions on previously logged events."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from kindshot.config import Config
from kindshot.decision import DecisionEngine, LlmTimeoutError, LlmCallError, LlmParseError
from kindshot.guardrails import check_guardrails
from kindshot.hold_profile import get_max_hold_minutes
from kindshot.logger import JsonlLogger
from kindshot.models import (
    Action,
    Bucket,
    ContextCard,
    DecisionRecord,
    EventRecord,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReplayOpsSelectionPolicy:
    limit: int = 5
    include_reported: bool = False
    require_runtime: bool = False
    require_collector: bool = False
    min_merged_events: int = 1


def _report_output_path(config: Config, dt: str, explicit_path: str = "") -> Path:
    if explicit_path:
        return Path(explicit_path)
    return config.replay_day_reports_dir / f"{dt}.json"


def _status_output_path(config: Config, dt: str, explicit_path: str = "") -> Path:
    if explicit_path:
        return Path(explicit_path)
    return config.replay_day_status_dir / f"{dt}.json"


def _ops_output_path(config: Config, explicit_path: str = "") -> Path:
    if explicit_path:
        return Path(explicit_path)
    return config.replay_ops_summary_path


def _ops_queue_output_path(config: Config, explicit_path: str = "") -> Path:
    if explicit_path:
        return Path(explicit_path)
    return config.replay_ops_queue_ready_path


def _ops_run_output_path(config: Config, explicit_path: str = "") -> Path:
    if explicit_path:
        return Path(explicit_path)
    return config.replay_ops_run_ready_path


def _ops_cycle_output_path(config: Config, explicit_path: str = "") -> Path:
    if explicit_path:
        return Path(explicit_path)
    return config.replay_ops_cycle_ready_path


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _collector_manifest_index_path(config: Config) -> Path:
    return config.collector_manifests_dir / "index.json"


def _runtime_manifest_index_path(config: Config) -> Path:
    return config.runtime_index_path


def _load_collector_manifest_index(config: Config) -> dict[str, Any]:
    path = _collector_manifest_index_path(config)
    if not path.exists():
        return {"generated_at": "", "entries": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_runtime_manifest_index(config: Config) -> dict[str, Any]:
    path = _runtime_manifest_index_path(config)
    if not path.exists():
        return {"generated_at": "", "entries": []}
    return json.loads(path.read_text(encoding="utf-8"))


def list_collected_dates(config: Config, *, include_partial: bool = False) -> list[str]:
    payload = _load_collector_manifest_index(config)
    dates: list[str] = []
    for row in payload.get("entries", []):
        status = str(row.get("status", "")).strip()
        if status == "complete" or (include_partial and status == "partial"):
            dt = str(row.get("date", "")).strip()
            if dt:
                dates.append(dt)
    return dates


def list_runtime_dates(config: Config) -> list[str]:
    payload = _load_runtime_manifest_index(config)
    dates: list[str] = []
    for row in payload.get("entries", []):
        dt = str(row.get("date", "")).strip()
        if dt:
            dates.append(dt)
    return dates


def load_collected_day_manifest(config: Config, dt: str) -> dict[str, Any]:
    path = config.collector_manifests_dir / f"{dt}.json"
    if not path.exists():
        raise FileNotFoundError(f"collector manifest not found for {dt}: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_runtime_day_manifest(config: Config, dt: str) -> dict[str, Any]:
    payload = _load_runtime_manifest_index(config)
    for row in payload.get("entries", []):
        if str(row.get("date", "")).strip() == dt:
            return row
    raise FileNotFoundError(f"runtime manifest not found for {dt}: {config.runtime_index_path}")


def _load_jsonl_records(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def load_runtime_day_artifacts(config: Config, dt: str) -> dict[str, Any]:
    manifest = load_runtime_day_manifest(config, dt)
    artifacts = manifest.get("artifacts", {})
    payload: dict[str, Any] = {"date": dt, "generated_at": manifest.get("generated_at"), "artifacts": {}}

    for artifact_name in ("context_cards", "price_snapshots", "market_context"):
        artifact_meta = artifacts.get(artifact_name) or {}
        path_text = str(artifact_meta.get("path", "")).strip()
        if not path_text:
            continue
        path = Path(path_text)
        payload["artifacts"][artifact_name] = {
            **artifact_meta,
            "records": _load_jsonl_records(path) if path.exists() else [],
        }

    return payload


def load_collector_day_bundle(config: Config, dt: str) -> dict[str, Any]:
    manifest = load_collected_day_manifest(config, dt)
    paths = manifest.get("paths", {})
    payload: dict[str, Any] = {"date": dt, "manifest": manifest, "artifacts": {}}
    for artifact_name in ("news", "classifications", "daily_prices", "daily_index"):
        path_text = str(paths.get(artifact_name, "")).strip()
        if not path_text:
            continue
        path = Path(path_text)
        payload["artifacts"][artifact_name] = {
            "path": str(path),
            "exists": path.exists(),
            "records": _load_jsonl_records(path) if path.exists() else [],
        }
    return payload


def _headline_key(ticker: str, headline: str) -> str:
    return f"{ticker.strip()}::{headline.strip()}"


def _build_runtime_events(context_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row for row in context_rows
        if row.get("bucket") == "POS_STRONG" and row.get("quant_check_passed") is True
    ]


def _build_collector_events(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    news_rows = bundle.get("artifacts", {}).get("news", {}).get("records", [])
    classification_rows = bundle.get("artifacts", {}).get("classifications", {}).get("records", [])
    news_by_id = {str(row.get("news_id", "")).strip(): row for row in news_rows if str(row.get("news_id", "")).strip()}

    events: list[dict[str, Any]] = []
    for row in classification_rows:
        if row.get("bucket") != "POS_STRONG":
            continue
        news_id = str(row.get("news_id", "")).strip()
        news_row = news_by_id.get(news_id, {})
        tickers = row.get("tickers") or news_row.get("tickers") or []
        ticker = str(tickers[0]).strip() if tickers else ""
        headline = str(row.get("title") or news_row.get("title") or "").strip()
        if not ticker or not headline:
            continue
        detected_at = str(news_row.get("collected_at") or "").strip()
        disclosed_at = ""
        if news_row.get("date") and news_row.get("time"):
            disclosed_at = f"{news_row['date'][:4]}-{news_row['date'][4:6]}-{news_row['date'][6:8]}T{news_row['time'][:2]}:{news_row['time'][2:4]}:{news_row['time'][4:6]}+09:00"
        events.append(
            {
                "type": "collector_event",
                "event_id": f"collector:{news_id}" if news_id else f"collector:{ticker}:{headline}",
                "ticker": ticker,
                "corp_name": str(news_row.get("dorg") or "").strip(),
                "headline": headline,
                "bucket": "POS_STRONG",
                "detected_at": detected_at,
                "disclosed_at": disclosed_at,
                "ctx": {},
                "quant_check_passed": None,
                "event_source": "collector",
            }
        )
    return events


def _merge_day_events(runtime_events: list[dict[str, Any]], collector_events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    merged = list(runtime_events)
    seen_keys = {
        _headline_key(str(row.get("ticker", "")), str(row.get("headline", "")))
        for row in runtime_events
    }
    collector_added = 0
    collector_deduped = 0
    for row in collector_events:
        key = _headline_key(str(row.get("ticker", "")), str(row.get("headline", "")))
        if key in seen_keys:
            collector_deduped += 1
            continue
        merged.append(row)
        seen_keys.add(key)
        collector_added += 1
    return merged, {
        "runtime_events": len(runtime_events),
        "collector_events": len(collector_events),
        "collector_added": collector_added,
        "collector_deduped": collector_deduped,
    }


def _build_price_snapshot_map(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    price_snapshots: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        event_id = str(row.get("event_id", "")).strip()
        horizon = str(row.get("horizon", "")).strip()
        if event_id and horizon:
            price_snapshots.setdefault(event_id, {})[horizon] = row
    return price_snapshots


def _print_day_bundle_summary(
    dt: str,
    *,
    collector_bundle: Optional[dict[str, Any]],
    runtime_bundle: Optional[dict[str, Any]],
    merge_stats: dict[str, int],
) -> None:
    print("\n" + "=" * 60)
    print(f"DAY INPUT SUMMARY: {dt}")
    print("=" * 60)
    if collector_bundle is not None:
        manifest = collector_bundle.get("manifest", {})
        print(
            "Collector: status=%s news=%s classifications=%s daily_prices=%s daily_index=%s"
            % (
                manifest.get("status", "-"),
                manifest.get("counts", {}).get("news", 0),
                manifest.get("counts", {}).get("classifications", 0),
                manifest.get("counts", {}).get("daily_prices", 0),
                manifest.get("counts", {}).get("daily_index", 0),
            )
        )
    else:
        print("Collector: unavailable")
    if runtime_bundle is not None:
        artifacts = runtime_bundle.get("artifacts", {})
        print(
            "Runtime: context_cards=%d price_snapshots=%d market_context=%d"
            % (
                len(artifacts.get("context_cards", {}).get("records", [])),
                len(artifacts.get("price_snapshots", {}).get("records", [])),
                len(artifacts.get("market_context", {}).get("records", [])),
            )
        )
    else:
        print("Runtime: unavailable")
    print(
        "Replay events: runtime=%d collector_total=%d collector_added=%d collector_deduped=%d"
        % (
            merge_stats.get("runtime_events", 0),
            merge_stats.get("collector_events", 0),
            merge_stats.get("collector_added", 0),
            merge_stats.get("collector_deduped", 0),
        )
    )
    print("=" * 60)


def _collector_input_summary(bundle: Optional[dict[str, Any]]) -> dict[str, Any]:
    if bundle is None:
        return {"available": False}
    manifest = bundle.get("manifest", {})
    artifacts = bundle.get("artifacts", {})
    return {
        "available": True,
        "status": manifest.get("status", ""),
        "counts": manifest.get("counts", {}),
        "artifacts": {
            name: {
                "exists": bool(payload.get("exists", False)),
                "record_count": len(payload.get("records", [])),
                "path": payload.get("path", ""),
            }
            for name, payload in artifacts.items()
        },
    }


def _runtime_input_summary(bundle: Optional[dict[str, Any]]) -> dict[str, Any]:
    if bundle is None:
        return {"available": False}
    artifacts = bundle.get("artifacts", {})
    return {
        "available": True,
        "generated_at": bundle.get("generated_at", ""),
        "artifacts": {
            name: {
                "exists": bool(payload.get("exists", False)),
                "record_count": len(payload.get("records", [])),
                "path": payload.get("path", ""),
                "recorded_at": payload.get("recorded_at", ""),
            }
            for name, payload in artifacts.items()
        },
    }


def _build_replay_day_status(dt: str, *, collector_bundle: Optional[dict[str, Any]], runtime_bundle: Optional[dict[str, Any]]) -> dict[str, Any]:
    warnings: list[str] = []
    collector_summary = _collector_input_summary(collector_bundle)
    runtime_summary = _runtime_input_summary(runtime_bundle)

    if not collector_summary.get("available"):
        warnings.append("COLLECTOR_MANIFEST_MISSING")
    else:
        status = str(collector_summary.get("status", "")).strip()
        if status and status != "complete":
            warnings.append("COLLECTOR_PARTIAL_STATUS")
        artifacts = collector_summary.get("artifacts", {})
        if not artifacts.get("news", {}).get("exists", False):
            warnings.append("COLLECTOR_NEWS_MISSING")
        if not artifacts.get("classifications", {}).get("exists", False):
            warnings.append("COLLECTOR_CLASSIFICATIONS_MISSING")

    if not runtime_summary.get("available"):
        warnings.extend([
            "RUNTIME_CONTEXT_CARDS_MISSING",
            "RUNTIME_PRICE_SNAPSHOTS_MISSING",
            "RUNTIME_MARKET_CONTEXT_MISSING",
        ])
    else:
        artifacts = runtime_summary.get("artifacts", {})
        if not artifacts.get("context_cards", {}).get("exists", False):
            warnings.append("RUNTIME_CONTEXT_CARDS_MISSING")
        if not artifacts.get("price_snapshots", {}).get("exists", False):
            warnings.append("RUNTIME_PRICE_SNAPSHOTS_MISSING")
        if not artifacts.get("market_context", {}).get("exists", False):
            warnings.append("RUNTIME_MARKET_CONTEXT_MISSING")

    runtime_events = _build_runtime_events(
        runtime_bundle.get("artifacts", {}).get("context_cards", {}).get("records", [])
        if runtime_bundle is not None else []
    )
    collector_events = _build_collector_events(collector_bundle) if collector_bundle is not None else []
    merged_events, merge_stats = _merge_day_events(runtime_events, collector_events)
    if not merged_events:
        warnings.append("NO_REPLAYABLE_EVENTS")

    if collector_summary.get("available") and runtime_summary.get("available"):
        health = "partial_inputs" if warnings else "ready"
    elif collector_summary.get("available"):
        collector_status = str(collector_summary.get("status", "")).strip()
        if collector_status and collector_status != "complete":
            health = "partial_inputs"
        else:
            health = "collector_only" if merged_events else "missing_inputs"
    elif runtime_summary.get("available"):
        health = "runtime_only" if merged_events else "missing_inputs"
    else:
        health = "missing_inputs"

    return {
        "date": dt,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "health": health,
        "warnings": warnings,
        "input": {
            "collector": collector_summary,
            "runtime": runtime_summary,
        },
        "replayability": {
            "runtime_event_count": len(runtime_events),
            "collector_event_count": len(collector_events),
            "merged_event_count": len(merged_events),
            "merge": merge_stats,
        },
    }


def _print_replay_day_status(report: dict[str, Any]) -> None:
    print("\n" + "=" * 60)
    print(f"DAY STATUS: {report['date']}")
    print("=" * 60)
    print(f"Health: {report['health']}")
    print(f"Warnings: {', '.join(report['warnings']) if report['warnings'] else '-'}")
    print(
        "Replayability: runtime=%d collector=%d merged=%d"
        % (
            report["replayability"]["runtime_event_count"],
            report["replayability"]["collector_event_count"],
            report["replayability"]["merged_event_count"],
        )
    )
    collector = report["input"]["collector"]
    runtime = report["input"]["runtime"]
    print(f"Collector available: {'yes' if collector.get('available') else 'no'}")
    print(f"Runtime available: {'yes' if runtime.get('available') else 'no'}")
    print("=" * 60)


def _available_replay_dates(config: Config) -> list[str]:
    dates = set(list_collected_dates(config, include_partial=True))
    dates.update(list_runtime_dates(config))
    return sorted(dates, reverse=True)


def _ops_row_for_date(config: Config, dt: str) -> dict[str, Any]:
    status_path = _status_output_path(config, dt)
    report_path = _report_output_path(config, dt)
    status = _read_json_file(status_path) or replay_day_status(dt, config, output_path=str(status_path))
    report = _read_json_file(report_path)
    return {
        "date": dt,
        "health": status.get("health", "missing_inputs"),
        "warning_count": len(status.get("warnings", [])),
        "warnings": status.get("warnings", []),
        "merged_event_count": status.get("replayability", {}).get("merged_event_count", 0),
        "collector_available": bool(status.get("input", {}).get("collector", {}).get("available", False)),
        "runtime_available": bool(status.get("input", {}).get("runtime", {}).get("available", False)),
        "report_available": bool(report),
        "buy_decisions": int(report.get("summary", {}).get("buy_decisions", 0) or 0),
        "price_data_trades": int(report.get("summary", {}).get("price_data_trades", 0) or 0),
    }


def _build_replay_ops_summary(config: Config, *, limit: int) -> dict[str, Any]:
    dates = _available_replay_dates(config)
    rows = [_ops_row_for_date(config, dt) for dt in dates]
    health_counts: dict[str, int] = {}
    warning_counts: dict[str, int] = {}
    for row in rows:
        health = str(row.get("health", "missing_inputs"))
        health_counts[health] = health_counts.get(health, 0) + 1
        for warning in row.get("warnings", []):
            warning_counts[warning] = warning_counts.get(warning, 0) + 1
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date_count": len(rows),
        "health_counts": health_counts,
        "warning_counts": warning_counts,
        "all_rows": rows,
        "rows": rows[:limit],
    }


def _selection_reason(row: dict[str, Any], policy: ReplayOpsSelectionPolicy) -> str:
    if row.get("health") != "ready":
        return "health_not_ready"
    if not policy.include_reported and row.get("report_available"):
        return "existing_report"
    if policy.require_runtime and not row.get("runtime_available"):
        return "missing_runtime"
    if policy.require_collector and not row.get("collector_available"):
        return "missing_collector"
    if int(row.get("merged_event_count", 0) or 0) < policy.min_merged_events:
        return "merged_event_below_min"
    return "selected"


def _build_replay_ops_ready_queue(config: Config, *, policy: ReplayOpsSelectionPolicy) -> dict[str, Any]:
    ops = _build_replay_ops_summary(config, limit=max(policy.limit, 1))
    queue_rows: list[dict[str, Any]] = []
    candidate_count = 0
    selected_count = 0
    skipped_counts: dict[str, int] = {}

    for row in ops.get("all_rows", []):
        reason = _selection_reason(row, policy)
        if row.get("health") == "ready":
            candidate_count += 1
        selected = False
        if reason == "selected":
            if selected_count < policy.limit:
                selected = True
                selected_count += 1
            else:
                reason = "limit_exceeded"
        if not selected:
            skipped_counts[reason] = skipped_counts.get(reason, 0) + 1
        queue_rows.append(
            {
                "date": row.get("date", ""),
                "health": row.get("health", "missing_inputs"),
                "selected": selected,
                "selection_reason": "selected" if selected else reason,
                "report_available": bool(row.get("report_available")),
                "collector_available": bool(row.get("collector_available")),
                "runtime_available": bool(row.get("runtime_available")),
                "merged_event_count": int(row.get("merged_event_count", 0) or 0),
                "warning_count": int(row.get("warning_count", 0) or 0),
                "warnings": list(row.get("warnings", [])),
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy": asdict(policy),
        "candidate_count": candidate_count,
        "selected_count": selected_count,
        "skipped_counts": skipped_counts,
        "rows": queue_rows,
    }


def _print_replay_ops_summary(report: dict[str, Any]) -> None:
    print("\n" + "=" * 60)
    print("REPLAY OPS SUMMARY")
    print("=" * 60)


def _print_replay_ops_queue_ready(report: dict[str, Any]) -> None:
    print("\n" + "=" * 60)
    print("REPLAY OPS QUEUE READY")
    print("=" * 60)
    print(f"Policy: {report['policy']}")
    print(f"Candidates: {report['candidate_count']}")
    print(f"Selected: {report['selected_count']}")
    print(f"Skipped counts: {report['skipped_counts'] or '-'}")
    print("\nRows:")
    for row in report.get("rows", []):
        print(
            "%s selected=%s reason=%s health=%s merged=%d collector=%s runtime=%s report=%s"
            % (
                row["date"],
                "yes" if row["selected"] else "no",
                row["selection_reason"],
                row["health"],
                row["merged_event_count"],
                "yes" if row["collector_available"] else "no",
                "yes" if row["runtime_available"] else "no",
                "yes" if row["report_available"] else "no",
            )
        )
    print("=" * 60)


def _print_replay_ops_run_ready(report: dict[str, Any]) -> None:
    print("\n" + "=" * 60)
    print("REPLAY OPS RUN READY")
    print("=" * 60)
    print(f"Policy: {report['policy']}")
    print(f"Candidates: {report['candidate_count']}")
    print(f"Selected: {report['selected_count']}")
    print(f"Executed: {report['executed_count']}")
    print(f"Skipped counts: {report['skipped_counts'] or '-'}")
    print("\nRows:")
    for row in report.get("rows", []):
        print(
            "%s selected=%s executed=%s reason=%s report=%s buys=%d price_trades=%d"
            % (
                row["date"],
                "yes" if row["selected"] else "no",
                "yes" if row["executed"] else "no",
                row["selection_reason"],
                row["report_path"] or "-",
                row["summary"].get("buy_decisions", 0),
                row["summary"].get("price_data_trades", 0),
            )
        )
    print("=" * 60)


def _print_replay_ops_cycle_ready(report: dict[str, Any]) -> None:
    print("\n" + "=" * 60)
    print("REPLAY OPS CYCLE READY")
    print("=" * 60)
    print(f"Policy: {report['policy']}")
    print(f"Continue on error: {'yes' if report['continue_on_error'] else 'no'}")
    print(f"Executed: {report['executed_count']}")
    print(f"Failed: {report['failed_count']}")
    print(f"Stopped early: {'yes' if report['stopped_early'] else 'no'}")
    print(f"Queue path: {report['queue_path']}")
    print(f"Run path: {report['run_path']}")
    print(f"Summary path: {report['summary_path']}")
    print("\nRows:")
    for row in report.get("rows", []):
        print(
            "%s selected=%s executed=%s error=%s report=%s buys=%d price_trades=%d"
            % (
                row["date"],
                "yes" if row["selected"] else "no",
                "yes" if row["executed"] else "no",
                row["error"] or "-",
                row["report_path"] or "-",
                row["summary"].get("buy_decisions", 0),
                row["summary"].get("price_data_trades", 0),
            )
        )
    print("=" * 60)


def _summarize_returns(returns: list[float]) -> dict[str, float]:
    """Summarize trade returns with simple risk-aware metrics."""
    if not returns:
        return {}

    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]

    equity = 1.0
    peak = 1.0
    max_drawdown_pct = 0.0
    for ret_pct in returns:
        equity *= 1 + ret_pct / 100
        peak = max(peak, equity)
        drawdown_pct = (equity / peak - 1) * 100
        max_drawdown_pct = min(max_drawdown_pct, drawdown_pct)

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    return {
        "trade_count": float(len(returns)),
        "win_rate_pct": len(wins) / len(returns) * 100,
        "avg_return_pct": sum(returns) / len(returns),
        "avg_win_pct": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss_pct": sum(losses) / len(losses) if losses else 0.0,
        "best_pct": max(returns),
        "worst_pct": min(returns),
        "max_drawdown_pct": max_drawdown_pct,
        "profit_factor": profit_factor,
    }


def _load_actionable_events(log_path: Path) -> list[dict]:
    """Load POS_STRONG events that passed quant from a JSONL log file (deduped by event_id)."""
    seen_ids: set[str] = set()
    events: list[dict] = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("type") != "event":
                continue
            if rec.get("bucket") != "POS_STRONG":
                continue
            if rec.get("quant_check_passed") is not True:
                continue
            eid = rec.get("event_id")
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            events.append(rec)
    return events


def _load_price_snapshots(log_path: Path) -> dict[str, dict[str, dict]]:
    """Load price_snapshot records keyed by event_id → horizon → snapshot data."""
    snapshots: dict[str, dict[str, dict]] = {}
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("type") != "price_snapshot":
                continue
            eid = rec.get("event_id", "")
            horizon = rec.get("horizon", "")
            if eid and horizon:
                snapshots.setdefault(eid, {})[horizon] = rec
    return snapshots


async def _fetch_post_hoc_prices(ticker: str, date_str: str) -> dict:
    """Fetch post-hoc daily prices via pykrx for a given date."""

    def _fetch() -> dict:
        try:
            from pykrx import stock

            df = stock.get_market_ohlcv(date_str, date_str, ticker)
            if df.empty:
                return {}
            row = df.iloc[0]
            return {
                "open": int(row["시가"]),
                "high": int(row["고가"]),
                "low": int(row["저가"]),
                "close": int(row["종가"]),
                "volume": int(row["거래량"]),
            }
        except Exception:
            logger.exception("pykrx post-hoc fetch failed for %s on %s", ticker, date_str)
            return {}

    return await asyncio.to_thread(_fetch)


async def _run_replay(
    *,
    source_name: str,
    events: list[dict[str, Any]],
    price_snapshots: dict[str, dict[str, dict[str, Any]]],
    config: Config,
    market_context: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    if not events:
        logger.info("No actionable events found in %s", source_name)
        return {
            "source": source_name,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total_actionable_events": 0,
                "buy_decisions": 0,
                "skip_decisions": 0,
                "llm_errors": 0,
                "returns_summary": {},
                "price_data_trades": 0,
                "price_snapshot_trades": 0,
                "pykrx_fallback_trades": 0,
            },
            "returns": [],
            "market_context_count": len(market_context or []),
        }

    logger.info("Replay: %d actionable events from %s", len(events), source_name)
    if market_context:
        logger.info("Replay runtime context: %d market snapshots available", len(market_context))

    engine = DecisionEngine(config)
    replay_log = JsonlLogger(config.log_dir, run_id=f"replay_{datetime.now().strftime('%Y%m%d_%H%M%S')}", file_prefix="replay")

    stats = {"total": 0, "buy": 0, "skip": 0, "error": 0, "returns": []}

    for rec in events:
        stats["total"] += 1
        ticker = rec["ticker"]
        headline = rec["headline"]
        corp_name = rec["corp_name"]
        event_id = rec.get("event_id", "")

        # Restore context card from logged data
        ctx_data = rec.get("ctx") or {}
        ctx = ContextCard(**{k: v for k, v in ctx_data.items() if k in ContextCard.model_fields})

        detected_at_raw = rec.get("detected_at", "")
        detected_at_dt: Optional[datetime] = None
        detected_at_str = detected_at_raw
        if detected_at_str:
            try:
                from datetime import timedelta, timezone as tz
                _KST = tz(timedelta(hours=9))
                dt = datetime.fromisoformat(detected_at_str)
                detected_at_dt = dt
                # Convert to KST to match live pipeline's KST prompt labeling
                dt_kst = dt.astimezone(_KST)
                detected_at_str = dt_kst.strftime("%H:%M:%S")
            except (ValueError, TypeError):
                detected_at_str = "09:00:00"
                detected_at_dt = None

        # LLM decision
        try:
            decision = await engine.decide(
                ticker=ticker,
                corp_name=corp_name,
                headline=headline,
                bucket=Bucket.POS_STRONG,
                ctx=ctx,
                detected_at_str=detected_at_str,
                keyword_hits=rec.get("keyword_hits") or [],
                run_id="replay",
                schema_version=config.schema_version,
            )
        except (LlmTimeoutError, LlmCallError, LlmParseError) as e:
            logger.warning("Replay LLM error for %s: %s", ticker, e)
            stats["error"] += 1
            continue

        decision.event_id = event_id
        decision.mode = "replay"

        # Guardrail
        gr = check_guardrails(
            ticker=ticker,
            config=config,
            spread_bps=ctx.spread_bps,
            adv_value_20d=ctx.adv_value_20d,
            ret_today=ctx.ret_today,
            intraday_value_vs_adv20d=ctx.intraday_value_vs_adv20d,
            quote_temp_stop=ctx.quote_temp_stop,
            quote_liquidation_trade=ctx.quote_liquidation_trade,
            top_ask_notional=ctx.top_ask_notional,
            decision_action=Action(decision.action.value),
            decision_confidence=decision.confidence,
            decision_time_kst=detected_at_dt,
            decision_hold_minutes=get_max_hold_minutes(headline, rec.get("keyword_hits") or [], config)
            if decision.action.value == "BUY" else 0,
        )
        if not gr.passed:
            logger.info("Replay GUARDRAIL block %s: %s", ticker, gr.reason)
            stats["skip"] += 1
            continue

        await replay_log.write(decision)

        if decision.action.value == "BUY":
            stats["buy"] += 1

            # Try price_snapshot t0/close first (most accurate)
            event_snaps = price_snapshots.get(event_id, {})
            t0_snap = event_snaps.get("t0")
            close_snap = event_snaps.get("close")

            if t0_snap and close_snap and t0_snap.get("px") and close_snap.get("px"):
                t0_px = t0_snap["px"]
                close_px = close_snap["px"]
                if t0_px > 0:
                    close_ret = (close_px - t0_px) / t0_px * 100
                    stats["returns"].append({
                        "ticker": ticker,
                        "headline": headline[:40],
                        "entry": t0_px,
                        "close": close_px,
                        "close_ret_pct": round(close_ret, 2),
                        "confidence": decision.confidence,
                        "price_source": "price_snapshot",
                    })
            else:
                # Fallback: pykrx open→close (less accurate)
                disclosed_at = rec.get("disclosed_at") or rec.get("detected_at")
                if disclosed_at:
                    try:
                        dt = datetime.fromisoformat(disclosed_at)
                        date_str = dt.strftime("%Y%m%d")
                        prices = await _fetch_post_hoc_prices(ticker, date_str)
                        if prices and prices.get("close") and prices.get("open"):
                            entry = prices["open"]
                            if entry > 0:
                                close_ret = (prices["close"] - entry) / entry * 100
                                stats["returns"].append({
                                    "ticker": ticker,
                                    "headline": headline[:40],
                                    "entry": entry,
                                    "close": prices["close"],
                                    "close_ret_pct": round(close_ret, 2),
                                    "confidence": decision.confidence,
                                    "price_source": "pykrx_ohlcv",
                                })
                    except (ValueError, TypeError):
                        logger.debug("Failed to compute pykrx return for %s", ticker)
        else:
            stats["skip"] += 1

    returns_summary: dict[str, Any] = {}
    snapshot_count = 0
    fallback_count = 0
    if stats["returns"]:
        rets = [r["close_ret_pct"] for r in stats["returns"]]
        returns_summary = _summarize_returns(rets)
        snapshot_count = sum(1 for r in stats["returns"] if r["price_source"] == "price_snapshot")
        fallback_count = len(rets) - snapshot_count

    report = {
        "source": source_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_actionable_events": stats["total"],
            "buy_decisions": stats["buy"],
            "skip_decisions": stats["skip"],
            "llm_errors": stats["error"],
            "returns_summary": returns_summary,
            "price_data_trades": len(stats["returns"]),
            "price_snapshot_trades": snapshot_count,
            "pykrx_fallback_trades": fallback_count,
        },
        "returns": sorted(stats["returns"], key=lambda x: x["close_ret_pct"], reverse=True),
        "market_context_count": len(market_context or []),
    }

    print("\n" + "=" * 60)
    print(f"REPLAY SUMMARY: {source_name}")
    print("=" * 60)
    print(f"Total actionable events: {report['summary']['total_actionable_events']}")
    print(f"BUY decisions: {report['summary']['buy_decisions']}")
    print(f"SKIP decisions: {report['summary']['skip_decisions']}")
    print(f"LLM errors: {report['summary']['llm_errors']}")

    if report["returns"]:
        summary = report["summary"]["returns_summary"]
        print(f"\n--- BUY P&L (close vs entry) ---")
        print(f"Trades with price data: {report['summary']['price_data_trades']} (snapshot: {report['summary']['price_snapshot_trades']}, pykrx fallback: {report['summary']['pykrx_fallback_trades']})")
        print(f"Win rate: {summary['win_rate_pct']:.0f}%")
        print(f"Avg return: {summary['avg_return_pct']:.2f}%")
        print(f"Avg win / loss: {summary['avg_win_pct']:.2f}% / {summary['avg_loss_pct']:.2f}%")
        print(f"Best: {summary['best_pct']:.2f}%  Worst: {summary['worst_pct']:.2f}%")
        print(f"Max drawdown: {summary['max_drawdown_pct']:.2f}%")
        pf = summary["profit_factor"]
        pf_text = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(f"Profit factor: {pf_text}")

        print(f"\nDetail:")
        for r in report["returns"]:
            src = "snap" if r["price_source"] == "price_snapshot" else "ohlcv"
            print(f"  {r['ticker']} {r['headline']} | conf={r['confidence']} "
                  f"entry={r['entry']:,.0f} close={r['close']:,.0f} ret={r['close_ret_pct']:+.2f}% [{src}]")
    else:
        print("\nNo BUY trades with price data available.")
    print("=" * 60)
    return report


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


async def replay(log_path: Path, config: Config, report_output_path: str = "") -> dict[str, Any]:
    """Replay logged events through LLM decision + post-hoc price analysis."""
    report = await _run_replay(
        source_name=log_path.name,
        events=_load_actionable_events(log_path),
        price_snapshots=_load_price_snapshots(log_path),
        config=config,
    )
    if report_output_path:
        _write_report(Path(report_output_path), report)
    return report


async def replay_runtime_date(dt: str, config: Config, report_output_path: str = "") -> dict[str, Any]:
    """Replay runtime artifacts for a specific KST date."""
    bundle = load_runtime_day_artifacts(config, dt)
    context_rows = bundle.get("artifacts", {}).get("context_cards", {}).get("records", [])
    price_rows = bundle.get("artifacts", {}).get("price_snapshots", {}).get("records", [])
    market_rows = bundle.get("artifacts", {}).get("market_context", {}).get("records", [])

    events = [
        row for row in context_rows
        if row.get("bucket") == "POS_STRONG" and row.get("quant_check_passed") is True
    ]
    price_snapshots: dict[str, dict[str, dict[str, Any]]] = {}
    for row in price_rows:
        event_id = str(row.get("event_id", "")).strip()
        horizon = str(row.get("horizon", "")).strip()
        if event_id and horizon:
            price_snapshots.setdefault(event_id, {})[horizon] = row

    report = await _run_replay(
        source_name=f"runtime:{dt}",
        events=events,
        price_snapshots=price_snapshots,
        config=config,
        market_context=market_rows,
    )
    if report_output_path:
        _write_report(Path(report_output_path), report)
    return report


async def replay_day(dt: str, config: Config, report_output_path: str = "") -> dict[str, Any]:
    """Replay a day using both collector manifests and runtime artifacts when available."""
    collector_bundle: Optional[dict[str, Any]] = None
    runtime_bundle: Optional[dict[str, Any]] = None

    try:
        collector_bundle = load_collector_day_bundle(config, dt)
    except FileNotFoundError:
        collector_bundle = None
    try:
        runtime_bundle = load_runtime_day_artifacts(config, dt)
    except FileNotFoundError:
        runtime_bundle = None

    if collector_bundle is None and runtime_bundle is None:
        raise FileNotFoundError(f"no collector manifest or runtime artifacts found for {dt}")

    runtime_events = _build_runtime_events(
        runtime_bundle.get("artifacts", {}).get("context_cards", {}).get("records", [])
        if runtime_bundle is not None else []
    )
    collector_events = _build_collector_events(collector_bundle) if collector_bundle is not None else []
    merged_events, merge_stats = _merge_day_events(runtime_events, collector_events)
    runtime_prices = _build_price_snapshot_map(
        runtime_bundle.get("artifacts", {}).get("price_snapshots", {}).get("records", [])
        if runtime_bundle is not None else []
    )
    market_rows = (
        runtime_bundle.get("artifacts", {}).get("market_context", {}).get("records", [])
        if runtime_bundle is not None else []
    )

    _print_day_bundle_summary(
        dt,
        collector_bundle=collector_bundle,
        runtime_bundle=runtime_bundle,
        merge_stats=merge_stats,
    )
    replay_report = await _run_replay(
        source_name=f"day:{dt}",
        events=merged_events,
        price_snapshots=runtime_prices,
        config=config,
        market_context=market_rows,
    )
    report = {
        "date": dt,
        "source": "replay_day",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": {
            "collector": _collector_input_summary(collector_bundle),
            "runtime": _runtime_input_summary(runtime_bundle),
            "merge": merge_stats,
        },
        "summary": replay_report["summary"],
        "returns": replay_report["returns"],
        "market_context_count": replay_report["market_context_count"],
    }
    _write_report(_report_output_path(config, dt, report_output_path), report)
    return report


def replay_day_status(dt: str, config: Config, output_path: str = "") -> dict[str, Any]:
    """Inspect collector/runtime day inputs before replay execution."""
    try:
        collector_bundle = load_collector_day_bundle(config, dt)
    except FileNotFoundError:
        collector_bundle = None
    try:
        runtime_bundle = load_runtime_day_artifacts(config, dt)
    except FileNotFoundError:
        runtime_bundle = None

    report = _build_replay_day_status(
        dt,
        collector_bundle=collector_bundle,
        runtime_bundle=runtime_bundle,
    )
    _print_replay_day_status(report)
    _write_report(_status_output_path(config, dt, output_path), report)
    return report


def replay_ops_summary(config: Config, *, limit: int = 10, output_path: str = "") -> dict[str, Any]:
    """Summarize replay readiness and outcomes across multiple dates."""
    report = _build_replay_ops_summary(config, limit=limit)
    _print_replay_ops_summary(report)
    _write_report(_ops_output_path(config, output_path), report)
    return report


def replay_ops_queue_ready(
    config: Config,
    *,
    limit: int = 5,
    include_reported: bool = False,
    require_runtime: bool = False,
    require_collector: bool = False,
    min_merged_events: int = 1,
    output_path: str = "",
) -> dict[str, Any]:
    """Build a policy-controlled ready queue without executing replay-day."""
    policy = ReplayOpsSelectionPolicy(
        limit=max(1, limit),
        include_reported=include_reported,
        require_runtime=require_runtime,
        require_collector=require_collector,
        min_merged_events=max(0, min_merged_events),
    )
    report = _build_replay_ops_ready_queue(config, policy=policy)
    _print_replay_ops_queue_ready(report)
    _write_report(_ops_queue_output_path(config, output_path), report)
    return report


async def _execute_replay_ops_queue(
    queue_report: dict[str, Any],
    config: Config,
    *,
    continue_on_error: bool,
) -> tuple[dict[str, Any], bool]:
    rows: list[dict[str, Any]] = []
    stopped_early = False

    for row in queue_report.get("rows", []):
        dt = str(row.get("date", "")).strip()
        base_row = {
            **row,
            "executed": False,
            "error": "",
            "report_path": str(_report_output_path(config, dt)) if dt and row.get("report_available") else "",
            "summary": {},
        }
        if not dt or not row.get("selected"):
            rows.append(base_row)
            continue
        try:
            replay_report = await replay_day(dt, config)
        except Exception as exc:
            base_row["error"] = f"{type(exc).__name__}: {exc}"
            rows.append(base_row)
            if not continue_on_error:
                stopped_early = True
                break
            continue

        base_row["executed"] = True
        base_row["report_path"] = str(_report_output_path(config, dt))
        base_row["summary"] = replay_report.get("summary", {})
        rows.append(base_row)

    return (
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "policy": queue_report.get("policy", {}),
            "candidate_count": queue_report.get("candidate_count", 0),
            "selected_count": queue_report.get("selected_count", 0),
            "executed_count": sum(1 for row in rows if row.get("executed")),
            "failed_count": sum(1 for row in rows if row.get("error")),
            "skipped_counts": queue_report.get("skipped_counts", {}),
            "rows": rows,
        },
        stopped_early,
    )


async def replay_ops_run_ready(
    config: Config,
    *,
    limit: int = 5,
    include_reported: bool = False,
    require_runtime: bool = False,
    require_collector: bool = False,
    min_merged_events: int = 1,
    output_path: str = "",
) -> dict[str, Any]:
    """Execute replay-day for dates selected by the shared replay ops policy."""
    policy = ReplayOpsSelectionPolicy(
        limit=max(1, limit),
        include_reported=include_reported,
        require_runtime=require_runtime,
        require_collector=require_collector,
        min_merged_events=max(0, min_merged_events),
    )
    queue_report = _build_replay_ops_ready_queue(config, policy=policy)
    report, _ = await _execute_replay_ops_queue(queue_report, config, continue_on_error=False)
    _print_replay_ops_run_ready(report)
    _write_report(_ops_run_output_path(config, output_path), report)
    return report


async def replay_ops_cycle_ready(
    config: Config,
    *,
    limit: int = 5,
    include_reported: bool = False,
    require_runtime: bool = False,
    require_collector: bool = False,
    min_merged_events: int = 1,
    continue_on_error: bool = False,
    output_path: str = "",
) -> dict[str, Any]:
    """Run queue, execution, and summary refresh in one batch-oriented ops command."""
    queue_report = replay_ops_queue_ready(
        config,
        limit=limit,
        include_reported=include_reported,
        require_runtime=require_runtime,
        require_collector=require_collector,
        min_merged_events=min_merged_events,
    )
    run_report, stopped_early = await _execute_replay_ops_queue(
        queue_report,
        config,
        continue_on_error=continue_on_error,
    )
    _write_report(_ops_run_output_path(config), run_report)
    summary_report = replay_ops_summary(config, limit=max(limit, 10))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy": queue_report.get("policy", {}),
        "continue_on_error": continue_on_error,
        "queue_path": str(_ops_queue_output_path(config)),
        "run_path": str(_ops_run_output_path(config)),
        "summary_path": str(_ops_output_path(config)),
        "executed_count": run_report.get("executed_count", 0),
        "failed_count": run_report.get("failed_count", 0),
        "stopped_early": stopped_early,
        "summary_health_counts": summary_report.get("health_counts", {}),
        "rows": run_report.get("rows", []),
    }
    _print_replay_ops_cycle_ready(report)
    _write_report(_ops_cycle_output_path(config, output_path), report)
    return report
