#!/usr/bin/env python3
"""오늘(또는 지정일) kindshot 로그 요약 스크립트.

사용법:
    python deploy/log_summary.py              # 오늘 로그
    python deploy/log_summary.py 20260309     # 특정 날짜
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def summarize(log_path: Path) -> None:
    if not log_path.exists():
        print(f"로그 파일 없음: {log_path}")
        return

    total = 0
    record_types: Counter[str] = Counter()
    buckets: Counter[str] = Counter()
    skip_reasons: Counter[str] = Counter()
    actions: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    tickers: Counter[str] = Counter()

    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                errors["json_parse_error"] += 1
                continue

            total += 1
            rt = rec.get("record_type", "unknown")
            record_types[rt] += 1

            if rt == "event":
                bucket = rec.get("bucket", "unknown")
                buckets[bucket] += 1
                if rec.get("skip_reason"):
                    skip_reasons[rec["skip_reason"]] += 1
                if rec.get("ticker"):
                    tickers[rec["ticker"]] += 1

            elif rt == "decision":
                action = rec.get("action", "unknown")
                actions[action] += 1
                if rec.get("ticker"):
                    tickers[rec["ticker"]] += 1

            elif rt == "price_snapshot":
                pass  # counted in record_types

    print(f"=== {log_path.name} ===")
    print(f"총 레코드: {total}")
    print()

    print("레코드 타입:")
    for k, v in record_types.most_common():
        print(f"  {k}: {v}")
    print()

    print("버킷 분포:")
    for k, v in buckets.most_common():
        print(f"  {k}: {v}")
    print()

    if actions:
        print("LLM 판단:")
        for k, v in actions.most_common():
            print(f"  {k}: {v}")
        print()

    if skip_reasons:
        print("스킵 사유 (상위 10):")
        for k, v in skip_reasons.most_common(10):
            print(f"  {k}: {v}")
        print()

    if tickers:
        print(f"고유 종목 수: {len(tickers)}")
        print("상위 종목:")
        for k, v in tickers.most_common(5):
            print(f"  {k}: {v}")
    print()

    if errors:
        print("에러:")
        for k, v in errors.most_common():
            print(f"  {k}: {v}")


def main() -> None:
    log_dir = Path("/opt/kindshot/logs")
    if not log_dir.exists():
        log_dir = Path("logs")

    if len(sys.argv) > 1:
        date_str = sys.argv[1]
    else:
        from datetime import datetime
        date_str = datetime.now().strftime("%Y%m%d")

    log_path = log_dir / f"kindshot_{date_str}.jsonl"
    summarize(log_path)


if __name__ == "__main__":
    main()
