from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from backtest_signals import summarize_signal_counts


def test_summarize_signal_counts_separates_dedupe_from_price_availability() -> None:
    summary = summarize_signal_counts(
        total_signals=87,
        blocked_count=32,
        deduped_count=42,
        analyzable_count=24,
    )

    assert summary == {
        "total_signals": 87,
        "blocked_count": 32,
        "raw_passed_count": 55,
        "deduped_count": 42,
        "duplicate_removed_count": 13,
        "analyzable_count": 24,
        "price_unavailable_count": 18,
    }
