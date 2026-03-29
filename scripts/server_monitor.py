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
from urllib.error import URLError
from urllib.request import urlopen


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


def _run_text_cmd(cmd: list[str]) -> str | None:
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
    if proc.returncode != 0:
        return None
    text = proc.stdout.strip()
    return text or None


def _run_text_cmd_with_sudo(cmd: list[str]) -> str | None:
    return _run_text_cmd(cmd) or _run_text_cmd(["sudo", *cmd])


def summarize_service(name: str) -> dict[str, Any]:
    active_state = _run_text_cmd_with_sudo(["systemctl", "is-active", name]) or "unknown"
    show_text = _run_text_cmd_with_sudo(
        [
            "systemctl",
            "show",
            name,
            "-p",
            "MainPID",
            "-p",
            "SubState",
            "-p",
            "ActiveEnterTimestamp",
        ]
    )
    fields: dict[str, str] = {}
    if show_text:
        for line in show_text.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            fields[key] = value

    pid_text = fields.get("MainPID", "0") or "0"
    try:
        main_pid = int(pid_text)
    except ValueError:
        main_pid = 0

    cmdline = ""
    if main_pid > 0:
        cmdline = _run_text_cmd_with_sudo(["ps", "-p", str(main_pid), "-o", "args="]) or ""

    mode = ""
    if "--paper" in cmdline:
        mode = "paper"
    elif "--dry-run" in cmdline:
        mode = "dry_run"
    elif "kindshot" in cmdline:
        mode = "live"

    return {
        "name": name,
        "active_state": active_state,
        "sub_state": fields.get("SubState", ""),
        "active_enter_timestamp": fields.get("ActiveEnterTimestamp", ""),
        "main_pid": main_pid,
        "cmdline": cmdline,
        "mode": mode,
    }


def _load_health_json(url: str) -> dict[str, Any] | None:
    try:
        with urlopen(url, timeout=2) as resp:
            return json.load(resp)
    except (OSError, TimeoutError, ValueError, URLError):
        return None


def summarize_health(payload: dict[str, Any] | None, *, url: str) -> dict[str, Any]:
    if payload is None:
        return {"reachable": False, "url": url}

    guardrail = payload.get("guardrail_state", {})
    circuit = payload.get("circuit_breaker", {})
    return {
        "reachable": True,
        "url": url,
        "status": payload.get("status", "unknown"),
        "last_poll_age_seconds": payload.get("last_poll_age_seconds"),
        "events_seen": payload.get("events_seen", 0),
        "events_processed": payload.get("events_processed", 0),
        "buy_count": payload.get("buy_count", 0),
        "skip_count": payload.get("skip_count", 0),
        "error_count": payload.get("error_count", 0),
        "llm_calls": payload.get("llm_calls", 0),
        "kis_calls": payload.get("kis_calls", 0),
        "position_count": guardrail.get("position_count", 0),
        "configured_max_positions": guardrail.get("configured_max_positions", 0),
        "nvidia_open": circuit.get("nvidia_open", False),
        "anthropic_open": circuit.get("anthropic_open", False),
    }


def _journal_text(date_str: str) -> str:
    start, end = _today_range(date_str)
    base = ["journalctl", "-u", "kindshot", "--since", start, "--until", end, "--no-pager"]
    return _run_journal_cmd(base) or _run_journal_cmd(["sudo", *base]) or ""


def build_summary(date_str: str, *, log_dir: Path, health_url: str = "http://127.0.0.1:8080/health") -> dict[str, Any]:
    runtime_log = log_dir / f"kindshot_{date_str}.jsonl"
    polling_trace = log_dir / f"polling_trace_{date_str}.jsonl"
    runtime = summarize_runtime_log(runtime_log)
    polling = summarize_poll_trace(polling_trace)
    journal = summarize_journal_text(_journal_text(date_str))
    services = {
        "kindshot": summarize_service("kindshot"),
        "kindshot-dashboard": summarize_service("kindshot-dashboard"),
    }
    health = summarize_health(_load_health_json(health_url), url=health_url)
    return {
        "date": date_str,
        "services": services,
        "health": health,
        "runtime": runtime,
        "polling": polling,
        "journal": journal,
    }


def summarize_verdict(summary: dict[str, Any]) -> str:
    services = summary.get("services", {})
    health = summary.get("health", {})
    runtime = summary["runtime"]
    polling = summary["polling"]
    journal = summary["journal"]

    kindshot = services.get("kindshot", {})
    if kindshot.get("active_state") not in {"", "active", "unknown"}:
        return f"kindshot service not ready ({kindshot.get('active_state')})"
    if health.get("reachable") and health.get("status") not in {"healthy", "ok"}:
        return f"health degraded ({health.get('status')})"

    decisions = int(runtime.get("record_types", {}).get("decision", 0) or 0)
    if runtime.get("exists") and decisions > 0:
        return "structured runtime active"
    if runtime.get("exists"):
        return "runtime log exists but no decisions yet"
    if int(polling.get("positive_poll_count", 0) or 0) > 0:
        return "service alive, polling active, no structured runtime log yet"
    if journal.get("latest_heartbeat"):
        return "service alive but runtime log not started yet"
    return "no runtime evidence available"


def render_summary(summary: dict[str, Any]) -> str:
    services = summary.get("services", {})
    health = summary.get("health", {})
    runtime = summary["runtime"]
    polling = summary["polling"]
    journal = summary["journal"]
    lines: list[str] = []
    w = lines.append

    w(f"=== Kindshot Server Monitor: {summary['date']} ===")
    w("")
    w("Services:")
    kindshot = services.get("kindshot")
    if kindshot:
        svc_line = (
            f"  kindshot: {kindshot.get('active_state', 'unknown')}"
            f" sub={kindshot.get('sub_state', '-') or '-'}"
        )
        if kindshot.get("mode"):
            svc_line += f" mode={kindshot['mode']}"
        if kindshot.get("main_pid", 0):
            svc_line += f" pid={kindshot['main_pid']}"
        w(svc_line)
        if kindshot.get("active_enter_timestamp"):
            w(f"  kindshot_since: {kindshot['active_enter_timestamp']}")
    dashboard = services.get("kindshot-dashboard")
    if dashboard:
        dash_line = (
            f"  dashboard: {dashboard.get('active_state', 'unknown')}"
            f" sub={dashboard.get('sub_state', '-') or '-'}"
        )
        if dashboard.get("main_pid", 0):
            dash_line += f" pid={dashboard['main_pid']}"
        w(dash_line)

    w("")
    w("Health:")
    if health.get("reachable"):
        w(
            "  "
            f"status={health.get('status', 'unknown')} "
            f"last_poll_age_s={health.get('last_poll_age_seconds', '?')} "
            f"events_seen={health.get('events_seen', 0)} "
            f"errors={health.get('error_count', 0)} "
            f"llm_calls={health.get('llm_calls', 0)} "
            f"kis_calls={health.get('kis_calls', 0)}"
        )
        w(
            "  "
            f"positions={health.get('position_count', 0)}/{health.get('configured_max_positions', 0)} "
            f"buys={health.get('buy_count', 0)} "
            f"skips={health.get('skip_count', 0)} "
            f"circuit_breaker=nvidia:{health.get('nvidia_open', False)} "
            f"anthropic:{health.get('anthropic_open', False)}"
        )
    else:
        w(f"  unreachable ({health.get('url', 'http://127.0.0.1:8080/health')})")

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
