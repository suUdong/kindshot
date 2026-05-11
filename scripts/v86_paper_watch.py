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
DOCS = REPO / "docs"


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
    _emit_wrap(out_path, log_path, args.baseline_trades)
    return 0


def _emit_wrap(csv_path: Path, log_path: Path, baseline: int) -> None:
    """30분 종료 시점에 docs/ 에 최종 wrap markdown 작성."""
    rows = list(csv.DictReader(csv_path.open()))
    if not rows:
        return
    last = rows[-1]
    first_signal_iter = next((i + 1 for i, r in enumerate(rows)
                              if int(r["v86_signals_in_db"]) > 0), None)
    out_md = DOCS / f"{datetime.now().strftime('%Y-%m-%d')}-ks-v86-paper-wrap.md"
    log_v86_lines = int(last.get("log_v86_lines", 0))
    delta = int(last["delta_vs_baseline"])
    v86_db = int(last["v86_signals_in_db"])
    out_md.write_text(
        f"# KS v86 Paper 30분 윈도우 wrap ({last['ts_kst']} KST)\n\n"
        f"- watcher CSV: `{csv_path.relative_to(REPO)}` (iterations={len(rows)})\n"
        f"- baseline trades = {baseline}\n"
        f"- final trades_count = {last['trades_count']} (delta = {delta:+d})\n"
        f"- v86 signals in db = {v86_db}\n"
        f"- log v86 lines (cumulative) = {log_v86_lines}\n"
        f"- first v86 signal at iteration = {first_signal_iter or 'n/a'}\n"
        f"- daemon log: `{log_path.relative_to(REPO)}`\n\n"
        "## 해석\n"
        + ("- v86 lane 첫 시그널 발생 → trade_history 기록 확인 필요\n"
           if v86_db > 0 else
           "- v86 lane 시그널 0건. universe ground truth + 시장 regime 진단 필요 "
           "(scripts/v86_diagnose.py)\n")
    )
    print(f"wrap md -> {out_md}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
