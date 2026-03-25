"""Tests for hold_profile — 보유시간 차등화."""

from kindshot.config import Config
from kindshot.hold_profile import get_max_hold_minutes, resolve_hold_profile


def test_supply_contract_15min():
    """공급계약 키워드 → 15분."""
    cfg = Config()
    assert get_max_hold_minutes("삼성전자 1000억 규모 공급계약 체결", ["공급계약"], cfg) == 15


def test_order_received_15min():
    """수주 키워드 → 15분."""
    cfg = Config()
    assert get_max_hold_minutes("HD현대중공업 8237억원 규모 수주", ["수주"], cfg) == 15


def test_patent_30min():
    """특허 키워드 → 30분."""
    cfg = Config()
    assert get_max_hold_minutes("알테오젠 미국 특허 등록", ["특허"], cfg) == 30


def test_fda_30min():
    """FDA 키워드 → 30분."""
    cfg = Config()
    assert get_max_hold_minutes("FDA 허가 획득", [], cfg) == 30


def test_clinical_phase3_30min():
    """임상3상 → 30분."""
    cfg = Config()
    assert get_max_hold_minutes("임상3상 성공", ["임상3상"], cfg) == 30


def test_treasury_stock_eod():
    """자사주 소각 → EOD (0분)."""
    cfg = Config()
    assert get_max_hold_minutes("자사주 소각 결정", ["자사주 소각"], cfg) == 0


def test_treasury_acquisition_eod():
    """자사주 취득 → EOD (0분)."""
    cfg = Config()
    assert get_max_hold_minutes("자사주취득 결정", ["자사주취득"], cfg) == 0


def test_default_uses_config():
    """매칭 없으면 config.max_hold_minutes 사용."""
    cfg = Config(max_hold_minutes=30)
    assert get_max_hold_minutes("일반적인 공시", [], cfg) == 30


def test_headline_fallback_when_no_keyword_hits():
    """keyword_hits 비어도 headline에서 매칭."""
    cfg = Config()
    assert get_max_hold_minutes("대규모 공급계약 체결 공시", [], cfg) == 15


def test_keyword_hits_priority_over_headline():
    """keyword_hits에서 먼저 매칭하므로 headline의 다른 키워드보다 우선."""
    cfg = Config()
    # keyword_hits에 "자사주 소각" → 0분 (EOD)
    # headline에 "공급계약" → 15분
    # keyword_hits가 우선
    result = get_max_hold_minutes("공급계약 관련 자사주 소각", ["자사주 소각"], cfg)
    assert result == 0


def test_clinical_phase2_20min():
    """임상2상 → 20분."""
    cfg = Config()
    assert get_max_hold_minutes("임상 2상 완료", ["임상 2상"], cfg) == 20


def test_resolve_hold_profile_returns_match():
    cfg = Config()
    minutes, matched = resolve_hold_profile("삼성전자 1000억 규모 공급계약 체결", ["공급계약"], cfg)
    assert minutes == 15
    assert matched == "공급계약"
