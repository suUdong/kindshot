#!/usr/bin/env python3
"""Operator-facing server monitor summary for Kindshot."""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


def _file_meta(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path)}
    stat = path.stat()
    return {
        "exists": True,
        "path": str(path),
        "size_bytes": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
    }


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
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


def summarize_runtime_log(path: Path) -> dict[str, Any]:
    summary = _file_meta(path)
    if not summary["exists"]:
        return summary

    records = _load_jsonl(path)
    record_types: Counter[str] = Counter()
    decision_actions: Counter[str] = Counter()
    decision_sources: Counter[str] = Counter()

    for rec in records:
        rtype = rec.get("type", rec.get("record_type", "unknown"))
        record_types[rtype] += 1
        if rtype == "decision":
            decision_actions[str(rec.get("action", "unknown"))] += 1
            decision_sources[str(rec.get("decision_source", "unknown"))] += 1

    summary.update(
        {
            "line_count": len(records),
            "record_types": dict(record_types),
            "decision_actions": dict(decision_actions),
            "decision_sources": dict(decision_sources),
        }
    )
    return summary


def summarize_poll_trace(path: Path) -> dict[str, Any]:
    summary = _file_meta(path)
    if not summary["exists"]:
        return summary

    records = _load_jsonl(path)
    poll_end = [rec for rec in records if rec.get("phase") == "poll_end"]
    positive = [rec for rec in poll_end if int(rec.get("items", 0) or 0) > 0]

    summary.update(
        {
            "poll_end_count": len(poll_end),
            "items_total": sum(int(rec.get("items", 0) or 0) for rec in poll_end),
            "positive_poll_count": len(positive),
            "last_poll_end_ts": poll_end[-1].get("ts") if poll_end else "",
            "latest_positive_poll": positive[-1] if positive else None,
        }
    )
    return summary


def summarize_journal_text(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    return {
        "line_count": len(lines),
        "nvidia_200": sum(
            1
            for line in lines
            if "POST https://integrate.api.nvidia.com/v1/chat/completions" in line and " 200 OK" in line
        ),
        "service_starts": sum(1 for line in lines if "Started kindshot KRX news-driven trading MVP." in line),
        "timeout_failures": sum(1 for line in lines if "Failed with result 'timeout'" in line),
        "latest_heartbeat": next((line for line in reversed(lines) if "Heartbeat:" in line), ""),
    }


def _today_range(date_str: str) -> tuple[str, str]:
    yyyy = date_str[:4]
    mm = date_str[4:6]
    dd = date_str[6:8]
    return f"{yyyy}-{mm}-{dd} 00:00", f"{yyyy}-{mm}-{dd} 23:59:59"


def _run_journal_cmd(cmd: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        )
    except OSError:
        return None
    if proc.returncode == 0 and proc.stdout:
        return proc.stdout
    return None


def _journal_text(date_str: str) -> str:
    start, end = _today_range(date_str)
    base = ["journalctl", "-u", "kindshot", "--since", start, "--until", end, "--no-pager"]
    return _run_journal_cmd(base) or _run_journal_cmd(["sudo", *base]) or ""


def build_summary(date_str: str, *, log_dir: Path) -> dict[str, Any]:
    runtime_log = log_dir / f"kindshot_{date_str}.jsonl"
    polling_trace = log_dir / f"polling_trace_{date_str}.jsonl"
    runtime = summarize_runtime_log(runtime_log)
    polling = summarize_poll_trace(polling_trace)
    journal = summarize_journal_text(_journal_text(date_str))
    return {
        "date": date_str,
        "runtime": runtime,
        "polling": polling,
        "journal": journal,
    }


def summarize_verdict(summary: dict[str, Any]) -> str:
    runtime = summary["runtime"]
    polling = summary["polling"]
    journal = summary["journal"]

    decisions = int(runtime.get("record_types", {}).get("decision", 0) or 0)
    if runtime.get("exists") and decisions > 0:
        return "structured runtime active"
    if runtime.get("exists"):
        return "runtime log exists but no decisions yet"
    if int(polling.get("positive_poll_count", 0) or 0) > 0:
        return "polling active but no structured runtime log yet"
    if journal.get("latest_heartbeat"):
        return "service alive but runtime log not started yet"
    return "no runtime evidence available"


def render_summary(summary: dict[str, Any]) -> str:
    runtime = summary["runtime"]
    polling = summary["polling"]
    journal = summary["journal"]
    lines: list[str] = []
    w = lines.append

    w(f"=== Kindshot Server Monitor: {summary['date']} ===")
    w("")
    w("Current Files:")
    if runtime["exists"]:
        w(f"  runtime_log: present ({runtime['size_bytes']} bytes, mtime={runtime['mtime']})")
        rt = runtime.get("record_types", {})
        w(
            "  structured_counts: "
            f"event={rt.get('event', 0)} decision={rt.get('decision', 0)} price_snapshot={rt.get('price_snapshot', 0)}"
        )
        actions = runtime.get("decision_actions", {})
        sources = runtime.get("decision_sources", {})
        w(f"  decisions: BUY={actions.get('BUY', 0)} SKIP={actions.get('SKIP', 0)}")
        if sources:
            source_txt = " ".join(f"{k}={v}" for k, v in sorted(sources.items()))
            w(f"  decision_sources: {source_txt}")
    else:
        w(f"  runtime_log: missing ({runtime['path']})")

    if polling["exists"]:
        w(f"  polling_trace: present ({polling['size_bytes']} bytes, mtime={polling['mtime']})")
    else:
        w(f"  polling_trace: missing ({polling['path']})")

    w("")
    w("Polling Trace:")
    w(
        "  "
        f"poll_end={polling.get('poll_end_count', 0)} "
        f"raw_items={polling.get('items_total', 0)} "
        f"positive_polls={polling.get('positive_poll_count', 0)}"
    )
    if polling.get("last_poll_end_ts"):
        w(f"  last_poll_end: {polling['last_poll_end_ts']}")
    latest_positive = polling.get("latest_positive_poll")
    if latest_positive:
        w(
            "  latest_positive_poll: "
            f"ts={latest_positive.get('ts', '')} "
            f"items={latest_positive.get('items', 0)} "
            f"raw={latest_positive.get('raw', '?')} "
            f"last_time_after={latest_positive.get('last_time_after', '')}"
        )

    w("")
    w("Journal:")
    w(
        "  "
        f"nvidia_200={journal['nvidia_200']} "
        f"service_starts={journal['service_starts']} "
        f"timeout_failures={journal['timeout_failures']} "
        f"lines={journal['line_count']}"
    )
    if journal["latest_heartbeat"]:
        w(f"  latest_heartbeat: {journal['latest_heartbeat']}")

    w("")
    w(f"Verdict: {summarize_verdict(summary)}")
    return "\n".join(lines)


def main() -> None:
    if len(sys.argv) > 1:
        date_str = sys.argv[1]
    else:
        date_str = datetime.now().strftime("%Y%m%d")

    log_dir = Path("/opt/kindshot/logs")
    if not log_dir.exists():
        log_dir = Path("logs")

    print(render_summary(build_summary(date_str, log_dir=log_dir)))


if __name__ == "__main__":
    main()
