from kindshot.headline_parser import (
    is_commentary_headline,
    is_contract_commentary_headline,
    normalize_analysis_headline,
)


def test_normalize_analysis_headline_strips_tags_and_broker_prefix():
    headline = '[클릭 e종목] KB증권 "삼성전자, 추가 상승 여력 충분…장기공급계약 요구 큰 폭 증가"'
    assert normalize_analysis_headline(headline) == "삼성전자, 추가 상승 여력 충분…장기공급계약 요구 큰 폭 증가"


def test_contract_commentary_headline_detected():
    headline = 'KB증권 "삼성전자, 추가 상승 여력 충분…장기공급계약 요구 큰 폭 증가"'
    assert is_commentary_headline(headline) is True
    assert is_contract_commentary_headline(headline) is True


def test_direct_contract_headline_not_treated_as_commentary():
    headline = "넥스틴, SK하이닉스와 106억 규모 공급계약 체결"
    assert is_commentary_headline(headline) is False
    assert is_contract_commentary_headline(headline) is False
