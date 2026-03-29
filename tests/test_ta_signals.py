"""Tests for ta_signals module."""

from __future__ import annotations

import pytest

from kindshot.ta_signals import (
    TAConfig,
    TASignalType,
    check_mean_reversion,
    check_momentum,
    check_volume_spike,
    ta_entry_filter,
)


class TestMeanReversion:
    def test_oversold_detected(self):
        # 20일간 약간의 변동 후 급락 → z-score < -2
        closes = [100.0, 102.0, 99.0, 101.0, 98.0,
                  103.0, 97.0, 100.0, 102.0, 99.0,
                  101.0, 98.0, 103.0, 97.0, 100.0,
                  102.0, 99.0, 101.0, 98.0, 100.0,
                  80.0]
        sig = check_mean_reversion(closes)
        assert sig is not None
        assert sig.signal_type == TASignalType.MEAN_REVERSION
        assert sig.z_score is not None and sig.z_score < -2.0
        assert 0 < sig.score <= 1.0

    def test_normal_price_no_signal(self):
        closes = [100.0 + i * 0.1 for i in range(21)]
        sig = check_mean_reversion(closes)
        assert sig is None

    def test_insufficient_data(self):
        closes = [100.0] * 10
        sig = check_mean_reversion(closes)
        assert sig is None

    def test_custom_config(self):
        closes = [100.0] * 10 + [90.0]
        cfg = TAConfig(mr_lookback=10, mr_z_threshold=-1.0)
        sig = check_mean_reversion(closes, config=cfg)
        # 표준편차 0이므로 None (모두 같은 값)
        assert sig is None  # std=0 → None

    def test_with_variance(self):
        # 변동 있는 데이터에서 급락
        closes = [100.0, 102.0, 98.0, 101.0, 99.0,
                  103.0, 97.0, 100.0, 102.0, 98.0,
                  101.0, 99.0, 103.0, 97.0, 100.0,
                  102.0, 98.0, 101.0, 99.0, 100.0,
                  85.0]  # 급락
        sig = check_mean_reversion(closes)
        assert sig is not None
        assert sig.z_score < -2.0


class TestVolumeSpike:
    def test_spike_with_bullish_candle(self):
        volumes = [1000.0] * 20 + [5000.0]  # 5x spike
        closes = [100.0] * 20 + [105.0]
        opens = [100.0] * 20 + [100.0]  # 양봉
        sig = check_volume_spike(volumes, closes, opens)
        assert sig is not None
        assert sig.signal_type == TASignalType.VOLUME_SPIKE
        assert sig.volume_ratio >= 3.0

    def test_spike_with_bearish_candle_no_signal(self):
        volumes = [1000.0] * 20 + [5000.0]
        closes = [100.0] * 20 + [95.0]  # 음봉
        opens = [100.0] * 20 + [100.0]
        sig = check_volume_spike(volumes, closes, opens)
        assert sig is None

    def test_normal_volume_no_signal(self):
        volumes = [1000.0] * 21
        closes = [100.0] * 20 + [105.0]
        opens = [100.0] * 21
        sig = check_volume_spike(volumes, closes, opens)
        assert sig is None

    def test_insufficient_data(self):
        sig = check_volume_spike([100] * 5, [100] * 5, [100] * 5)
        assert sig is None


class TestMomentum:
    def test_uptrend(self):
        closes = [100.0] * 20 + [110.0]  # +10%
        sig = check_momentum(closes)
        assert sig is not None
        assert sig.momentum_pct > 0
        assert "상승" in sig.detail

    def test_downtrend(self):
        closes = [100.0] * 20 + [90.0]  # -10%
        cfg = TAConfig(mom_threshold=0.0)
        sig = check_momentum(closes, config=cfg)
        assert sig is not None
        assert sig.momentum_pct < 0
        assert "하락" in sig.detail

    def test_insufficient_data(self):
        sig = check_momentum([100.0] * 5)
        assert sig is None


class TestTAEntryFilter:
    def test_returns_all_keys(self):
        closes = [100.0] * 21
        result = ta_entry_filter(closes)
        assert "mean_reversion" in result
        assert "volume_spike" in result
        assert "momentum" in result

    def test_with_volume_data(self):
        closes = [100.0] * 20 + [105.0]
        volumes = [1000.0] * 20 + [5000.0]
        opens = [100.0] * 20 + [100.0]
        result = ta_entry_filter(closes, volumes=volumes, opens=opens)
        assert result["volume_spike"] is not None

    def test_without_volume_data(self):
        closes = [100.0] * 21
        result = ta_entry_filter(closes)
        assert result["volume_spike"] is None
