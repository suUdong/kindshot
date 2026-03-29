"""Tests for config.py — defaults, env overrides, edge cases."""

import os
from unittest.mock import patch

from kindshot.config import Config


def test_default_config_creates_without_error():
    """Config() with no env vars should use defaults."""
    # Snapshot only non-kindshot env vars so Config sees true defaults
    clean_env = {k: v for k, v in os.environ.items() if not k.startswith((
        "KIS_", "ANTHROPIC_", "ADV_", "POS_STRONG_", "LLM_", "FEED_", "SPREAD_",
        "CHASE_", "MIN_BUY_", "PAPER_", "TRAILING_", "PARTIAL_", "DYNAMIC_", "MAX_HOLD_",
        "NO_BUY_", "KOSPI_", "MIN_MARKET_", "DAILY_LOSS_", "MAX_POSITIONS",
        "MAX_SECTOR_", "ORDER_SIZE", "PIPELINE_", "PYKRX_", "RECENT_PATTERN_", "UNKNOWN_", "SESSION_",
        "FINALIZE_", "COLLECTOR_", "LOG_DIR", "DATA_DIR", "ALPHA_SCANNER_",
    ))}
    with patch.dict(os.environ, clean_env, clear=True):
        cfg = Config()
        assert cfg.llm_model == "claude-haiku-4-5-20251001"
        assert cfg.kis_is_paper is True
        assert cfg.adv_threshold == 500_000_000
        assert cfg.pos_strong_adv_threshold == 300_000_000
        assert cfg.max_positions == 4


def test_env_override_string():
    with patch.dict(os.environ, {"LLM_MODEL": "claude-sonnet-4-20250514"}):
        cfg = Config()
        assert cfg.llm_model == "claude-sonnet-4-20250514"


def test_env_override_bool():
    with patch.dict(os.environ, {"KIS_IS_PAPER": "false"}):
        cfg = Config()
        assert cfg.kis_is_paper is False


def test_env_override_float():
    with patch.dict(os.environ, {"CHASE_BUY_PCT": "7.5"}):
        cfg = Config()
        assert cfg.chase_buy_pct == 7.5


def test_alpha_scanner_api_env_override():
    with patch.dict(
        os.environ,
        {
            "ALPHA_SCANNER_API_BASE_URL": "http://alpha-scanner.local:8765",
            "ALPHA_SCANNER_API_TIMEOUT_S": "2.5",
        },
    ):
        cfg = Config()
        assert cfg.alpha_scanner_api_base_url == "http://alpha-scanner.local:8765"
        assert cfg.alpha_scanner_api_timeout_s == 2.5


def test_env_override_int():
    with patch.dict(os.environ, {"MIN_BUY_CONFIDENCE": "80"}):
        cfg = Config()
        assert cfg.min_buy_confidence == 80


def test_max_positions_env_override_within_safe_range():
    with patch.dict(os.environ, {"MAX_POSITIONS": "5"}, clear=False):
        cfg = Config()
        assert cfg.max_positions == 5


def test_max_positions_invalid_high_env_falls_back_to_repo_default():
    with patch.dict(os.environ, {"MAX_POSITIONS": "9999"}, clear=False):
        cfg = Config()
        assert cfg.max_positions == 4


def test_trailing_stop_defaults():
    clean_env = {k: v for k, v in os.environ.items() if not k.startswith((
        "KIS_", "ANTHROPIC_", "ADV_", "POS_STRONG_", "LLM_", "FEED_", "SPREAD_",
        "CHASE_", "MIN_BUY_", "PAPER_", "TRAILING_", "PARTIAL_", "DYNAMIC_", "MAX_HOLD_",
        "NO_BUY_", "KOSPI_", "MIN_MARKET_", "DAILY_LOSS_", "MAX_POSITIONS",
        "MAX_SECTOR_", "ORDER_SIZE", "PIPELINE_", "PYKRX_", "RECENT_PATTERN_", "UNKNOWN_", "SESSION_",
        "FINALIZE_", "COLLECTOR_", "LOG_DIR", "DATA_DIR", "FAST_PROFILE_", "ALPHA_SCANNER_",
    ))}
    with patch.dict(os.environ, clean_env, clear=True):
        cfg = Config()
        assert cfg.trailing_stop_enabled is True
        assert cfg.trailing_stop_pct == 1.0  # v70: 0.8→1.0
        assert cfg.trailing_stop_activation_pct == 0.5  # v83: 0.2→0.5
        assert cfg.trailing_stop_early_pct == 0.5  # v83: 0.3→0.5
        assert cfg.trailing_stop_mid_pct == 0.8  # v65: 0.5→0.8
        assert cfg.trailing_stop_late_pct == 1.0  # v65: 0.7→1.0
        assert cfg.partial_take_profit_enabled is True
        assert cfg.partial_take_profit_target_ratio == 1.0
        assert cfg.partial_take_profit_size_pct == 50.0
        assert cfg.trailing_stop_post_partial_early_pct == 0.4
        assert cfg.trailing_stop_post_partial_mid_pct == 0.6
        assert cfg.trailing_stop_post_partial_late_pct == 0.8
        assert cfg.max_hold_minutes == 30  # v83: 20→30
        assert cfg.fast_profile_hold_minutes == 30  # v83: 20→30
        assert cfg.fast_profile_no_buy_after_kst_hour == 14
        assert cfg.fast_profile_no_buy_after_kst_minute == 30  # v78: 0→30 (14:30까지 허용)


