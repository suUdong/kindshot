"""Tests for event registry: dedup, event_id, correction linking."""

from datetime import datetime, timezone

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
    r2 = _raw(link="https://kind.krx.co.kr/?rcpNo=20260305000002", guid="guid2")
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
    r1 = RawDisclosure(
        title="삼성전자(005930) - 임원변경",
        link="https://example.com/page-a",
        rss_guid=None,
        published="2026-03-05T09:00:00+09:00",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=datetime.now(timezone.utc),
    )
    r2 = RawDisclosure(
        title="삼성전자(005930) - 임원변경",
        link="https://example.com/page-b",
        rss_guid=None,
        published="2026-03-05T09:00:00+09:00",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=datetime.now(timezone.utc),
    )
    res1 = reg.process(r1)
    res2 = reg.process(r2)
    assert res1 is not None
    assert res2 is not None
    assert res1.event_id != res2.event_id
