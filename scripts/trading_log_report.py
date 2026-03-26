#!/usr/bin/env python3
"""Standalone trading-log analysis report for a single Kindshot JSONL file."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from kindshot.tz import KST as _KST


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


def resolve_log_path(*, date_str: str | None, log_file: str | None) -> Path:
    if log_file:
        return Path(log_file).expanduser().resolve()
    if not date_str:
        raise ValueError("either --date or --log-file is required")

    candidates = [
        PROJECT_ROOT / "logs" / f"kindshot_{date_str}.jsonl",
        Path("/opt/kindshot/logs") / f"kindshot_{date_str}.jsonl",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _parse_hour(ts: str) -> str | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_KST)
        else:
            dt = dt.astimezone(_KST)
        return f"{dt.hour:02d}"
    except ValueError:
        return None


def analyze_log(path: Path) -> dict[str, Any]:
    records = _load_records(path)
    record_types: Counter[str] = Counter()
    decision_actions: Counter[str] = Counter()
    decision_sources: Counter[str] = Counter()
    inline_actions: Counter[str] = Counter()
    buy_guardrails: Counter[str] = Counter()
    hour_source: dict[str, Counter[str]] = defaultdict(Counter)
    source_reasons: dict[str, Counter[str]] = defaultdict(Counter)

    for rec in records:
        rtype = str(rec.get("type", rec.get("record_type", "unknown")))
        record_types[rtype] += 1

        if rtype == "decision":
            source = str(rec.get("decision_source", "unknown"))
            action = str(rec.get("action", "unknown"))
            decision_actions[action] += 1
            decision_sources[source] += 1
            reason = str(rec.get("reason", "")).strip()
            if reason:
                source_reasons[source][reason] += 1

            hour = _parse_hour(str(rec.get("decided_at", "") or rec.get("detected_at", "")))
            if hour is not None:
                hour_source[hour][source] += 1

        elif rtype == "event":
            action = str(rec.get("decision_action", ""))
            if action in {"BUY", "SKIP"}:
                inline_actions[action] += 1
                if action == "BUY":
                    guardrail = (
                        rec.get("guardrail_reason")
                        or ("PASSED" if rec.get("guardrail_passed") else None)
                        or rec.get("skip_reason")
                        or "UNKNOWN"
                    )
                    buy_guardrails[str(guardrail)] += 1

    stat = path.stat() if path.exists() else None
    return {
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": stat.st_size if stat else 0,
        "line_count": len(records),
        "record_types": dict(record_types),
        "decision_actions": dict(decision_actions),
        "decision_sources": dict(decision_sources),
        "inline_actions": dict(inline_actions),
        "buy_guardrails": dict(buy_guardrails),
        "hour_source": {hour: dict(counter) for hour, counter in sorted(hour_source.items())},
        "source_reasons": {
            source: dict(counter.most_common(5))
            for source, counter in sorted(source_reasons.items())
        },
    }


def summarize_verdict(summary: dict[str, Any]) -> str:
    buys = int(summary["decision_actions"].get("BUY", 0))
    skips = int(summary["decision_actions"].get("SKIP", 0))
    total = buys + skips
    if total == 0:
        return "no structured decisions"
    if buys == 0:
        return "fully defensive"
    if buys > skips:
        return "buy-heavy"
    return "mixed"


def render_report(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    w = lines.append

    w("=== Trading Log Report ===")
    w(f"path: {summary['path']}")
    w(f"exists: {summary['exists']}")
    if not summary["exists"]:
        return "\n".join(lines)

    w(f"size_bytes: {summary['size_bytes']}")
    w(f"line_count: {summary['line_count']}")
    rt = summary["record_types"]
    w(
        "record_types: "
        f"event={rt.get('event', 0)} decision={rt.get('decision', 0)} price_snapshot={rt.get('price_snapshot', 0)}"
    )
    w("")

    w("Structured Decisions:")
    da = summary["decision_actions"]
    ds = summary["decision_sources"]
    w(f"  BUY={da.get('BUY', 0)} SKIP={da.get('SKIP', 0)} total={sum(da.values())}")
    if ds:
        w("  decision_sources:")
        for source, count in sorted(ds.items()):
            w(f"    - {source}: {count}")
    w("")

    ia = summary["inline_actions"]
    bg = summary["buy_guardrails"]
    w("Inline Intent:")
    w(f"  BUY={ia.get('BUY', 0)} SKIP={ia.get('SKIP', 0)} total={sum(ia.values())}")
    if bg:
        w("  BUY guardrails:")
        for reason, count in sorted(bg.items(), key=lambda item: (-item[1], item[0])):
            w(f"    - {reason}: {count}")
    w("")

    hs = summary["hour_source"]
    if hs:
        w("Time Of Day:")
        for hour, sources in hs.items():
            detail = " ".join(f"{source}={count}" for source, count in sorted(sources.items()))
            w(f"  {hour}: {detail}")
        w("")

    sr = summary["source_reasons"]
    if sr:
        w("Top Reasons:")
        for source, reasons in sr.items():
            w(f"  {source}:")
            for reason, count in reasons.items():
                w(f"    - {count}x {reason}")
        w("")

    w(f"Bottom Line: {summarize_verdict(summary)}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Date in YYYYMMDD format")
    parser.add_argument("--log-file", help="Explicit JSONL log file path")
    args = parser.parse_args()

    try:
        path = resolve_log_path(date_str=args.date, log_file=args.log_file)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc

    print(render_report(analyze_log(path)))


if __name__ == "__main__":
    main()
