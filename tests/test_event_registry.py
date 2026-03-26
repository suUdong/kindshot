"""Tests for event registry: dedup, event_id, correction linking."""

from datetime import datetime, timedelta, timezone

import pytest

from kindshot.event_registry import EventRegistry
from kindshot.feed import RawDisclosure
from kindshot.models import EventKind


def _raw(title: str = "삼성전자(005930) - 공급계약 체결", link: str = "https://kind.krx.co.kr/?rcpNo=20260305000001", guid: str = "guid1") -> RawDisclosure:
    return RawDisclosure(
        title=title,
        link=link,
        rss_guid=guid,
        published="2026-03-05T09:12:04+09:00",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=datetime.now(timezone.utc),
    )


def test_dedup_same_uid():
    reg = EventRegistry()
    r1 = _raw()
    e1 = reg.process(r1)
    assert e1 is not None
    e2 = reg.process(r1)
    assert e2 is None  # duplicate


def test_different_uid():
    reg = EventRegistry()
    r1 = _raw(link="https://kind.krx.co.kr/?rcpNo=20260305000001")
    later = datetime.now(timezone.utc) + timedelta(minutes=11)
    r2 = RawDisclosure(
        title="삼성전자(005930) - 공급계약 체결",
        link="https://kind.krx.co.kr/?rcpNo=20260305000002",
        rss_guid="guid2",
        published="2026-03-05T09:23:04+09:00",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=later,
    )
    assert reg.process(r1) is not None
    assert reg.process(r2) is not None


def test_correction_detected():
    reg = EventRegistry()
    original = _raw(title="삼성전자(005930) - 공급계약 체결")
    reg.process(original)

    correction = _raw(
        title="삼성전자(005930) - [정정] 공급계약 체결",
        link="https://kind.krx.co.kr/?rcpNo=20260305000002",
        guid="guid2",
    )
    result = reg.process(correction)
    assert result is not None
    assert result.event_kind == EventKind.CORRECTION
    assert result.parent_id is not None


def test_withdrawal_detected():
    reg = EventRegistry()
    r = _raw(title="삼성전자(005930) - 정정(취소) 유상증자")
    result = reg.process(r)
    assert result is not None
    assert result.event_kind == EventKind.WITHDRAWAL


def test_fallback_event_id_no_uid():
    reg = EventRegistry()
    r = _raw(link="https://example.com/no-uid-here")
    result = reg.process(r)
    assert result is not None
    assert result.event_id_method.value == "FALLBACK"


def test_fallback_no_published_no_guid():
    """Fallback when both published and rss_guid are missing."""
    reg = EventRegistry()
    r = RawDisclosure(
        title="삼성전자(005930) - 공급계약 체결",
        link="https://example.com/no-uid",
        rss_guid=None,
        published=None,
        ticker="005930",
        corp_name="삼성전자",
        detected_at=datetime.now(timezone.utc),
    )
    result = reg.process(r)
    assert result is not None
    assert result.event_id_method.value == "FALLBACK"


def test_correction_only_matches_original_parent():
    """Corrections should only match ORIGINAL events, not other corrections."""
    reg = EventRegistry()
    # Original
    orig = _raw(title="삼성전자(005930) - 공급계약 체결")
    orig_result = reg.process(orig)

    # First correction
    corr1 = _raw(
        title="삼성전자(005930) - [정정] 공급계약 체결",
        link="https://kind.krx.co.kr/?rcpNo=20260305000002",
        guid="guid2",
    )
    corr1_result = reg.process(corr1)
    assert corr1_result is not None
    assert corr1_result.parent_id == orig_result.event_id

    # Second correction should also match the ORIGINAL, not the first correction
    corr2 = _raw(
        title="삼성전자(005930) - [정정] 공급계약 체결",
        link="https://kind.krx.co.kr/?rcpNo=20260305000003",
        guid="guid3",
    )
    corr2_result = reg.process(corr2)
    assert corr2_result is not None
    assert corr2_result.parent_id == orig_result.event_id


def test_ttl_prune_on_new_day():
    """History should be cleared when date changes."""
    reg = EventRegistry()
    day1 = datetime(2026, 3, 5, 10, 0, 0, tzinfo=timezone.utc)
    r1 = RawDisclosure(
        title="삼성전자(005930) - 공급계약 체결",
        link="https://kind.krx.co.kr/?rcpNo=20260305000001",
        rss_guid="guid1",
        published="2026-03-05T09:12:04+09:00",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=day1,
    )
    reg.process(r1)

    # Next day: same link/guid should NOT be deduped (fresh day)
    day2 = datetime(2026, 3, 6, 10, 0, 0, tzinfo=timezone.utc)
    r2 = RawDisclosure(
        title="삼성전자(005930) - 공급계약 체결",
        link="https://kind.krx.co.kr/?rcpNo=20260305000001",
        rss_guid="guid1",
        published="2026-03-05T09:12:04+09:00",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=day2,
    )
    result = reg.process(r2)
    assert result is not None  # Should not be deduped after TTL prune


