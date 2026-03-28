from scripts.v78_guardrail_profitability_validation import (
    bootstrap_mean,
    horizon_stats,
    infer_910a331_summary,
    parse_signal_report,
    parse_signal_rows,
    verify_returns_with_pykrx,
)


def test_parse_signal_rows_extracts_detail_table() -> None:
    markdown = """
| 날짜 | 종목 | 버킷 | conf | 진입가 | T+1(%) | T+5(%) | T+30(%) | 원래가드레일 |
|------|------|------|------|--------|--------|--------|---------|-------------|
| 20260319 | 005380 | POS_STRONG | 78 | 522,000 | -0.96 | -6.13 | N/A | PASSED |
| 20260320 | 007810 | POS_STRONG | 75 | 64,600 | -5.88 | 30.03 | N/A | MARKET_CLOSE_CUTOFF |
""".strip()

    rows = parse_signal_rows(markdown)

    assert len(rows) == 2
    assert rows[0].ticker == "005380"
    assert rows[0].entry_px == 522000
    assert rows[0].ret_t5 == -6.13
    assert rows[1].original_guardrail == "MARKET_CLOSE_CUTOFF"


def test_parse_signal_report_extracts_summary_counts() -> None:
    markdown = """
# Report
- 전체 BUY 시그널: 87건
- v78 가드레일 차단: 32건

| 20260319 | 005380 | POS_STRONG | 78 | 522,000 | -0.96 | -6.13 | N/A | PASSED |
""".strip()

    rows, summary = parse_signal_report(markdown)

    assert len(rows) == 1
    assert summary == {"total_buy_signals": 87, "raw_blocked": 32}


def test_infer_910a331_summary_separates_raw_and_deduped_counts() -> None:
    summary = infer_910a331_summary(total_buy_signals=87, deduped_passed=42, raw_blocked=32)

    assert summary["raw_passed_inferred"] == 55
    assert summary["duplicate_passes_removed"] == 13


def test_horizon_stats_and_bootstrap_mean_cover_basic_shapes() -> None:
    markdown = """
| 20260319 | 005380 | POS_STRONG | 78 | 522,000 | -0.96 | -6.13 | N/A | PASSED |
| 20260320 | 007810 | POS_STRONG | 75 | 64,600 | -5.88 | 30.03 | N/A | MARKET_CLOSE_CUTOFF |
| 20260320 | 237690 | POS_STRONG | 82 | 150,800 | -5.5 | 5.64 | N/A | ORDERBOOK_TOP_LEVEL_LIQUIDITY |
""".strip()
    rows = parse_signal_rows(markdown)

    stats = horizon_stats(rows, "ret_t5")
    interval = bootstrap_mean([row.ret_t5 for row in rows if row.ret_t5 is not None], iterations=2000, seed=7)

    assert stats.count == 3
    assert stats.win_rate == 66.7
    assert stats.avg_ret == 9.85
    assert interval is not None
    assert interval.p05 <= interval.mean <= interval.p95


def test_horizon_stats_uses_true_median_for_even_sample() -> None:
    rows = parse_signal_rows(
        """
| 20260319 | 005380 | POS_STRONG | 78 | 522,000 | -1.0 | -6.0 | N/A | PASSED |
| 20260320 | 007810 | POS_STRONG | 75 | 64,600 | -2.0 | 2.0 | N/A | MARKET_CLOSE_CUTOFF |
| 20260320 | 237690 | POS_STRONG | 82 | 150,800 | -3.0 | 4.0 | N/A | ORDERBOOK_TOP_LEVEL_LIQUIDITY |
| 20260321 | 000660 | POS_STRONG | 82 | 1,013,000 | -4.0 | 30.0 | N/A | PASSED |
""".strip()
    )

    stats = horizon_stats(rows, "ret_t5")

    assert stats.median_ret == 3.0


def test_verify_returns_with_pykrx_checks_entry_and_future_returns(monkeypatch) -> None:
    import pandas as pd
    from scripts import v78_guardrail_profitability_validation as module

    def fake_get_market_ohlcv(start: str, end: str, ticker: str):
        index = pd.to_datetime(["2026-03-19", "2026-03-20", "2026-03-23", "2026-03-24", "2026-03-25", "2026-03-26"])
        if ticker == "005930":
            return pd.DataFrame({"종가": [1, 1, 1, 1, 1, 1]}, index=index)
        if ticker == "005380":
            return pd.DataFrame({"종가": [522000, 517000, 485000, 492000, 501000, 490000]}, index=index)
        raise AssertionError(f"unexpected ticker: {ticker}")

    monkeypatch.setattr(module.stock, "get_market_ohlcv", fake_get_market_ohlcv)
    rows = parse_signal_rows(
        """
| 20260319 | 005380 | POS_STRONG | 78 | 522,000 | -0.96 | -6.13 | N/A | PASSED |
""".strip()
    )

    summary = verify_returns_with_pykrx(rows)

    assert summary.verified_rows == 1
    assert summary.mismatches == []
