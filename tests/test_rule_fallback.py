"""Tests for rule-based fallback decision (LLM unavailable)."""

import pytest
from kindshot.decision import _rule_based_decide
from kindshot.models import Bucket, ContextCard


def _ctx(**kw):
    return ContextCard(**kw)


class TestRuleFallbackBucket:
    def test_pos_weak_always_skip(self):
        result = _rule_based_decide(Bucket.POS_WEAK, "자사주 소각 결정", ["자사주 소각"], _ctx())
        assert result["action"] == "SKIP"

    def test_neg_strong_always_skip(self):
        result = _rule_based_decide(Bucket.NEG_STRONG, "유상증자 결정", ["유상증자"], _ctx())
        assert result["action"] == "SKIP"

    def test_unknown_always_skip(self):
        result = _rule_based_decide(Bucket.UNKNOWN, "어떤 공시", [], _ctx())
        assert result["action"] == "SKIP"


class TestRuleFallbackPosStrong:
    def test_high_conviction_supply_contract(self):
        result = _rule_based_decide(Bucket.POS_STRONG, "공급계약 체결", ["공급계약"], _ctx())
        assert result["action"] == "BUY"
        assert result["confidence"] >= 75
        assert "공급계약" in result["reason"]

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
        """당일 3%+ 상승 시 추격매수 차단."""
        result = _rule_based_decide(
            Bucket.POS_STRONG, "공급계약 체결", ["공급계약"],
            _ctx(ret_today=5.0),
        )
        assert result["action"] == "SKIP"
        assert "chase_buy" in result["reason"]

    def test_no_chase_block_under_threshold(self):
        result = _rule_based_decide(
            Bucket.POS_STRONG, "공급계약 체결", ["공급계약"],
            _ctx(ret_today=2.0),
        )
        assert result["action"] == "BUY"

    def test_size_hint_conservative(self):
        """Fallback never gives L size."""
        result = _rule_based_decide(Bucket.POS_STRONG, "대항 공개매수", ["대항 공개매수"], _ctx())
        assert result["action"] == "BUY"
        assert result["size_hint"] in ("S", "M")

    def test_order_keyword(self):
        result = _rule_based_decide(Bucket.POS_STRONG, "수주 공시", ["수주"], _ctx())
        assert result["action"] == "BUY"
        assert result["confidence"] >= 77

    def test_patent(self):
        result = _rule_based_decide(Bucket.POS_STRONG, "특허 등록", ["특허"], _ctx())
        assert result["action"] == "BUY"
        assert result["confidence"] >= 77