def test_fallback_includes_link_for_collision_resistance():
    """Two disclosures with same published+ticker+title but different links get different IDs."""
    reg = EventRegistry()
    base_detected_at = datetime.now(timezone.utc)
    r1 = RawDisclosure(
        title="삼성전자(005930) - 임원변경",
        link="https://example.com/page-a",
        rss_guid=None,
        published="2026-03-05T09:00:00+09:00",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=base_detected_at,
    )
    r2 = RawDisclosure(
        title="삼성전자(005930) - 임원변경",
        link="https://example.com/page-b",
        rss_guid=None,
        published="2026-03-05T09:00:00+09:00",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=base_detected_at + timedelta(minutes=11),
    )
    res1 = reg.process(r1)
    res2 = reg.process(r2)
    assert res1 is not None
    assert res2 is not None
    assert res1.event_id != res2.event_id


def test_persistence_survives_restart(tmp_path):
    """Dedup state should persist across registry instances."""
    state_dir = tmp_path / "state"
    reg1 = EventRegistry(state_dir=state_dir)
    r = _raw()
    e1 = reg1.process(r)
    assert e1 is not None

    # New registry with same state_dir should load persisted IDs
    reg2 = EventRegistry(state_dir=state_dir)
    e2 = reg2.process(r)
    assert e2 is None  # should be deduped from persisted state


def test_persistence_without_state_dir():
    """Without state_dir, no file I/O should occur."""
    reg = EventRegistry()  # no state_dir
    r = _raw()
    e = reg.process(r)
    assert e is not None


def test_related_title_duplicate_same_ticker_within_window():
    reg = EventRegistry()
    base_dt = datetime(2026, 3, 13, 7, 44, tzinfo=timezone.utc)
    r1 = RawDisclosure(
        title="헥토이노베이션, 실적·스테이블코인 모멘텀 모두 매력적-유안타",
        link="kis://news/1",
        rss_guid="guid1",
        published="2026-03-13T16:44:00+09:00",
        ticker="214180",
        corp_name="헥토이노베이션",
        detected_at=base_dt,
    )
    r2 = RawDisclosure(
        title='유안타증권 "헥토이노베이션, 실적·모멘텀 확보…스테이블코인 수혜 기대"',
        link="kis://news/2",
        rss_guid="guid2",
        published="2026-03-13T16:50:00+09:00",
        ticker="214180",
        corp_name="헥토이노베이션",
        detected_at=base_dt.replace(minute=52),
    )

    assert reg.process(r1) is not None
    assert reg.process(r2) is None


def test_related_title_duplicate_outside_window_not_deduped():
    reg = EventRegistry()
    r1 = RawDisclosure(
        title="셀트리온, 바이오시밀러 글로벌 규제 완화 최대 수혜 기업",
        link="kis://news/1",
        rss_guid="guid1",
        published="2026-03-13T08:00:00+09:00",
        ticker="068270",
        corp_name="셀트리온",
        detected_at=datetime(2026, 3, 13, 8, 0, tzinfo=timezone.utc),
    )
    r2 = RawDisclosure(
        title="셀트리온, 바이오시밀러 규제 완화 최대 수혜 400조 시장 규모의 경제",
        link="kis://news/2",
        rss_guid="guid2",
        published="2026-03-13T08:20:00+09:00",
        ticker="068270",
        corp_name="셀트리온",
        detected_at=datetime(2026, 3, 13, 8, 20, tzinfo=timezone.utc),
    )

    assert reg.process(r1) is not None
    assert reg.process(r2) is not None


def test_related_title_duplicate_different_ticker_not_deduped():
    reg = EventRegistry()
    base_dt = datetime(2026, 3, 13, 7, 44, tzinfo=timezone.utc)
    r1 = RawDisclosure(
        title="크래프톤, 한화에어로와 피지컬 AI 합작법인 설립",
        link="kis://news/1",
        rss_guid="guid1",
        published="2026-03-13T16:44:00+09:00",
        ticker="259960",
        corp_name="크래프톤",
        detected_at=base_dt,
    )
    r2 = RawDisclosure(
        title="한화에어로, 크래프톤과 피지컬 AI 합작법인 설립",
        link="kis://news/2",
        rss_guid="guid2",
        published="2026-03-13T16:45:00+09:00",
        ticker="012450",
        corp_name="한화에어로스페이스",
        detected_at=base_dt.replace(minute=45),
    )

    assert reg.process(r1) is not None
    assert reg.process(r2) is not None


