"""Tests for rule-based fallback decision (LLM unavailable)."""

import pytest
from kindshot.decision import _rule_based_decide
from kindshot.models import Bucket, ContextCard


def _ctx(**kw):
    return ContextCard(**kw)


class TestRuleFallbackBucket:
    def test_pos_weak_high_conviction_buys(self):
        """POS_WEAK도 매우 고확신(80+) 키워드면 BUY."""
        result = _rule_based_decide(Bucket.POS_WEAK, "자사주 소각 결정", ["자사주 소각"], _ctx())
        assert result["action"] == "BUY"
        assert result["confidence"] >= 80
        assert result["size_hint"] == "S"  # POS_WEAK은 항상 S

    def test_pos_weak_medium_conviction_skips(self):
        """POS_WEAK에서 conf 79 키워드는 SKIP (80 미만)."""
        result = _rule_based_decide(Bucket.POS_WEAK, "대형 계약 체결", ["대형 계약"], _ctx())
        assert result["action"] == "SKIP"
        assert "pos_weak_below_80" in result["reason"]

    def test_pos_weak_no_keyword_skips(self):
        result = _rule_based_decide(Bucket.POS_WEAK, "일반 공시", [], _ctx())
        assert result["action"] == "SKIP"

    def test_neg_strong_always_skip(self):
        result = _rule_based_decide(Bucket.NEG_STRONG, "유상증자 결정", ["유상증자"], _ctx())
        assert result["action"] == "SKIP"

    def test_unknown_always_skip(self):
        result = _rule_based_decide(Bucket.UNKNOWN, "어떤 공시", [], _ctx())
        assert result["action"] == "SKIP"


