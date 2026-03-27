"""v67: 변동성 레짐 감지 및 confidence 보정 테스트."""

from kindshot.guardrails import (
    detect_volatility_regime,
    apply_volatility_confidence_adjustment,
    apply_news_category_confidence_adjustment,
)


class TestDetectVolatilityRegime:
    def test_high_from_index_change(self):
        assert detect_volatility_regime(kospi_change_pct=-2.0) == "high"
        assert detect_volatility_regime(kospi_change_pct=1.5) == "high"

    def test_high_from_vol_pct(self):
        assert detect_volatility_regime(vol_pct_20d=40.0) == "high"

    def test_high_from_atr(self):
        assert detect_volatility_regime(atr_14=5.0) == "high"

    def test_low_regime(self):
        assert detect_volatility_regime(kospi_change_pct=0.1, kosdaq_change_pct=0.2) == "low"

    def test_low_regime_with_low_vol(self):
        assert detect_volatility_regime(kospi_change_pct=0.1, vol_pct_20d=10.0) == "low"

    def test_normal_regime(self):
        assert detect_volatility_regime(kospi_change_pct=0.8, kosdaq_change_pct=-0.5) == "normal"

    def test_no_data_returns_low(self):
        # 데이터 없으면 idx_change=0 < 0.3 → low
        assert detect_volatility_regime() == "low"

    def test_high_vol_overrides_low_index(self):
        # 지수 변동 작지만 vol_pct_20d 높으면 high
        assert detect_volatility_regime(kospi_change_pct=0.1, vol_pct_20d=36.0) == "high"

    def test_borderline_normal(self):
        # 지수 0.5% → normal (not low, not high)
        assert detect_volatility_regime(kospi_change_pct=0.5) == "normal"


class TestApplyVolatilityConfidenceAdjustment:
    def test_high_penalty(self):
        assert apply_volatility_confidence_adjustment(80, "high") == 77

    def test_low_boost(self):
        assert apply_volatility_confidence_adjustment(80, "low") == 82

    def test_normal_no_change(self):
        assert apply_volatility_confidence_adjustment(80, "normal") == 80

    def test_high_floor_at_zero(self):
        assert apply_volatility_confidence_adjustment(2, "high") == 0

    def test_low_cap_at_100(self):
        assert apply_volatility_confidence_adjustment(99, "low") == 100


class TestApplyNewsCategoryConfidenceAdjustment:
    def test_shareholder_boost(self):
        assert apply_news_category_confidence_adjustment(80, "shareholder_return") == 82  # v71: +2

    def test_contract_penalty(self):
        assert apply_news_category_confidence_adjustment(80, "contract") == 78  # v72: -2

    def test_policy_penalty(self):
        assert apply_news_category_confidence_adjustment(80, "policy_funding") == 78

    def test_other_boost(self):
        assert apply_news_category_confidence_adjustment(80, "other") == 82  # v71: +2

    def test_clinical_neutral(self):
        assert apply_news_category_confidence_adjustment(80, "clinical_regulatory") == 80  # v71: 0

    def test_mna_strong_boost(self):
        assert apply_news_category_confidence_adjustment(80, "mna") == 85  # v71: +5

    def test_cap_at_100(self):
        assert apply_news_category_confidence_adjustment(99, "shareholder_return") == 100

    def test_floor_at_0(self):
        assert apply_news_category_confidence_adjustment(1, "policy_funding") == 0
