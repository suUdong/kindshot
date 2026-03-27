from datetime import datetime, timezone

from kindshot.news_semantics import (
    TickerNewsClusterTracker,
    apply_impact_score_confidence_adjustment,
    build_news_signal,
    extract_contract_amount_eok,
    extract_operating_profit_eok,
    extract_revenue_eok,
    extract_sales_ratio_pct,
)


def test_extract_contract_amount_eok_supports_eok_and_jo():
    assert extract_contract_amount_eok("A사, 8237억 규모 공급계약 체결") == 8237
    assert extract_contract_amount_eok("B사, 1.5조 단일판매ㆍ공급계약 체결") == 15000


def test_extract_revenue_and_operating_profit_eok():
    headline = "C사 지난해 매출 2,341억, 영업이익 187억 기록"
    assert extract_revenue_eok(headline) == 2341
    assert extract_operating_profit_eok(headline) == 187


def test_extract_sales_ratio_pct():
    assert extract_sales_ratio_pct("매출액대비 12.5% 규모 공급계약 체결") == 12.5


def test_cluster_tracker_groups_related_ticker_news():
    tracker = TickerNewsClusterTracker(window_minutes=180)
    t0 = datetime(2026, 3, 28, 0, 0, tzinfo=timezone.utc)
    first = build_news_signal(
        headline="테스트(005930) 8237억 규모 공급계약 체결",
        ticker="005930",
        corp_name="테스트",
        detected_at=t0,
        keyword_hits=["공급계약"],
        cluster_tracker=tracker,
    )
    second = build_news_signal(
        headline="테스트 8200억 공급계약 공시",
        ticker="005930",
        corp_name="테스트",
        detected_at=t0.replace(minute=5),
        keyword_hits=["공급계약"],
        cluster_tracker=tracker,
    )
    assert first.cluster is not None
    assert second.cluster is not None
    assert first.cluster.cluster_id == second.cluster.cluster_id
    assert second.cluster.cluster_size == 2
    assert second.cluster.corroborated is True


def test_cluster_tracker_separates_unrelated_categories():
    tracker = TickerNewsClusterTracker(window_minutes=180)
    t0 = datetime(2026, 3, 28, 0, 0, tzinfo=timezone.utc)
    contract = build_news_signal(
        headline="테스트 8237억 규모 공급계약 체결",
        ticker="005930",
        corp_name="테스트",
        detected_at=t0,
        keyword_hits=["공급계약"],
        cluster_tracker=tracker,
    )
    earnings = build_news_signal(
        headline="테스트 지난해 매출 2341억 영업이익 187억",
        ticker="005930",
        corp_name="테스트",
        detected_at=t0.replace(minute=3),
        keyword_hits=["실적"],
        cluster_tracker=tracker,
    )
    assert contract.cluster is not None
    assert earnings.cluster is not None
    assert contract.cluster.cluster_id != earnings.cluster.cluster_id


def test_build_news_signal_computes_high_impact_for_large_direct_disclosure():
    signal = build_news_signal(
        headline="테스트 8237억 규모 공급계약 체결",
        ticker="005930",
        corp_name="테스트",
        detected_at=datetime(2026, 3, 28, 0, 0, tzinfo=timezone.utc),
        keyword_hits=["공급계약"],
    )
    assert signal.news_category == "contract"
    assert signal.direct_disclosure is True
    assert signal.contract_amount_eok == 8237
    assert signal.impact_score is not None and signal.impact_score >= 75


def test_build_news_signal_penalizes_commentary():
    signal = build_news_signal(
        headline='KB증권 "테스트, 추가 상승 여력 충분…장기공급계약 기대"',
        ticker="005930",
        corp_name="테스트",
        detected_at=datetime(2026, 3, 28, 0, 0, tzinfo=timezone.utc),
        dorg="연합뉴스",
        keyword_hits=["공급계약"],
    )
    assert signal.commentary is True
    assert signal.broker_note is True
    assert signal.impact_score is not None and signal.impact_score <= 45


def test_apply_impact_score_confidence_adjustment_is_bounded():
    assert apply_impact_score_confidence_adjustment(80, 90) == 84
    assert apply_impact_score_confidence_adjustment(80, 40) == 77
    assert apply_impact_score_confidence_adjustment(2, 20) == 0
