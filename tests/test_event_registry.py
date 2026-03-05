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
