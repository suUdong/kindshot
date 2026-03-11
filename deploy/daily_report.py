#!/usr/bin/env python3
"""장 마감 후 daily report — 이벤트별 티커 + 시간대별 수익률 요약.

사용법:
    python deploy/daily_report.py              # 오늘
    python deploy/daily_report.py 20260311     # 특정 날짜
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path


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


def generate_report(log_path: Path) -> None:
    if not log_path.exists():
        print(f"로그 파일 없음: {log_path}")
        return

    records = _load_records(log_path)

    # 이벤트 수집 (skip_stage=DUPLICATE 제외)
    events: dict[str, dict] = {}  # event_id → event record
    decisions: dict[str, dict] = {}  # event_id → decision record
    snapshots: dict[str, dict[str, dict]] = defaultdict(dict)  # event_id → horizon → snapshot

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

    # 액션 가능 이벤트 (POS_STRONG + quant pass + decision 있음)
    actionable = {
        eid: ev for eid, ev in events.items()
        if ev.get("bucket") == "POS_STRONG"
        and ev.get("quant_check_passed") is True
    }

    # 가격 추적 대상 (NEG_STRONG도 포함)
    tracked = {
        eid: ev for eid, ev in events.items()
        if eid in snapshots
    }

    # ── 전체 요약 ──
    bucket_counts = defaultdict(int)
    for ev in events.values():
        bucket_counts[ev.get("bucket", "?")] += 1

    print(f"{'=' * 72}")
    print(f"  DAILY REPORT: {log_path.stem}")
    print(f"{'=' * 72}")
    print(f"  총 이벤트: {len(events)}건")
    print(f"  버킷: ", end="")
    parts = []
    for b in ["POS_STRONG", "POS_WEAK", "NEG_STRONG", "NEG_WEAK", "UNKNOWN"]:
        if bucket_counts[b]:
            parts.append(f"{b}={bucket_counts[b]}")
    print(" | ".join(parts))
    print(f"  LLM 판단: {len(decisions)}건 (BUY={sum(1 for d in decisions.values() if d.get('action')=='BUY')}, SKIP={sum(1 for d in decisions.values() if d.get('action')=='SKIP')})")
    print()

    # ── BUY 판단 성과표 ──
    buy_decisions = {eid: d for eid, d in decisions.items() if d.get("action") == "BUY"}

    if buy_decisions:
        print(f"  {'─' * 70}")
        print(f"  BUY 판단 성과")
        print(f"  {'─' * 70}")
        _HORIZONS = ["t0", "t+1m", "t+5m", "t+30m", "close"]
        header = f"  {'티커':<8} {'헤드라인':<28} {'conf':>4} {'t0':>8} {'t+1m':>7} {'t+5m':>7} {'t+30m':>7} {'close':>7}"
        print(header)
        print(f"  {'─' * 70}")

        close_rets = []

        for eid, dec in sorted(buy_decisions.items(), key=lambda x: x[1].get("decided_at", "")):
            ev = events.get(eid, {})
            ticker = ev.get("ticker", "?")
            headline = ev.get("headline", "")[:26]
            conf = dec.get("confidence", "?")
            snaps = snapshots.get(eid, {})

            t0_px = snaps.get("t0", {}).get("px")
            cols = [f"{t0_px:>8,.0f}" if t0_px else f"{'N/A':>8}"]

            for h in _HORIZONS[1:]:
                ret = snaps.get(h, {}).get("ret_long_vs_t0")
                if ret is not None:
                    pct = ret * 100
                    cols.append(f"{pct:>+6.2f}%")
                    if h == "close":
                        close_rets.append(pct)
                else:
                    cols.append(f"{'N/A':>7}")

            print(f"  {ticker:<8} {headline:<28} {conf:>4} {' '.join(cols)}")

        if close_rets:
            print(f"  {'─' * 70}")
            wins = [r for r in close_rets if r > 0]
            avg = sum(close_rets) / len(close_rets)
            print(f"  승률: {len(wins)}/{len(close_rets)} ({len(wins)/len(close_rets)*100:.0f}%)  평균: {avg:+.2f}%  최고: {max(close_rets):+.2f}%  최저: {min(close_rets):+.2f}%")
        print()

    # ── NEG_STRONG (SHORT_WATCH) 추적 ──
    neg_tracked = {
        eid: ev for eid, ev in tracked.items()
        if ev.get("bucket") == "NEG_STRONG" and eid in snapshots
    }

    if neg_tracked:
        print(f"  {'─' * 70}")
        print(f"  NEG_STRONG (SHORT_WATCH) 추적")
        print(f"  {'─' * 70}")
        header = f"  {'티커':<8} {'헤드라인':<28} {'t0':>8} {'t+1m':>7} {'t+5m':>7} {'t+30m':>7} {'close':>7}"
        print(header)
        print(f"  {'─' * 70}")

        for eid, ev in sorted(neg_tracked.items(), key=lambda x: x[1].get("detected_at", "")):
            ticker = ev.get("ticker", "?")
            headline = ev.get("headline", "")[:26]
            snaps = snapshots.get(eid, {})

            t0_px = snaps.get("t0", {}).get("px")
            cols = [f"{t0_px:>8,.0f}" if t0_px else f"{'N/A':>8}"]

            for h in ["t+1m", "t+5m", "t+30m", "close"]:
                # NEG_STRONG은 short 관점이므로 ret_short 표시
                ret = snaps.get(h, {}).get("ret_short_vs_t0")
                if ret is not None:
                    cols.append(f"{ret * 100:>+6.2f}%")
                else:
                    cols.append(f"{'N/A':>7}")

            print(f"  {ticker:<8} {headline:<28} {' '.join(cols)}")
        print()

    # ── 시간대별 이벤트 분포 ──
    hour_dist = defaultdict(int)
    for ev in events.values():
        detected = ev.get("detected_at", "")
        if detected:
            try:
                dt = datetime.fromisoformat(detected)
                hour_dist[dt.hour] += 1
            except (ValueError, TypeError):
                pass

    if hour_dist:
        print(f"  {'─' * 70}")
        print(f"  시간대별 이벤트 분포")
        print(f"  {'─' * 70}")
        for h in sorted(hour_dist):
            bar = "#" * min(hour_dist[h], 50)
            print(f"  {h:02d}시  {bar} {hour_dist[h]}")
        print()

    print(f"{'=' * 72}")


def main() -> None:
    log_dir = Path("/opt/kindshot/logs")
    if not log_dir.exists():
        log_dir = Path("logs")

    if len(sys.argv) > 1:
        date_str = sys.argv[1]
    else:
        date_str = datetime.now().strftime("%Y%m%d")

    log_path = log_dir / f"kindshot_{date_str}.jsonl"
    generate_report(log_path)


if __name__ == "__main__":
    main()