def test_unknown_review_defaults_enabled():
    cfg = Config()
    assert cfg.unknown_shadow_review_enabled is True
    assert cfg.unknown_paper_promotion_enabled is True
    assert cfg.unknown_review_article_enrichment_enabled is True


def test_paper_tp_sl_defaults():
    cfg = Config(paper_take_profit_pct=0.8, paper_stop_loss_pct=-1.0)
    assert cfg.paper_take_profit_pct == 0.8
    assert cfg.paper_stop_loss_pct == -1.0


def test_config_is_frozen():
    cfg = Config()
    try:
        cfg.llm_model = "other"  # type: ignore[misc]
        assert False, "Config should be frozen"
    except AttributeError:
        pass


def test_validate_missing_api_key_warns():
    cfg = Config(anthropic_api_key="")
    warnings = cfg.validate()
    assert any("ANTHROPIC_API_KEY" in w for w in warnings)


def test_validate_bad_tp_raises():
    import pytest
    cfg = Config(paper_take_profit_pct=-1.0)
    with pytest.raises(ValueError, match="paper_take_profit_pct"):
        cfg.validate()


def test_validate_bad_sl_raises():
    import pytest
    cfg = Config(paper_stop_loss_pct=1.0)
    with pytest.raises(ValueError, match="paper_stop_loss_pct"):
        cfg.validate()


def test_validate_bad_chase_buy_raises():
    import pytest
    cfg = Config(chase_buy_pct=-2.0)
    with pytest.raises(ValueError, match="chase_buy_pct"):
        cfg.validate()


def test_validate_bad_confidence_raises():
    import pytest
    cfg = Config(min_buy_confidence=150)
    with pytest.raises(ValueError, match="min_buy_confidence"):
        cfg.validate()


def test_validate_bad_max_positions_raises():
    import pytest
    cfg = Config(max_positions=9)
    with pytest.raises(ValueError, match="max_positions"):
        cfg.validate()


def test_load_config_calls_validate():
    from kindshot.config import load_config
    import pytest
    with pytest.raises(ValueError):
        load_config(paper_take_profit_pct=-1.0)


def test_recent_pattern_defaults_and_overrides():
    with patch.dict(
        os.environ,
        {
            "RECENT_PATTERN_ENABLED": "true",
            "RECENT_PATTERN_LOOKBACK_DAYS": "8",
            "RECENT_PATTERN_MIN_TRADES": "3",
            "RECENT_PATTERN_PROFIT_BOOST": "4",
            "RECENT_PATTERN_PROFIT_MIN_WIN_RATE": "0.6",
            "RECENT_PATTERN_LOSS_MAX_WIN_RATE": "0.2",
            "RECENT_PATTERN_LOSS_MAX_TOTAL_PNL_PCT": "-0.7",
        },
        clear=False,
    ):
        cfg = Config()
        assert cfg.recent_pattern_enabled is True
        assert cfg.recent_pattern_lookback_days == 8
        assert cfg.recent_pattern_min_trades == 3
        assert cfg.recent_pattern_profit_boost == 4
        assert cfg.recent_pattern_profit_min_win_rate == 0.6
        assert cfg.recent_pattern_loss_max_win_rate == 0.2
        assert cfg.recent_pattern_loss_max_total_pnl_pct == -0.7


def test_dynamic_daily_loss_recent_win_rate_defaults_and_overrides():
    with patch.dict(
        os.environ,
        {
            "DYNAMIC_DAILY_LOSS_RECENT_TRADE_WINDOW": "5",
            "DYNAMIC_DAILY_LOSS_RECENT_TRADE_MIN_SAMPLES": "4",
            "DYNAMIC_DAILY_LOSS_LOW_WIN_RATE_THRESHOLD": "0.4",
            "DYNAMIC_DAILY_LOSS_LOW_WIN_RATE_MULT": "0.8",
            "DYNAMIC_DAILY_LOSS_ZERO_WIN_RATE_MULT": "0.6",
        },
        clear=False,
    ):
        cfg = Config()
        assert cfg.dynamic_daily_loss_recent_trade_window == 5
        assert cfg.dynamic_daily_loss_recent_trade_min_samples == 4
        assert cfg.dynamic_daily_loss_low_win_rate_threshold == 0.4
        assert cfg.dynamic_daily_loss_low_win_rate_multiplier == 0.8
        assert cfg.dynamic_daily_loss_zero_win_rate_multiplier == 0.6
