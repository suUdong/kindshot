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


def test_ignore_shareholder_meeting():
    result = classify("주주총회 소집 결과")
    assert result.bucket == Bucket.IGNORE

def test_ignore_audit_report():
    result = classify("삼성전자(주) 감사보고서 제출")
    assert result.bucket == Bucket.IGNORE

def test_ignore_share_count_change():
    result = classify("케이뱅크, 최대주주등 소유주식수 126,690,193주 증가")
    assert result.bucket == Bucket.IGNORE

def test_unknown_no_keywords():
    result = classify("로킷헬스케어 탈모치료 소재 전임상서 모낭 수 늘고 모발 굵어져")
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


# ── New bucket tests ──────────────────────────────────

def test_neg_strong_earnings_shock():
    result = classify("A사, 3분기 어닝 쇼크…영업적자 전환")
    assert result.bucket == Bucket.NEG_STRONG
    assert "어닝 쇼크" in result.keyword_hits

def test_pos_strong_earnings_surprise():
    result = classify("삼성전자, 어닝 서프라이즈…사상최대 영업이익")
    assert result.bucket == Bucket.POS_STRONG
    assert "어닝 서프라이즈" in result.keyword_hits

def test_neg_strong_spin_off():
    result = classify("LG화학, 배터리사업부 물적분할 결정")
    assert result.bucket == Bucket.NEG_STRONG
    assert "물적분할 결정" in result.keyword_hits

def test_pos_weak_demerger():
    result = classify("현대백화점, 면세사업부 인적분할 결정")
    assert result.bucket == Bucket.POS_WEAK
    assert "인적분할 결정" in result.keyword_hits

def test_neg_strong_audit_opinion():
    result = classify("A사, 감사의견 거절로 관리종목 지정")
    assert result.bucket == Bucket.NEG_STRONG

def test_pos_strong_fda():
    result = classify("한미약품, 신약 FDA 승인 획득")
    assert result.bucket == Bucket.POS_STRONG
    assert "FDA 승인" in result.keyword_hits

def test_neg_strong_clinical_failure():
    result = classify("브릿지바이오, 임상 3상 실패…임상 중단")
    assert result.bucket == Bucket.NEG_STRONG

def test_pos_strong_tech_export():
    result = classify("에이비엘바이오, 글로벌 빅파마와 기술수출 계약 체결")
    assert result.bucket == Bucket.POS_STRONG
    assert "기술수출 계약 체결" in result.keyword_hits

def test_pos_strong_proxy_fight():
    result = classify("행동주의 펀드, A사 경영권 분쟁 돌입…공개매수 선언")
    assert result.bucket == Bucket.POS_STRONG
    assert "경영권 분쟁" in result.keyword_hits

def test_neg_strong_proxy_fight_end():
    """경영권 분쟁 종료 = NEG (원점 복귀 패턴), 복합 키워드 우선"""
    result = classify("A사, 경영권 분쟁 종료…양측 합의")
    assert result.bucket == Bucket.NEG_STRONG
    assert "경영권 분쟁 종료" in result.keyword_hits

def test_pos_strong_special_dividend():
    result = classify("삼성전자, 특별배당 1주당 1000원 결정")
    assert result.bucket == Bucket.POS_STRONG
    assert "특별배당" in result.keyword_hits

def test_neg_strong_going_concern():
    result = classify("A사, 계속기업 불확실성 감사보고서 제출")
    assert result.bucket == Bucket.NEG_STRONG
    assert "계속기업 불확실성" in result.keyword_hits
