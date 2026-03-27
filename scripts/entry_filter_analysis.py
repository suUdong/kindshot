#!/usr/bin/env python3
"""Build an entry-filter evidence report from local Kindshot logs."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from kindshot.entry_filter_analysis import build_entry_filter_report, render_entry_filter_report
from kindshot.tz import KST as _KST

ANALYSIS_DIR = PROJECT_ROOT / "logs" / "daily_analysis"
def main() -> int:
    report = build_entry_filter_report(
        log_dir=PROJECT_ROOT / "logs",
        runtime_context_dir=PROJECT_ROOT / "data" / "runtime" / "context_cards",
        runtime_snapshot_dir=PROJECT_ROOT / "data" / "runtime" / "price_snapshots",
    )
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(_KST).strftime("%Y%m%d")
    json_path = ANALYSIS_DIR / f"entry_filter_analysis_{stamp}.json"
    txt_path = ANALYSIS_DIR / f"entry_filter_analysis_{stamp}.txt"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    txt_path.write_text(render_entry_filter_report(report), encoding="utf-8")
    print(txt_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
