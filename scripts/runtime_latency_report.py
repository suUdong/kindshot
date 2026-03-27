#!/usr/bin/env python3
"""Summarize recent runtime latency evidence from Kindshot JSONL logs."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from kindshot.runtime_latency import summarize_latency_samples
from kindshot.tz import KST as _KST

ANALYSIS_DIR = PROJECT_ROOT / "logs" / "daily_analysis"
_STAGES = (
    "news_to_pipeline_ms",
    "context_card_ms",
    "decision_total_ms",
    "guardrail_ms",
    "order_attempt_ms",
    "pipeline_total_ms",
    "llm_latency_ms",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize Kindshot runtime latency")
    parser.add_argument("--log-dir", type=Path, default=PROJECT_ROOT / "logs")
    parser.add_argument("--date", type=str, default="", help="Target YYYYMMDD log file")
    parser.add_argument("--limit", type=int, default=3, help="How many recent log files to inspect when --date is omitted")
    return parser.parse_args()


def _candidate_log_paths(log_dir: Path, date_str: str, limit: int) -> list[Path]:
    if date_str:
        target = log_dir / f"kindshot_{date_str}.jsonl"
        return [target] if target.exists() else []
    paths = sorted(log_dir.glob("kindshot_*.jsonl"))
    return paths[-max(1, limit):]


def _load_profile_rows(log_paths: list[Path]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in log_paths:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            try:
                row = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if row.get("type") != "event":
                continue
            profile = row.get("pipeline_profile")
            if isinstance(profile, dict):
                rows.append(row)
    return rows


def _build_report(rows: list[dict[str, object]], log_paths: list[Path]) -> dict[str, object]:
    generated_at = datetime.now(_KST).isoformat()
    if not rows:
        return {
            "generated_at": generated_at,
            "log_paths": [str(path) for path in log_paths],
            "profiled_events": 0,
            "stages": {},
            "bottlenecks": {},
            "decision_sources": {},
            "cache_layers": {},
            "warnings": ["No event rows with pipeline_profile were found in the selected log files."],
        }

    stage_values: dict[str, list[int]] = {stage: [] for stage in _STAGES}
    bottlenecks: Counter[str] = Counter()
    decision_sources: Counter[str] = Counter()
    cache_layers: Counter[str] = Counter()

    for row in rows:
        profile = row.get("pipeline_profile") or {}
        decision_source = row.get("decision_source")
        if decision_source:
            decision_sources[str(decision_source)] += 1
        cache_layer = profile.get("llm_cache_layer") or row.get("decision_cache_layer")
        if cache_layer:
            cache_layers[str(cache_layer)] += 1
        bottleneck = profile.get("bottleneck_stage")
        if bottleneck:
            bottlenecks[str(bottleneck)] += 1
        for stage in _STAGES:
            value = profile.get(stage)
            if isinstance(value, (int, float)):
                stage_values[stage].append(int(value))

    return {
        "generated_at": generated_at,
        "log_paths": [str(path) for path in log_paths],
        "profiled_events": len(rows),
        "stages": {
            stage: summarize_latency_samples(values)
            for stage, values in stage_values.items()
        },
        "bottlenecks": dict(bottlenecks.most_common(5)),
        "decision_sources": dict(decision_sources),
        "cache_layers": dict(cache_layers),
        "warnings": [],
    }


def _render_report(report: dict[str, object]) -> str:
    lines = [
        "Kindshot Runtime Latency Report",
        f"Generated: {report['generated_at']}",
        f"Profiled events: {report['profiled_events']}",
    ]
    if report.get("log_paths"):
        lines.append("Logs:")
        for path in report["log_paths"]:
            lines.append(f"- {path}")
    warnings = report.get("warnings") or []
    if warnings:
        lines.append("Warnings:")
        for warning in warnings:
            lines.append(f"- {warning}")
        return "\n".join(lines) + "\n"

    lines.append("Stage summary:")
    for stage, stats in (report.get("stages") or {}).items():
        if not stats or stats.get("samples", 0) == 0:
            lines.append(f"- {stage}: no samples")
            continue
        lines.append(
            f"- {stage}: samples={stats['samples']} avg={stats['avg_ms']}ms p95={stats['p95_ms']}ms max={stats['max_ms']}ms"
        )
    lines.append(f"Bottlenecks: {report.get('bottlenecks') or {}}")
    lines.append(f"Decision sources: {report.get('decision_sources') or {}}")
    lines.append(f"Cache layers: {report.get('cache_layers') or {}}")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = _parse_args()
    log_paths = _candidate_log_paths(args.log_dir, args.date, args.limit)
    report = _build_report(_load_profile_rows(log_paths), log_paths)
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(_KST).strftime("%Y%m%d")
    json_path = ANALYSIS_DIR / f"runtime_latency_report_{stamp}.json"
    txt_path = ANALYSIS_DIR / f"runtime_latency_report_{stamp}.txt"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    txt_path.write_text(_render_report(report), encoding="utf-8")
    print(txt_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
