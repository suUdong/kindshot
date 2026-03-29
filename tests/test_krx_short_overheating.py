"""Tests for KRX 공매도 과열종목 스크래퍼."""

from __future__ import annotations

from datetime import date

import pytest

from kindshot.krx_short_overheating import (
    OverheatingRecord,
    calc_entry_date,
    filter_released,
    parse_overheating_response,
    score_overheating_confidence,
)


# ── Data Model ──────────────────────────────────────────


class TestOverheatingRecord:
    def test_fields(self):
        rec = OverheatingRecord(
            ticker="005930",
            corp_name="삼성전자",
            market="STK",
            designation_date=date(2026, 3, 20),
            release_date=date(2026, 3, 25),
            designation_type="해제",
            overheating_days=3,
        )
        assert rec.ticker == "005930"
        assert rec.release_date == date(2026, 3, 25)
        assert rec.overheating_days == 3


# ── parse_overheating_response ──────────────────────────


KRX_SAMPLE_RESPONSE = {
    "OutBlock_1": [
        {
            "ISU_SRT_CD": "005930",
            "ISU_ABBRV": "삼성전자",
            "MKT_NM": "KOSPI",
            "OVRHT_TP_NM": "지정",
            "OVRHT_DD_CNT": "3",
            "OVRHT_STRT_DD": "2026/03/20",
            "OVRHT_END_DD": "2026/03/24",
        },
        {
            "ISU_SRT_CD": "005930",
            "ISU_ABBRV": "삼성전자",
            "MKT_NM": "KOSPI",
            "OVRHT_TP_NM": "해제",
            "OVRHT_DD_CNT": "3",
            "OVRHT_STRT_DD": "2026/03/20",
            "OVRHT_END_DD": "2026/03/25",
        },
        {
            "ISU_SRT_CD": "000660",
            "ISU_ABBRV": "SK하이닉스",
            "MKT_NM": "KOSPI",
            "OVRHT_TP_NM": "지정",
            "OVRHT_DD_CNT": "5",
            "OVRHT_STRT_DD": "2026/03/18",
            "OVRHT_END_DD": "2026/03/24",
        },
    ]
}


class TestParseOverheatingResponse:
    def test_parse_all_records(self):
        records = parse_overheating_response(KRX_SAMPLE_RESPONSE)
        assert len(records) == 3

    def test_parse_release_record(self):
        records = parse_overheating_response(KRX_SAMPLE_RESPONSE)
        releases = [r for r in records if r.designation_type == "해제"]
        assert len(releases) == 1
        assert releases[0].ticker == "005930"
        assert releases[0].release_date == date(2026, 3, 25)
        assert releases[0].overheating_days == 3

    def test_empty_response(self):
        assert parse_overheating_response({}) == []
        assert parse_overheating_response({"OutBlock_1": []}) == []

    def test_malformed_record_skipped(self):
        resp = {"OutBlock_1": [{"ISU_SRT_CD": "005930"}]}
        records = parse_overheating_response(resp)
        assert records == []


# ── filter_released ─────────────────────────────────────


class TestFilterReleased:
    def test_filters_only_released(self):
        records = parse_overheating_response(KRX_SAMPLE_RESPONSE)
        released = filter_released(records)
        assert len(released) == 1
        assert released[0].designation_type == "해제"

    def test_filter_with_date(self):
        records = parse_overheating_response(KRX_SAMPLE_RESPONSE)
        released = filter_released(records, released_after=date(2026, 3, 26))
        assert len(released) == 0

    def test_filter_with_date_inclusive(self):
        records = parse_overheating_response(KRX_SAMPLE_RESPONSE)
        released = filter_released(records, released_after=date(2026, 3, 25))
        assert len(released) == 1


# ── calc_entry_date (D+2 영업일) ────────────────────────


class TestCalcEntryDate:
    def test_d2_normal_weekday(self):
        # 수요일 해제 → 금요일 진입
        assert calc_entry_date(date(2026, 3, 25)) == date(2026, 3, 27)

    def test_d2_thursday_release(self):
        # 목요일 해제 → 월요일 진입 (주말 건너뜀)
        assert calc_entry_date(date(2026, 3, 26)) == date(2026, 3, 30)

    def test_d2_friday_release(self):
        # 금요일 해제 → 화요일 진입
        assert calc_entry_date(date(2026, 3, 27)) == date(2026, 3, 31)

    def test_custom_offset(self):
        # D+1
        assert calc_entry_date(date(2026, 3, 25), offset=1) == date(2026, 3, 26)


# ── score_overheating_confidence ────────────────────────


class TestScoreOverheatingConfidence:
    def test_base_confidence(self):
        score = score_overheating_confidence(overheating_days=1, drop_pct=0.0)
        assert score == 60

    def test_long_overheating_bonus(self):
        score = score_overheating_confidence(overheating_days=5, drop_pct=0.0)
        assert score == 70

    def test_moderate_overheating_bonus(self):
        score = score_overheating_confidence(overheating_days=3, drop_pct=0.0)
        assert score == 65

    def test_deep_drop_bonus(self):
        score = score_overheating_confidence(overheating_days=1, drop_pct=-12.0)
        assert score == 75

    def test_moderate_drop_bonus(self):
        score = score_overheating_confidence(overheating_days=1, drop_pct=-6.0)
        assert score == 68

    def test_combined(self):
        score = score_overheating_confidence(overheating_days=7, drop_pct=-15.0)
        assert score == 85

    def test_cap_at_100(self):
        score = score_overheating_confidence(overheating_days=20, drop_pct=-30.0)
        assert score <= 100
