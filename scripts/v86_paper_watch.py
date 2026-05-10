"""v86 paper 활성화 검증 워처 (단일 실행).

실행 직후부터 5분 간격으로 6회 (총 30분) trade_history.db / paper 로그를
스냅샷해서 data/runtime/v86_paper_watch_<YYYYMMDD_HHMMSS>.csv 에 누적 기록.

사용:
  .venv/bin/python scripts/v86_paper_watch.py \
    --log logs/paper-v86-20260511.log \
    --baseline-trades 14
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DB = REPO / "data" / "trade_history.db"
RUNTIME = REPO / "data" / "runtime"


def snapshot(log_path: Path, baseline: int) -> dict:
    con = sqlite3.connect(DB)
    cur = con.cursor()
    trades = cur.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    v86_rows = cur.execute(
        "SELECT COUNT(*) FROM trades "
        "WHERE decision_source = 'volume_breakout' "
        "OR decision_reason LIKE '%v86 breakout%'"
    ).fetchone()[0]
    con.close()

    log_v86_count = 0
    if log_path.exists():
        out = subprocess.run(
            ["grep", "-c", "-E", "VolumeBreakoutFeed|volume_breakout", str(log_path)],
            capture_output=True, text=True, check=False,
        )
        try:
            log_v86_count = int(out.stdout.strip() or "0")
        except ValueError:
            log_v86_count = 0

    return {
        "ts_kst": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trades_count": trades,
        "delta_vs_baseline": trades - baseline,
        "v86_signals_in_db": v86_rows,
        "log_v86_lines": log_v86_count,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--log", required=True, help="paper daemon log path")
    p.add_argument("--baseline-trades", type=int, required=True)
    p.add_argument("--interval-s", type=int, default=300)
    p.add_argument("--iterations", type=int, default=6)
    args = p.parse_args()

    RUNTIME.mkdir(parents=True, exist_ok=True)
    out_path = RUNTIME / f"v86_paper_watch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    log_path = Path(args.log)

    cols = ["ts_kst", "trades_count", "delta_vs_baseline", "v86_signals_in_db", "log_v86_lines"]
    with out_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for i in range(args.iterations):
            row = snapshot(log_path, args.baseline_trades)
            w.writerow(row)
            fh.flush()
            print(f"[{i+1}/{args.iterations}] {row}", file=sys.stderr)
            if i + 1 < args.iterations:
                time.sleep(args.interval_s)

    print(f"watch done -> {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
