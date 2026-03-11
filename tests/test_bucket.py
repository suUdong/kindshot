"""Tests for keyword bucketing with NEG-first override."""

import pytest

from kindshot.bucket import classify
from kindshot.models import Bucket


def test_pos_strong_supply_contract():
    result = classify("삼성전자, 신규 공급계약 체결")
    assert result.bucket == Bucket.POS_STRONG
    assert "공급계약" in result.keyword_hits


def test_pos_strong_supply_contract_with_space():
    result = classify("비츠로시스 자회사, 대규모 공급 계약 체결")
    assert result.bucket == Bucket.POS_STRONG
    assert "공급 계약" in result.keyword_hits


def test_neg_strong_override():
    """NEG keyword overrides POS keyword."""
    result = classify("A사, 공급계약 해지 결정")
    assert result.bucket == Bucket.NEG_STRONG
    assert "해지" in result.keyword_hits


def test_neg_strong_cb():
    result = classify("전환사채(CB) 발행 결정")
    assert result.bucket == Bucket.NEG_STRONG


def test_pos_strong_buyback():
    # "취득" != "취소" so this should be POS_STRONG
    result = classify("자사주 매입 결정")
    assert result.bucket == Bucket.POS_STRONG


def test_pos_strong_approval_signal():
    result = classify("종근당, 당뇨병 치료제 품목허가 승인")
    assert result.bucket == Bucket.POS_STRONG
    assert "품목허가 승인" in result.keyword_hits


def test_pos_strong_mfds_approval_signal():
    result = classify("라메디텍, 채혈·혈당 측정기기 식약처 허가")
    assert result.bucket == Bucket.POS_STRONG
    assert "식약처 허가" in result.keyword_hits


def test_unknown_no_keywords():
    result = classify("주주총회 소집 결과")
    assert result.bucket == Bucket.UNKNOWN


def test_pos_weak_cash_dividend_decision():
    result = classify("미창석유, 주당 3,500원 현금배당 결정")
    assert result.bucket == Bucket.POS_WEAK
    assert "현금배당" in result.keyword_hits


def test_pos_weak_target_price_revision():
    result = classify("KCC, 저평가 해소 기대…목표가 68만원 상향")
    assert result.bucket == Bucket.POS_WEAK
    assert "목표가" in result.keyword_hits


def test_neg_weak_target_price_cut_overrides_positive_target_word():
    result = classify("KCC, 실적 둔화 우려…목표가 하향")
    assert result.bucket == Bucket.NEG_WEAK
    assert "목표가 하향" in result.keyword_hits


def test_matched_positions_logged():
    result = classify("대형 수주 및 공급계약 체결")
    assert len(result.keyword_hits) >= 2
    assert len(result.matched_positions) >= 2


def test_withdrawal_still_neg():
    result = classify("정정(취소) 유상증자 결정")
    assert result.bucket == Bucket.NEG_STRONG
