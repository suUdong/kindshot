#!/usr/bin/env python3
"""JSONL 로그 로테이션 — 오래된 로그 파일 정리.

사용법:
    python scripts/log_rotate.py                    # 기본 7일 보관
    python scripts/log_rotate.py --keep-days 14     # 14일 보관
    python scripts/log_rotate.py --dry-run          # 삭제 대상만 출력

크론 등록 예시:
    0 4 * * * cd /opt/kindshot && python scripts/log_rotate.py >> logs/rotate.log 2>&1
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_KST = timezone(timedelta(hours=9))

LOG_DIRS = [
    "logs",
    "logs/unknown_inbox",
    "logs/unknown_review",
    "logs/unknown_promotion",
    "logs/unknown_headlines",
]

EXTENSIONS = {".jsonl", ".txt", ".log"}


def _file_age_days(path: Path, now: datetime) -> float:
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=_KST)
    return (now - mtime).total_seconds() / 86400


def rotate(project_root: Path, *, keep_days: int = 7, dry_run: bool = False) -> dict:
    now = datetime.now(_KST)
    deleted = 0
    freed_bytes = 0
    kept = 0

    for rel_dir in LOG_DIRS:
        log_dir = project_root / rel_dir
        if not log_dir.is_dir():
            continue

        for path in sorted(log_dir.iterdir()):
            if not path.is_file():
                continue
            if path.suffix not in EXTENSIONS:
                continue

            age = _file_age_days(path, now)
            if age > keep_days:
                size = path.stat().st_size
                if dry_run:
                    print(f"  [DRY] {path.relative_to(project_root)} ({age:.0f}d, {size/1024:.0f}KB)")
                else:
                    path.unlink()
                    print(f"  DEL  {path.relative_to(project_root)} ({age:.0f}d, {size/1024:.0f}KB)")
                deleted += 1
                freed_bytes += size
            else:
                kept += 1

    return {"deleted": deleted, "kept": kept, "freed_mb": round(freed_bytes / 1e6, 2)}


def main() -> None:
    keep_days = 7
    dry_run = "--dry-run" in sys.argv

    for i, arg in enumerate(sys.argv):
        if arg == "--keep-days" and i + 1 < len(sys.argv):
            keep_days = int(sys.argv[i + 1])

    project_root = Path(__file__).resolve().parent.parent
    print(f"Log rotation: keep={keep_days}d, dry_run={dry_run}")

    result = rotate(project_root, keep_days=keep_days, dry_run=dry_run)
    print(f"Result: deleted={result['deleted']}, kept={result['kept']}, freed={result['freed_mb']}MB")


if __name__ == "__main__":
    main()
