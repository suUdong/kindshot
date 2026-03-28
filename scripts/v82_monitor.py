#!/usr/bin/env python3
"""v82 월요일 장 모니터링 — 차단률, 시그널 수, 매도 실행 로그 추적.

사용법:
    python scripts/v82_monitor.py              # 1회 출력
    python scripts/v82_monitor.py --watch      # 30초마다 갱신
    python scripts/v82_monitor.py --watch 15   # 15초마다 갱신
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def _load_today_records(log_dir: Path, date_str: str | None = None) -> list[dict]:
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")
    log_path = log_dir / f"kindshot_{date_str}.jsonl"
    if not log_path.exists():
        return []
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


def _analyze(records: list[dict]) -> dict:
    """v82 핵심 지표 분석."""
    events = [r for r in records if r.get("type") == "event"]
    decisions = [r for r in records if r.get("type") == "decision"]
    exits = [r for r in records if r.get("type") == "exit"]

    total_events = len(events)
    skipped = [e for e in events if e.get("skip_stage")]
    passed = [e for e in events if not e.get("skip_stage")]

    # 차단 사유 분류
    skip_reasons = Counter(e.get("skip_reason", "UNKNOWN") for e in skipped)

    # v82 신규 차단 추적
    article_blocks = [e for e in skipped if "article_pattern" in (e.get("skip_reason") or "").lower()]
    foreign_cap_blocks = [e for e in skipped if "foreign" in (e.get("skip_reason") or "").lower()
                          or "품목허가" in (e.get("headline") or "").lower()
                          and e.get("skip_stage") == "GUARDRAIL"]

    # BUY 시그널
    buys = [d for d in decisions if d.get("action") == "BUY"]
    skips_llm = [d for d in decisions if d.get("action") == "SKIP"]

    # 매도 실행 로그
    sell_events = [r for r in records if r.get("type") in ("sell", "exit", "trailing_stop_exit", "t5m_exit")]

    # confidence 분포
    confidences = [d.get("confidence", 0) for d in decisions]

    return {
        "total_events": total_events,
        "skipped": len(skipped),
        "passed": len(passed),
        "block_rate": f"{len(skipped)/total_events*100:.1f}%" if total_events > 0 else "N/A",
        "skip_reasons": skip_reasons.most_common(10),
        "article_blocks": len(article_blocks),
        "foreign_cap_blocks": len(foreign_cap_blocks),
        "buy_signals": len(buys),
        "skip_signals": len(skips_llm),
        "sell_events": len(sell_events),
        "sell_details": sell_events[-5:],  # 최근 5건
        "avg_confidence": sum(confidences) / len(confidences) if confidences else 0,
        "buys": buys,
    }


def _format_report(stats: dict) -> str:
    lines = []
    lines.append("=" * 60)
    lines.append(f"  v82 모니터 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 60)

    lines.append(f"\n📊 이벤트: {stats['total_events']}건 (통과 {stats['passed']} / 차단 {stats['skipped']})")
    lines.append(f"   차단률: {stats['block_rate']}")

    lines.append(f"\n🎯 시그널: BUY {stats['buy_signals']} / SKIP {stats['skip_signals']}")
    lines.append(f"   평균 confidence: {stats['avg_confidence']:.1f}")

    lines.append(f"\n🛡️ v82 신규 차단:")
    lines.append(f"   CEO/인물발언 하드블록: {stats['article_blocks']}건")
    lines.append(f"   해외품목허가 cap: {stats['foreign_cap_blocks']}건")

    lines.append(f"\n💰 매도 실행: {stats['sell_events']}건")
    for s in stats.get("sell_details", []):
        ticker = s.get("ticker", "?")
        ret = s.get("exit_ret_pct") or s.get("ret_pct") or s.get("pnl_pct", "?")
        reason = s.get("exit_reason") or s.get("reason", "?")
        lines.append(f"   [{ticker}] {ret}% — {reason}")

    if stats["skip_reasons"]:
        lines.append("\n📋 차단 사유 TOP:")
        for reason, cnt in stats["skip_reasons"]:
            lines.append(f"   {reason}: {cnt}건")

    if stats["buys"]:
        lines.append("\n🟢 BUY 시그널:")
        for b in stats["buys"][-5:]:
            ticker = b.get("ticker", "?")
            conf = b.get("confidence", "?")
            reason = (b.get("reason") or "")[:40]
            lines.append(f"   [{ticker}] conf={conf} — {reason}")

    lines.append("")
    return "\n".join(lines)


def main():
    log_dir = Path(os.environ.get("LOG_DIR", PROJECT_ROOT / "logs"))
    # 서버에서는 /opt/kindshot/logs
    if not log_dir.exists():
        server_log_dir = Path("/opt/kindshot/logs")
        if server_log_dir.exists():
            log_dir = server_log_dir

    date_str = None
    watch = False
    interval = 30

    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--watch":
            watch = True
            if i + 1 < len(args) and args[i + 1].isdigit():
                interval = int(args[i + 1])
        elif arg == "--date" and i + 1 < len(args):
            date_str = args[i + 1]

    if watch:
        try:
            while True:
                os.system("clear" if os.name != "nt" else "cls")
                records = _load_today_records(log_dir, date_str)
                if records:
                    stats = _analyze(records)
                    print(_format_report(stats))
                else:
                    print(f"로그 없음 ({log_dir})")
                print(f"[{interval}초 후 갱신 — Ctrl+C 종료]")
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n모니터 종료.")
    else:
        records = _load_today_records(log_dir, date_str)
        if records:
            stats = _analyze(records)
            print(_format_report(stats))
        else:
            print(f"로그 없음 ({log_dir})")


if __name__ == "__main__":
    main()