class TestRuleFallbackPosStrong:
    def test_small_supply_contract_skips(self):
        """금액 없는 단독 공급계약은 SKIP (false positive 방지)."""
        result = _rule_based_decide(Bucket.POS_STRONG, "공급계약 체결", ["공급계약"], _ctx())
        assert result["action"] == "SKIP"
        assert "no_high_conviction" in result["reason"]

    def test_large_supply_contract_buys(self):
        """매출액대비 10%+ 대형 공급계약은 BUY."""
        result = _rule_based_decide(
            Bucket.POS_STRONG,
            "삼성물산, 2.89조원 규모 공급계약(매출액대비 10.5%)",
            ["공급계약"], _ctx(),
        )
        assert result["action"] == "BUY"
        assert result["confidence"] >= 78

    def test_large_amount_contract_buys(self):
        """1000억+ 금액 수주는 BUY."""
        result = _rule_based_decide(
            Bucket.POS_STRONG,
            "LNG운반선 수주 3,779억원 규모",
            ["수주"], _ctx(),
        )
        assert result["action"] == "BUY"
        assert result["confidence"] >= 77

    def test_high_conviction_buyback(self):
        result = _rule_based_decide(Bucket.POS_STRONG, "자사주 소각 결정", ["자사주 소각"], _ctx())
        assert result["action"] == "BUY"
        assert result["confidence"] >= 80

    def test_high_conviction_tender_offer(self):
        result = _rule_based_decide(Bucket.POS_STRONG, "공개매수 발표", ["공개매수"], _ctx())
        assert result["action"] == "BUY"
        assert result["confidence"] >= 80

    def test_high_conviction_earnings(self):
        result = _rule_based_decide(Bucket.POS_STRONG, "어닝 서프라이즈 달성", ["어닝 서프라이즈"], _ctx())
        assert result["action"] == "BUY"
        assert result["confidence"] >= 79

    def test_generic_keyword_skips(self):
        """MOU, 업무협약 등 generic POS_STRONG은 fallback에서 SKIP."""
        result = _rule_based_decide(Bucket.POS_STRONG, "MOU 체결", ["MOU"], _ctx())
        assert result["action"] == "SKIP"
        assert "no_high_conviction" in result["reason"]

    def test_chase_buy_blocked(self):
        """당일 2%+ 상승 시 추격매수 차단."""
        result = _rule_based_decide(
            Bucket.POS_STRONG, "자사주 소각 결정", ["자사주 소각"],
            _ctx(ret_today=5.0),
        )
        assert result["action"] == "SKIP"
        assert "chase_buy" in result["reason"]

    def test_no_chase_block_under_threshold(self):
        result = _rule_based_decide(
            Bucket.POS_STRONG, "자사주 소각 결정", ["자사주 소각"],
            _ctx(ret_today=1.5),
        )
        assert result["action"] == "BUY"

    def test_size_hint_conservative(self):
        """Fallback never gives L size."""
        result = _rule_based_decide(Bucket.POS_STRONG, "대항 공개매수", ["대항 공개매수"], _ctx())
        assert result["action"] == "BUY"
        assert result["size_hint"] in ("S", "M")

    def test_standalone_order_skips(self):
        """단독 '수주'는 금액 없으면 SKIP."""
        result = _rule_based_decide(Bucket.POS_STRONG, "수주 공시", ["수주"], _ctx())
        assert result["action"] == "SKIP"

    def test_standalone_patent_buys(self):
        """'특허 등록'은 확정 공시 → BUY (rule_fallback 강화)."""
        result = _rule_based_decide(Bucket.POS_STRONG, "특허 등록", ["특허"], _ctx())
        assert result["action"] == "BUY"
        assert result["confidence"] >= 76

    def test_bare_patent_keyword_skips(self):
        """단독 '특허' (등록/취득 없음)는 SKIP."""
        result = _rule_based_decide(Bucket.POS_STRONG, "특허 관련 뉴스", ["특허"], _ctx())
        assert result["action"] == "SKIP"

    def test_fda_approval_buys(self):
        result = _rule_based_decide(Bucket.POS_STRONG, "FDA 승인 획득", ["FDA 승인"], _ctx())
        assert result["action"] == "BUY"
        assert result["confidence"] >= 80

    def test_record_earnings_buys(self):
        result = _rule_based_decide(Bucket.POS_STRONG, "사상최대 실적 달성", ["사상최대 실적"], _ctx())
        assert result["action"] == "BUY"
        assert result["confidence"] >= 78

    def test_medium_contract_500_buys(self):
        """500억+ 수주는 BUY."""
        result = _rule_based_decide(
            Bucket.POS_STRONG,
            "방위사업용 수출엔진 공급계약 500억원",
            ["공급계약"], _ctx(),
        )
        assert result["action"] == "BUY"
        assert result["confidence"] >= 77

    def test_stock_cancel_decision_buys(self):
        """'주식 소각 결정' 키워드 BUY."""
        result = _rule_based_decide(
            Bucket.POS_STRONG, "에스케이스퀘어 주식회사 주식 소각 결정",
            ["주식 소각 결정"], _ctx(),
        )
        assert result["action"] == "BUY"
        assert result["confidence"] >= 80

    def test_fda_permit_buys(self):
        """'FDA 허가' 키워드 BUY."""
        result = _rule_based_decide(
            Bucket.POS_STRONG, "제이엘케이, 뇌졸중 AI 美 FDA 허가",
            ["FDA 허가"], _ctx(),
        )
        assert result["action"] == "BUY"
        assert result["confidence"] >= 80

    def test_dev_contract_large_amount_buys(self):
        """대형 개발 계약 (금액 기반) BUY."""
        result = _rule_based_decide(
            Bucket.POS_STRONG,
            "알테오젠, 바이오젠과 최대 8200억원대 개발 계약 체결",
            ["개발 계약"], _ctx(),
        )
        assert result["action"] == "BUY"
        assert result["confidence"] >= 77

    def test_cdmo_contract_buys(self):
        """CDMO 계약 키워드 BUY."""
        result = _rule_based_decide(
            Bucket.POS_STRONG,
            "마티카 바이오, 북미 대형 의료연구기관과 CDMO 계약 체결",
            ["CDMO 계약"], _ctx(),
        )
        assert result["action"] == "BUY"
        assert result["confidence"] >= 78

    def test_trillion_won_contract_buys(self):
        """조 단위 대형 계약 BUY."""
        result = _rule_based_decide(
            Bucket.POS_STRONG,
            "현대건설, 1.96조원 규모 공급계약(수택동 주택재개발 정비사업) 체결",
            ["공급계약"], _ctx(),
        )
        assert result["action"] == "BUY"
        assert result["confidence"] >= 80

    def test_kind_formal_disclosure_buys(self):
        """단일판매ㆍ공급계약체결 KIND 정규 공시 BUY."""
        result = _rule_based_decide(
            Bucket.POS_STRONG,
            "(주)대우건설 단일판매ㆍ공급계약체결",
            ["공급계약"], _ctx(),
        )
        assert result["action"] == "BUY"
        assert result["confidence"] >= 77
