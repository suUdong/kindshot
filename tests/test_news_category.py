"""v67: news_category 모듈 테스트."""

from kindshot.news_category import classify_news_type, get_category_confidence_adjustment


class TestClassifyNewsType:
    def test_contract_keywords(self):
        assert classify_news_type("삼성전자 공급계약 체결 500억") == "contract"
        assert classify_news_type("현대중공업 수주 3000억") == "contract"

    def test_mna_keywords(self):
        assert classify_news_type("A사 B사 인수 완료") == "mna"
        assert classify_news_type("경영권 분쟁 심화") == "mna"

    def test_clinical_regulatory(self):
        assert classify_news_type("FDA 승인 획득") == "clinical_regulatory"
        assert classify_news_type("임상 3상 성공") == "clinical_regulatory"

    def test_shareholder_return(self):
        assert classify_news_type("자사주 소각 결정") == "shareholder_return"
        assert classify_news_type("공개매수 발표") == "shareholder_return"

    def test_earnings_turnaround(self):
        assert classify_news_type("흑자전환 성공") == "earnings_turnaround"
        assert classify_news_type("사상최대 실적") == "earnings_turnaround"

    def test_product_technology(self):
        assert classify_news_type("신제품 출시") == "product_technology"
        assert classify_news_type("기술수출 계약") == "product_technology"

    def test_policy_funding(self):
        assert classify_news_type("국책과제 선정") == "policy_funding"
        assert classify_news_type("MOU 체결") == "policy_funding"

    def test_other_fallback(self):
        assert classify_news_type("일반 뉴스 헤드라인") == "other"

    def test_priority_order(self):
        # shareholder_return이 contract보다 우선
        assert classify_news_type("자사주 소각 및 수주") == "shareholder_return"

    def test_keyword_hits_used(self):
        assert classify_news_type("알 수 없는 헤드라인", ["수주"]) == "contract"


class TestCategoryConfidenceAdjustment:
    def test_shareholder_return_boost(self):
        assert get_category_confidence_adjustment("shareholder_return") == 3

    def test_contract_neutral(self):
        assert get_category_confidence_adjustment("contract") == 0

    def test_policy_funding_penalty(self):
        assert get_category_confidence_adjustment("policy_funding") == -2

    def test_unknown_category(self):
        assert get_category_confidence_adjustment("nonexistent") == 0

    def test_other_neutral(self):
        assert get_category_confidence_adjustment("other") == 0
