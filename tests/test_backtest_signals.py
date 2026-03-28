from scripts.backtest_signals import summarize_signal_counts


def test_summarize_signal_counts_breaks_out_duplicates() -> None:
    assert summarize_signal_counts(total_signals=87, blocked_count=32, deduped_count=42) == {
        "total_signals": 87,
        "blocked_count": 32,
        "raw_passed_count": 55,
        "deduped_count": 42,
        "duplicate_removed_count": 13,
    }
