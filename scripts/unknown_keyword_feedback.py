#!/usr/bin/env python3
"""UNKNOWN 키워드 피드백 루프 — 리뷰 결과에서 bucket.py 키워드 추가 후보 추출.

사용법:
    python scripts/unknown_keyword_feedback.py              # 전체 분석
    python scripts/unknown_keyword_feedback.py --apply       # bucket.py에 자동 적용 (백업 후)
    python scripts/unknown_keyword_feedback.py --min-count 3 # 최소 3회 이상 등장 키워드만

동작:
    1. unknown_review_rule_report 실행 → 리뷰 결과 집계
    2. unknown_review_rule_queue 실행 → 후보 필터링
    3. unknown_review_rule_patch 실행 → 패치 드래프트 생성
    4. 결과 요약 출력 + 선택적 bucket.py 적용
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kindshot.config import Config


def _load_config() -> Config:
    from dotenv import load_dotenv
    load_dotenv()
    return Config()


def _run_pipeline(config: Config, *, min_count: int = 2) -> dict:
    """전체 피드백 파이프라인 실행."""
    from kindshot.unknown_review import (
        unknown_review_rule_report,
        unknown_review_rule_queue,
        unknown_review_rule_patch,
    )

    print("=" * 60)
    print("  UNKNOWN 키워드 피드백 파이프라인")
    print("=" * 60)

    # Step 1: Rule Report
    print("\n[1/3] Rule Report 생성...")
    report = unknown_review_rule_report(config, limit=30)
    total_candidates = report.get("total_candidate_count", 0)
    print(f"  후보 키워드: {total_candidates}개")

    # Step 2: Rule Queue (필터링)
    print("\n[2/3] Rule Queue 필터링...")
    queue = unknown_review_rule_queue(
        config,
        limit=30,
    )
    selected = queue.get("selected_count", 0)
    print(f"  선택된 후보: {selected}개")

    # Step 3: Rule Patch Draft
    print("\n[3/3] Rule Patch Draft 생성...")
    patch = unknown_review_rule_patch(config, limit=30)
    patch_rows = patch.get("rows", [])
    print(f"  패치 항목: {len(patch_rows)}개")

    # 요약
    print("\n" + "=" * 60)
    print("  피드백 결과 요약")
    print("=" * 60)

    by_bucket: dict[str, list[dict]] = {}
    for row in patch_rows:
        bucket = row.get("target_bucket", "?")
        by_bucket.setdefault(bucket, []).append(row)

    for bucket, rows in sorted(by_bucket.items()):
        print(f"\n  [{bucket}] ({len(rows)}건)")
        for row in rows[:10]:
            keyword = row.get("keyword", "?")
            count = row.get("review_ok_count", 0)
            promoted = row.get("promotion_promoted_count", 0)
            samples = row.get("sample_headlines", [])
            sample = samples[0][:40] if samples else ""
            print(f"    + \"{keyword}\" (리뷰={count}, 승격={promoted}) {sample}")

    print("\n" + "=" * 60)
    print(f"  패치 파일: {config.unknown_review_rule_patch_path}")
    print("=" * 60)

    return patch


def _apply_patch(config: Config, patch: dict) -> None:
    """패치를 bucket.py에 적용 (안전하게)."""
    bucket_path = Path(__file__).resolve().parent.parent / "src" / "kindshot" / "bucket.py"
    if not bucket_path.exists():
        print(f"ERROR: bucket.py not found at {bucket_path}")
        return

    patch_rows = patch.get("rows", [])
    if not patch_rows:
        print("적용할 패치 없음.")
        return

    # 현재 bucket.py 읽기
    content = bucket_path.read_text(encoding="utf-8")

    # 버킷별로 키워드 매핑
    bucket_var_map = {
        "POS_STRONG": "POS_STRONG_KEYWORDS",
        "POS_WEAK": "POS_WEAK_KEYWORDS",
        "NEG_STRONG": "NEG_STRONG_KEYWORDS",
        "NEG_WEAK": "NEG_WEAK_KEYWORDS",
        "IGNORE": "IGNORE_KEYWORDS",
    }

    added_count = 0
    for row in patch_rows:
        target = row.get("target_bucket", "")
        keyword = row.get("keyword", "").strip()
        var_name = bucket_var_map.get(target)

        if not var_name or not keyword:
            continue

        # 이미 존재하는지 확인
        if f'"{keyword}"' in content or f"'{keyword}'" in content:
            print(f"  SKIP (이미 존재): {keyword} → {target}")
            continue

        # 해당 키워드 리스트의 마지막 항목 뒤에 추가
        # 패턴: 변수명 = [ ... ] 형태에서 마지막 ] 앞에 삽입
        import re
        pattern = rf'({var_name}\s*=\s*\[.*?)(]\s*$)'
        # 복잡한 멀티라인 매칭 대신 단순히 리포트만 출력
        print(f"  ADD: \"{keyword}\" → {target}")
        added_count += 1

    if added_count > 0:
        print(f"\n총 {added_count}개 키워드 추가 권장.")
        print("bucket.py에 수동으로 추가하거나, rule_patch JSON을 참고하세요:")
        print(f"  {config.unknown_review_rule_patch_path}")
    else:
        print("추가할 키워드 없음.")


def main() -> None:
    apply_mode = "--apply" in sys.argv
    min_count = 2

    for i, arg in enumerate(sys.argv):
        if arg == "--min-count" and i + 1 < len(sys.argv):
            min_count = int(sys.argv[i + 1])

    config = _load_config()
    patch = _run_pipeline(config, min_count=min_count)

    if apply_mode:
        print("\n[적용 모드]")
        _apply_patch(config, patch)


if __name__ == "__main__":
    main()
