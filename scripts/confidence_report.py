#!/usr/bin/env python3
"""Standalone confidence-distribution report for Kindshot decision logs."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent

_BANDS: list[tuple[str, int, int | None]] = [
    ("<50", 0, 49),
    ("50-59", 50, 59),
    ("60-69", 60, 69),
    ("70-79", 70, 79),
    ("80-89", 80, 89),
    ("90+", 90, None),
]


def resolve_log_paths(*, dates: list[str], log_files: list[str]) -> list[Path]:
    paths: list[Path] = []
    for log_file in log_files:
        paths.append(Path(log_file).expanduser().resolve())
    for date_str in dates:
        candidates = [
            PROJECT_ROOT / "logs" / f"kindshot_{date_str}.jsonl",
            Path("/opt/kindshot/logs") / f"kindshot_{date_str}.jsonl",
        ]
        chosen = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
        paths.append(chosen)
    if not paths:
        raise ValueError("provide at least one --date or --log-file")
    return paths


def _load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _band_for_confidence(confidence: int) -> str:
    for label, low, high in _BANDS:
        if confidence >= low and (high is None or confidence <= high):
            return label
    return "unknown"


def _collapse_flag(mode_share: float | None) -> str:
    if mode_share is None:
        return "no-data"
    if mode_share >= 0.80:
        return "collapsed"
    if mode_share >= 0.60:
        return "clustered"
    return "spread"


def summarize_decision_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    confidences: list[int] = []
    exact_counts: Counter[int] = Counter()
    source_counts: Counter[str] = Counter()
    action_counts: Counter[str] = Counter()
    band_counts: Counter[str] = Counter()
    action_band_counts: dict[str, Counter[str]] = defaultdict(Counter)

    for row in rows:
        confidence = row.get("confidence")
        if not isinstance(confidence, (int, float)):
            continue
        confidence = int(confidence)
        source = str(row.get("decision_source", "unknown"))
        action = str(row.get("action", "unknown"))
        band = _band_for_confidence(confidence)

        confidences.append(confidence)
        exact_counts[confidence] += 1
        source_counts[source] += 1
        action_counts[action] += 1
        band_counts[band] += 1
        action_band_counts[action][band] += 1

    total = len(confidences)
    mode_conf = None
    mode_count = 0
    if exact_counts:
        mode_conf, mode_count = exact_counts.most_common(1)[0]

    return {
        "decision_count": total,
        "source_counts": dict(source_counts),
        "action_counts": dict(action_counts),
        "exact_counts": dict(exact_counts),
        "top_exact": exact_counts.most_common(5),
        "band_counts": {label: band_counts.get(label, 0) for label, _low, _high in _BANDS},
        "action_band_counts": {
            action: {label: counts.get(label, 0) for label, _low, _high in _BANDS}
            for action, counts in sorted(action_band_counts.items())
        },
        "mode_confidence": mode_conf,
        "mode_count": mode_count,
        "mode_share": (mode_count / total) if total else None,
        "collapse_flag": _collapse_flag((mode_count / total) if total else None),
        "min_confidence": min(confidences) if confidences else None,
        "median_confidence": median(confidences) if confidences else None,
        "max_confidence": max(confidences) if confidences else None,
    }


def analyze_log(path: Path) -> dict[str, Any]:
    records = _load_records(path)
    decisions = [row for row in records if row.get("type") == "decision"]
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in decisions:
        by_source[str(row.get("decision_source", "unknown"))].append(row)

    return {
        "path": str(path),
        "exists": path.exists(),
        "line_count": len(records),
        "decision_rows": len(decisions),
        "overall": summarize_decision_rows(decisions),
        "by_source": {source: summarize_decision_rows(rows) for source, rows in sorted(by_source.items())},
    }


def comparison_rows(cohorts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for cohort in cohorts:
        overall = cohort["overall"]
        llm = cohort["by_source"].get("LLM")
        rows.append(
            {
                "label": Path(cohort["path"]).name,
                "decision_count": overall["decision_count"],
                "llm_count": llm["decision_count"] if llm else 0,
                "llm_mode_confidence": llm["mode_confidence"] if llm else None,
                "llm_mode_share": llm["mode_share"] if llm else None,
                "llm_collapse_flag": llm["collapse_flag"] if llm else "no-llm",
                "overall_median": overall["median_confidence"],
            }
        )
    return rows


def render_report(cohorts: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    w = lines.append

    w("=== Confidence Distribution Report ===")
    w("")
    for cohort in cohorts:
        w(f"Cohort: {Path(cohort['path']).name}")
        w(f"  path: {cohort['path']}")
        w(f"  exists: {cohort['exists']}")
        if not cohort["exists"]:
            w("")
            continue
        w(f"  line_count: {cohort['line_count']}")
        w(f"  decision_rows: {cohort['decision_rows']}")

        overall = cohort["overall"]
        w(
            "  overall: "
            f"median={overall['median_confidence']} "
            f"min={overall['min_confidence']} "
            f"max={overall['max_confidence']} "
            f"mode={overall['mode_confidence']} "
            f"mode_share={overall['mode_share']:.1%}" if overall["mode_share"] is not None else "  overall: no-data"
        )
        if overall["mode_share"] is not None:
            w(f"  collapse_flag: {overall['collapse_flag']}")

        if overall["source_counts"]:
            src_txt = " ".join(f"{source}={count}" for source, count in sorted(overall["source_counts"].items()))
            w(f"  source_split: {src_txt}")

        if overall["top_exact"]:
            top_txt = ", ".join(f"{value}:{count}" for value, count in overall["top_exact"])
            w(f"  top_exact: {top_txt}")

        band_txt = " ".join(f"{label}={overall['band_counts'][label]}" for label, _low, _high in _BANDS)
        w(f"  bands: {band_txt}")

        for action, counts in overall["action_band_counts"].items():
            action_txt = " ".join(f"{label}={counts[label]}" for label, _low, _high in _BANDS if counts[label] > 0)
            w(f"  action_bands[{action}]: {action_txt or 'none'}")

        for source, summary in cohort["by_source"].items():
            if summary["decision_count"] == 0:
                continue
            mode_share = summary["mode_share"]
            w(
                f"  source[{source}]: "
                f"n={summary['decision_count']} "
                f"mode={summary['mode_confidence']} "
                f"mode_share={mode_share:.1%} "
                f"flag={summary['collapse_flag']}"
            )
        w("")

    if len(cohorts) > 1:
        w("Comparison:")
        for row in comparison_rows(cohorts):
            mode_share = "-" if row["llm_mode_share"] is None else f"{row['llm_mode_share']:.1%}"
            w(
                f"  {row['label']}: decisions={row['decision_count']} "
                f"llm={row['llm_count']} "
                f"llm_mode={row['llm_mode_confidence']} "
                f"llm_mode_share={mode_share} "
                f"llm_flag={row['llm_collapse_flag']} "
                f"overall_median={row['overall_median']}"
            )

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", action="append", default=[], help="Date in YYYYMMDD format. Repeatable.")
    parser.add_argument("--log-file", action="append", default=[], help="Explicit JSONL log path. Repeatable.")
    args = parser.parse_args()

    try:
        paths = resolve_log_paths(dates=args.date, log_files=args.log_file)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc

    print(render_report([analyze_log(path) for path in paths]))


if __name__ == "__main__":
    main()
