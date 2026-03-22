#!/usr/bin/env python3
"""UNKNOWN 배치 리뷰 — inbox에 쌓인 UNKNOWN 이벤트를 LLM으로 일괄 분류.

사용법:
    python scripts/unknown_batch_review.py                 # 미리뷰 inbox 전체
    python scripts/unknown_batch_review.py --date 2026-03-18  # 특정 날짜
    python scripts/unknown_batch_review.py --max-items 50  # 최대 50건
    python scripts/unknown_batch_review.py --dry-run        # 실제 LLM 호출 없이 대상 목록만

요구사항:
    - ANTHROPIC_API_KEY 환경변수 필요
    - logs/unknown_inbox/ 에 inbox JSONL 파일 존재

동작:
    1. inbox 파일에서 미리뷰 이벤트 추출 (이미 review된 건 제외)
    2. UnknownReviewEngine으로 LLM 분류
    3. 결과를 logs/unknown_review/ 에 기록
    4. promotion 대상은 logs/unknown_promotion/ 에 기록
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 프로젝트 루트 기준 sys.path 설정
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kindshot.config import Config

_KST = timezone(timedelta(hours=9))


def _load_config() -> Config:
    from dotenv import load_dotenv
    load_dotenv()
    return Config()


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _find_pending_reviews(config: Config, date_filter: str = "") -> list[tuple[str, dict]]:
    """inbox에 있지만 review에 없는 이벤트 목록 반환."""
    inbox_dir = config.unknown_inbox_dir
    review_dir = config.unknown_review_dir

    if not inbox_dir.exists():
        return []

    pattern = f"{date_filter}*.jsonl" if date_filter else "*.jsonl"
    inbox_files = sorted(inbox_dir.glob(pattern))

    pending = []
    for inbox_file in inbox_files:
        date_str = inbox_file.stem
        inbox_rows = _read_jsonl(inbox_file)
        review_file = review_dir / f"{date_str}.jsonl"
        reviewed_ids = {
            str(r.get("event_id", "")).strip()
            for r in _read_jsonl(review_file)
        }

        for row in inbox_rows:
            event_id = str(row.get("event_id", "")).strip()
            if event_id and event_id not in reviewed_ids:
                pending.append((date_str, row))

    return pending


async def _run_batch_review(
    config: Config,
    pending: list[tuple[str, dict]],
    *,
    max_items: int = 0,
    dry_run: bool = False,
) -> dict:
    """배치 리뷰 실행."""
    from kindshot.unknown_review import (
        UnknownReviewEngine,
        UnknownReviewRequest,
        append_unknown_review,
        append_unknown_promotion,
        evaluate_unknown_promotion,
    )
    from kindshot.models import ReviewStatus

    if max_items > 0:
        pending = pending[:max_items]

    print(f"\n배치 리뷰 대상: {len(pending)}건")
    if dry_run:
        print("[DRY RUN] LLM 호출 없이 대상 목록만 출력")
        for date_str, row in pending:
            print(f"  [{date_str}] {row.get('ticker','?')} {row.get('headline','')[:60]}")
        return {"mode": "dry_run", "pending_count": len(pending)}

    engine = UnknownReviewEngine(config)
    stats = Counter()

    for i, (date_str, row) in enumerate(pending):
        event_id = row.get("event_id", "")
        headline = row.get("headline", "")
        ticker = row.get("ticker", "")

        request = UnknownReviewRequest(
            event_id=event_id,
            detected_at=datetime.fromisoformat(row["detected_at"]) if row.get("detected_at") else datetime.now(_KST),
            runtime_mode=row.get("runtime_mode", "batch"),
            ticker=ticker,
            corp_name=row.get("corp_name", ""),
            headline=headline,
            rss_link=row.get("rss_link", ""),
            rss_guid=row.get("rss_guid"),
            published=row.get("published"),
            source=row.get("source", "batch"),
        )

        try:
            reviews = await engine.review_with_optional_article(request)
            for review in reviews:
                append_unknown_review(config, request.detected_at, review)

            latest = reviews[-1]
            status = latest.review_status.value if hasattr(latest.review_status, 'value') else str(latest.review_status)
            stats[status] += 1

            bucket_str = ""
            if hasattr(latest, 'suggested_bucket') and latest.suggested_bucket:
                bucket_str = latest.suggested_bucket.value if hasattr(latest.suggested_bucket, 'value') else str(latest.suggested_bucket)

            # Promotion 평가
            if config.unknown_paper_promotion_enabled and status == "ok":
                promotion = evaluate_unknown_promotion(config, latest)
                append_unknown_promotion(config, request.detected_at, promotion)
                promo_status = promotion.promotion_status.value if hasattr(promotion.promotion_status, 'value') else str(promotion.promotion_status)
                if promo_status == "promoted":
                    stats["promoted"] += 1

            progress = f"[{i+1}/{len(pending)}]"
            print(f"  {progress} {ticker} → {status} bucket={bucket_str} {headline[:40]}")

        except Exception as e:
            stats["error"] += 1
            print(f"  [{i+1}/{len(pending)}] {ticker} → ERROR: {e}")

        # Rate limit 대응: 건당 0.5초 대기
        if i < len(pending) - 1:
            await asyncio.sleep(0.5)

    print(f"\n=== 배치 리뷰 완료 ===")
    print(f"  총: {len(pending)}건")
    for k, v in stats.most_common():
        print(f"  {k}: {v}")

    return {"total": len(pending), "stats": dict(stats)}


def main() -> None:
    date_filter = ""
    max_items = 0
    dry_run = "--dry-run" in sys.argv

    for i, arg in enumerate(sys.argv):
        if arg == "--date" and i + 1 < len(sys.argv):
            date_filter = sys.argv[i + 1]
        elif arg == "--max-items" and i + 1 < len(sys.argv):
            max_items = int(sys.argv[i + 1])

    config = _load_config()
    pending = _find_pending_reviews(config, date_filter)

    if not pending:
        print("리뷰 대기 건 없음.")
        return

    asyncio.run(_run_batch_review(config, pending, max_items=max_items, dry_run=dry_run))


if __name__ == "__main__":
    main()