# ── US-002: EventRegistry.unmark() — 장전 재평가 ──────────────────


def test_unmark_allows_reprocessing():
    """unmark 후 동일 event_id 재처리 가능."""
    reg = EventRegistry()
    r = _raw()
    e1 = reg.process(r)
    assert e1 is not None
    # 동일 이벤트 → DUPLICATE
    assert reg.process(r) is None

    # unmark → 재처리 가능
    assert reg.unmark(e1.event_id) is True
    e2 = reg.process(r)
    assert e2 is not None
    assert e2.event_id == e1.event_id


def test_unmark_nonexistent_returns_false():
    """존재하지 않는 event_id unmark 시 False 반환."""
    reg = EventRegistry()
    assert reg.unmark("nonexistent_id") is False


def test_unmark_does_not_affect_different_events():
    """unmark는 해당 event_id만 영향. 다른 이벤트는 정상 dedup."""
    reg = EventRegistry()
    r1 = RawDisclosure(
        title="삼성전자(005930) - 자사주 소각 결정",
        link="https://kind.krx.co.kr/?rcpNo=A",
        rss_guid="guid_a", published="2026-03-05T09:12:04+09:00",
        ticker="005930", corp_name="삼성전자",
        detected_at=datetime.now(timezone.utc),
    )
    r2 = RawDisclosure(
        title="현대차(005380) - 대규모 수주 체결",
        link="https://kind.krx.co.kr/?rcpNo=B",
        rss_guid="guid_b", published="2026-03-05T09:15:00+09:00",
        ticker="005380", corp_name="현대차",
        detected_at=datetime.now(timezone.utc),
    )

    e1 = reg.process(r1)
    e2 = reg.process(r2)
    assert e1 is not None
    assert e2 is not None

    # unmark r1만
    reg.unmark(e1.event_id)

    # r1 재처리 가능, r2는 여전히 DUPLICATE
    assert reg.process(r1) is not None
    assert reg.process(r2) is None


# ── Cross-source content-hash dedup 테스트 ──────────────────


def test_cross_source_content_hash_dedup():
    """동일 공시가 KIS와 KIND에서 올 때 content-hash로 중복 제거."""
    reg = EventRegistry()
    # KIS에서 먼저 도착
    r_kis = RawDisclosure(
        title="삼성전자(005930) - 대규모 수주 체결",
        link="kis://news/KIS001",
        rss_guid="KIS001",
        published="2026-03-27T09:12:00+09:00",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=datetime.now(timezone.utc),
    )
    # KIND에서 동일 공시 도착 (다른 link/guid, 같은 내용)
    r_kind = RawDisclosure(
        title="삼성전자(005930) - 대규모 수주 체결",
        link="https://kind.krx.co.kr/?rcpNo=20260327000001",
        rss_guid="guid_kind_001",
        published="2026-03-27T09:12:00+09:00",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=datetime.now(timezone.utc),
    )
    assert reg.process(r_kis) is not None
    assert reg.process(r_kind) is None  # cross-source dedup


def test_same_source_same_title_not_content_deduped():
    """동일 소스 내 같은 제목 다른 공시는 content-hash dedup 하지 않음."""
    reg = EventRegistry()
    r1 = RawDisclosure(
        title="삼성전자(005930) - 임원 변경",
        link="https://kind.krx.co.kr/?rcpNo=20260327000010",
        rss_guid="guid10",
        published="2026-03-27T09:00:00+09:00",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=datetime.now(timezone.utc),
    )
    r2 = RawDisclosure(
        title="삼성전자(005930) - 임원 변경",
        link="https://kind.krx.co.kr/?rcpNo=20260327000011",
        rss_guid="guid11",
        published="2026-03-27T09:05:00+09:00",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=datetime.now(timezone.utc) + timedelta(minutes=11),
    )
    assert reg.process(r1) is not None
    assert reg.process(r2) is not None  # 같은 소스 → dedup 안 함


def test_content_hash_clears_on_new_day():
    """날짜 변경 시 content_hashes도 초기화."""
    reg = EventRegistry()
    day1 = datetime(2026, 3, 27, 10, 0, 0, tzinfo=timezone.utc)
    r1 = RawDisclosure(
        title="삼성전자(005930) - 공급계약 체결",
        link="kis://news/D001",
        rss_guid="D001",
        published="2026-03-27T09:00:00+09:00",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=day1,
    )
    reg.process(r1)
    assert len(reg._content_hashes) > 0

    # 날짜 변경
    day2 = datetime(2026, 3, 28, 10, 0, 0, tzinfo=timezone.utc)
    reg._prune_if_new_day(day2)
    assert len(reg._content_hashes) == 0
