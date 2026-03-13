#!/usr/bin/env python3
"""장 마감 후 daily report — 이벤트별 티커 + 시간대별 수익률 요약.

사용법:
    python deploy/daily_report.py              # 오늘 (txt)
    python deploy/daily_report.py 20260311     # 특정 날짜 (txt)
    python deploy/daily_report.py --telegram   # 오늘 (텔레그램 전송)
    python deploy/daily_report.py --telegram 20260311
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen
from urllib.parse import quote


# ── 데이터 수집 ──

def _load_records(log_path: Path) -> list[dict]:
    records = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _collect(log_path: Path) -> dict:
    """로그 파일에서 이벤트/결정/스냅샷 수집."""
    records = _load_records(log_path)

    events: dict[str, dict] = {}
    decisions: dict[str, dict] = {}
    snapshots: dict[str, dict[str, dict]] = defaultdict(dict)

    for rec in records:
        rtype = rec.get("type")
        eid = rec.get("event_id", "")

        if rtype == "event":
            if rec.get("skip_reason") == "DUPLICATE":
                continue
            events[eid] = rec
        elif rtype == "decision":
            decisions[eid] = rec
        elif rtype == "price_snapshot":
            horizon = rec.get("horizon", "")
            if eid and horizon:
                snapshots[eid][horizon] = rec

    bucket_counts = defaultdict(int)
    for ev in events.values():
        bucket_counts[ev.get("bucket", "?")] += 1

    hour_dist = defaultdict(int)
    for ev in events.values():
        detected = ev.get("detected_at", "")
        if detected:
            try:
                dt = datetime.fromisoformat(detected)
                hour_dist[dt.hour] += 1
            except (ValueError, TypeError):
                pass

    return {
        "events": events,
        "decisions": decisions,
        "snapshots": snapshots,
        "bucket_counts": bucket_counts,
        "hour_dist": hour_dist,
    }


def _ret_pct(snaps: dict, horizon: str, key: str = "ret_long_vs_t0") -> Optional[float]:
    ret = snaps.get(horizon, {}).get(key)
    if ret is not None:
        return ret * 100
    return None


# ── TXT 포맷 (파일/터미널용) ──

def format_txt(log_path: Path, data: dict) -> str:
    events = data["events"]
    decisions = data["decisions"]
    snapshots = data["snapshots"]
    bucket_counts = data["bucket_counts"]
    hour_dist = data["hour_dist"]

    lines = []
    w = lines.append

    w(f"{'=' * 72}")
    w(f"  DAILY REPORT: {log_path.stem}")
    w(f"{'=' * 72}")
    w(f"  총 이벤트: {len(events)}건")

    parts = []
    for b in ["POS_STRONG", "POS_WEAK", "NEG_STRONG", "NEG_WEAK", "IGNORE", "UNKNOWN"]:
        if bucket_counts[b]:
            parts.append(f"{b}={bucket_counts[b]}")
    w(f"  버킷: {' | '.join(parts)}")

    n_buy = sum(1 for d in decisions.values() if d.get("action") == "BUY")
    n_skip = sum(1 for d in decisions.values() if d.get("action") == "SKIP")
    w(f"  LLM 판단: {len(decisions)}건 (BUY={n_buy}, SKIP={n_skip})")
    w("")

    # BUY 성과
    buy_decisions = {eid: d for eid, d in decisions.items() if d.get("action") == "BUY"}
    if buy_decisions:
        w(f"  {'─' * 70}")
        w(f"  BUY 판단 성과")
        w(f"  {'─' * 70}")
        w(f"  {'티커':<8} {'헤드라인':<28} {'conf':>4} {'t0':>8} {'t+1m':>7} {'t+5m':>7} {'t+30m':>7} {'close':>7}")
        w(f"  {'─' * 70}")

        close_rets = []
        for eid, dec in sorted(buy_decisions.items(), key=lambda x: x[1].get("decided_at", "")):
            ev = events.get(eid, {})
            ticker = ev.get("ticker", "?")
            headline = ev.get("headline", "")[:26]
            conf = dec.get("confidence", "?")
            snaps = snapshots.get(eid, {})

            t0_px = snaps.get("t0", {}).get("px")
            cols = [f"{t0_px:>8,.0f}" if t0_px else f"{'N/A':>8}"]

            for h in ["t+1m", "t+5m", "t+30m", "close"]:
                r = _ret_pct(snaps, h)
                if r is not None:
                    cols.append(f"{r:>+6.2f}%")
                    if h == "close":
                        close_rets.append(r)
                else:
                    cols.append(f"{'N/A':>7}")

            w(f"  {ticker:<8} {headline:<28} {conf:>4} {' '.join(cols)}")

        if close_rets:
            w(f"  {'─' * 70}")
            wins = [r for r in close_rets if r > 0]
            avg = sum(close_rets) / len(close_rets)
            w(f"  승률: {len(wins)}/{len(close_rets)} ({len(wins)/len(close_rets)*100:.0f}%)  평균: {avg:+.2f}%  최고: {max(close_rets):+.2f}%  최저: {min(close_rets):+.2f}%")
        w("")

    # NEG_STRONG
    neg_tracked = {
        eid: ev for eid, ev in events.items()
        if ev.get("bucket") == "NEG_STRONG" and eid in snapshots
    }
    if neg_tracked:
        w(f"  {'─' * 70}")
        w(f"  NEG_STRONG (SHORT_WATCH) 추적")
        w(f"  {'─' * 70}")
        w(f"  {'티커':<8} {'헤드라인':<28} {'t0':>8} {'t+1m':>7} {'t+5m':>7} {'t+30m':>7} {'close':>7}")
        w(f"  {'─' * 70}")

        for eid, ev in sorted(neg_tracked.items(), key=lambda x: x[1].get("detected_at", "")):
            ticker = ev.get("ticker", "?")
            headline = ev.get("headline", "")[:26]
            snaps = snapshots.get(eid, {})

            t0_px = snaps.get("t0", {}).get("px")
            cols = [f"{t0_px:>8,.0f}" if t0_px else f"{'N/A':>8}"]

            for h in ["t+1m", "t+5m", "t+30m", "close"]:
                r = _ret_pct(snaps, h, key="ret_short_vs_t0")
                if r is not None:
                    cols.append(f"{r:>+6.2f}%")
                else:
                    cols.append(f"{'N/A':>7}")

            w(f"  {ticker:<8} {headline:<28} {' '.join(cols)}")
        w("")

    # 시간대별
    if hour_dist:
        w(f"  {'─' * 70}")
        w(f"  시간대별 이벤트 분포")
        w(f"  {'─' * 70}")
        for h in sorted(hour_dist):
            bar = "#" * min(hour_dist[h], 50)
            w(f"  {h:02d}시  {bar} {hour_dist[h]}")
        w("")

    w(f"{'=' * 72}")
    return "\n".join(lines)


# ── 텔레그램 포맷 (HTML) ──

def format_telegram(log_path: Path, data: dict) -> str:
    events = data["events"]
    decisions = data["decisions"]
    snapshots = data["snapshots"]
    bucket_counts = data["bucket_counts"]
    hour_dist = data["hour_dist"]

    lines = []
    w = lines.append

    date_label = log_path.stem.replace("kindshot_", "")
    w(f"<b>Daily Report: {date_label}</b>")
    w("")
    w(f"총 이벤트: {len(events)}건")

    parts = []
    for b in ["POS_STRONG", "POS_WEAK", "NEG_STRONG", "NEG_WEAK", "IGNORE", "UNKNOWN"]:
        if bucket_counts[b]:
            parts.append(f"{b}={bucket_counts[b]}")
    w(" | ".join(parts))

    n_buy = sum(1 for d in decisions.values() if d.get("action") == "BUY")
    n_skip = sum(1 for d in decisions.values() if d.get("action") == "SKIP")
    w(f"LLM 판단: {len(decisions)}건 (BUY={n_buy}, SKIP={n_skip})")

    # BUY 성과
    buy_decisions = {eid: d for eid, d in decisions.items() if d.get("action") == "BUY"}
    if buy_decisions:
        w("")
        w("<b>-- BUY 성과 --</b>")

        close_rets = []
        for eid, dec in sorted(buy_decisions.items(), key=lambda x: x[1].get("decided_at", "")):
            ev = events.get(eid, {})
            ticker = ev.get("ticker", "?")
            headline = ev.get("headline", "")[:30]
            conf = dec.get("confidence", "?")
            snaps = snapshots.get(eid, {})
            t0_px = snaps.get("t0", {}).get("px")

            w(f"<b>{ticker}</b> {headline}")
            entry_str = f"{t0_px:,.0f}" if t0_px else "N/A"
            w(f"  conf={conf} | entry={entry_str}")

            ret_parts = []
            for h in ["t+1m", "t+5m", "t+30m", "close"]:
                r = _ret_pct(snaps, h)
                if r is not None:
                    ret_parts.append(f"{h}: {r:+.2f}%")
                    if h == "close":
                        close_rets.append(r)
                else:
                    ret_parts.append(f"{h}: N/A")
            w(f"  {' / '.join(ret_parts)}")

        if close_rets:
            w("")
            wins = [r for r in close_rets if r > 0]
            avg = sum(close_rets) / len(close_rets)
            w(f"승률: {len(wins)}/{len(close_rets)} ({len(wins)/len(close_rets)*100:.0f}%) | 평균: {avg:+.2f}%")

    # NEG_STRONG
    neg_tracked = {
        eid: ev for eid, ev in events.items()
        if ev.get("bucket") == "NEG_STRONG" and eid in snapshots
    }
    if neg_tracked:
        w("")
        w("<b>-- NEG_STRONG 추적 --</b>")
        for eid, ev in sorted(neg_tracked.items(), key=lambda x: x[1].get("detected_at", "")):
            ticker = ev.get("ticker", "?")
            headline = ev.get("headline", "")[:30]
            snaps = snapshots.get(eid, {})

            w(f"<b>{ticker}</b> {headline}")
            ret_parts = []
            for h in ["t+1m", "t+5m", "t+30m", "close"]:
                r = _ret_pct(snaps, h, key="ret_short_vs_t0")
                if r is not None:
                    ret_parts.append(f"{h}: {r:+.2f}%")
                else:
                    ret_parts.append(f"{h}: N/A")
            w(f"  {' / '.join(ret_parts)}")

    # 시간대별 (상위 5개만)
    if hour_dist:
        w("")
        w("<b>-- 시간대 분포 (상위) --</b>")
        for h, cnt in sorted(hour_dist.items(), key=lambda x: -x[1])[:5]:
            w(f"  {h:02d}시: {cnt}건")

    return "\n".join(lines)


# ── 텔레그램 전송 ──

def send_telegram(text: str, bot_token: str, chat_id: str) -> bool:
    """텔레그램 Bot API로 메시지 전송. 외부 의존성 없음."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")

    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                print(f"텔레그램 전송 완료 (chat_id={chat_id})")
                return True
            else:
                print(f"텔레그램 API 에러: {result}")
                return False
    except Exception as e:
        print(f"텔레그램 전송 실패: {e}")
        return False


# ── CLI ──

def main() -> None:
    log_dir = Path("/opt/kindshot/logs")
    if not log_dir.exists():
        log_dir = Path("logs")

    telegram_mode = "--telegram" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if args:
        date_str = args[0]
    else:
        date_str = datetime.now().strftime("%Y%m%d")

    log_path = log_dir / f"kindshot_{date_str}.jsonl"

    if not log_path.exists():
        print(f"로그 파일 없음: {log_path}")
        return

    data = _collect(log_path)

    if telegram_mode:
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not bot_token or not chat_id:
            print("TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 환경변수 필요")
            sys.exit(1)

        text = format_telegram(log_path, data)
        send_telegram(text, bot_token, chat_id)
    else:
        print(format_txt(log_path, data))


if __name__ == "__main__":
    main()
