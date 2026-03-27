from datetime import datetime, timedelta, timezone

from kindshot.entry_filter_analysis import (
    EntryFilterTradeRow,
    compute_effective_entry_delay_ms,
    compute_orderbook_bid_ask_ratios,
    recommend_max_entry_delay_ms,
)
from kindshot.kis_client import OrderbookSnapshot


KST = timezone(timedelta(hours=9))


def test_effective_entry_delay_uses_market_open_for_pre_open_disclosure():
    disclosed_at = datetime(2026, 3, 27, 8, 45, tzinfo=KST)
    entry_time = datetime(2026, 3, 27, 9, 1, 30, tzinfo=KST)

    assert compute_effective_entry_delay_ms(disclosed_at, entry_time) == 90_000


def test_effective_entry_delay_uses_disclosure_time_during_session():
    disclosed_at = datetime(2026, 3, 27, 10, 5, 10, tzinfo=KST)
    entry_time = datetime(2026, 3, 27, 10, 6, 15, tzinfo=KST)

    assert compute_effective_entry_delay_ms(disclosed_at, entry_time) == 65_000


def test_compute_orderbook_bid_ask_ratios_returns_level1_and_total():
    snapshot = OrderbookSnapshot(
        ask_price1=50_000.0,
        bid_price1=49_900.0,
        ask_size1=200,
        bid_size1=100,
        total_ask_size=5_000,
        total_bid_size=3_000,
        spread_bps=20.0,
    )

    level1_ratio, total_ratio = compute_orderbook_bid_ask_ratios(snapshot)

    assert level1_ratio == 0.5
    assert total_ratio == 0.6


def test_recommend_max_entry_delay_ms_prefers_60s_when_stale_cohort_is_weaker():
    rows = [
        EntryFilterTradeRow("a", "20260327", "005930", "fast-1", 0.2, 0.1, 15_000, None, None, None, None),
        EntryFilterTradeRow("b", "20260327", "005930", "fast-2", 0.1, 0.1, 40_000, None, None, None, None),
        EntryFilterTradeRow("c", "20260327", "005930", "stale-1", -0.8, -0.7, 65_000, None, None, None, None),
        EntryFilterTradeRow("d", "20260327", "005930", "stale-2", -0.4, -0.3, 90_000, None, None, None, None),
    ]

    assert recommend_max_entry_delay_ms(rows) == 60_000
