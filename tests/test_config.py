"""Tests for config.py — defaults, env overrides, edge cases."""

import os
from unittest.mock import patch

from kindshot.config import Config


def test_default_config_creates_without_error():
    """Config() with no env vars should use defaults."""
    # Snapshot only non-kindshot env vars so Config sees true defaults
    clean_env = {k: v for k, v in os.environ.items() if not k.startswith((
        "KIS_", "ANTHROPIC_", "ADV_", "LLM_", "FEED_", "SPREAD_",
        "CHASE_", "MIN_BUY_", "PAPER_", "TRAILING_", "MAX_HOLD_",
        "NO_BUY_", "KOSPI_", "MIN_MARKET_", "DAILY_LOSS_", "MAX_POSITIONS",
        "MAX_SECTOR_", "ORDER_SIZE", "PIPELINE_", "PYKRX_", "UNKNOWN_",
        "FINALIZE_", "COLLECTOR_", "LOG_DIR", "DATA_DIR",
    ))}
    with patch.dict(os.environ, clean_env, clear=True):
        cfg = Config()
        assert cfg.llm_model == "claude-haiku-4-5-20251001"
        assert cfg.kis_is_paper is True
        assert cfg.adv_threshold == 500_000_000


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


def test_env_override_int():
    with patch.dict(os.environ, {"MIN_BUY_CONFIDENCE": "80"}):
        cfg = Config()
        assert cfg.min_buy_confidence == 80


def test_trailing_stop_defaults():
    cfg = Config()
    assert cfg.trailing_stop_enabled is True
    assert cfg.trailing_stop_pct == 0.5
    assert cfg.trailing_stop_activation_pct == 0.3
    assert cfg.trailing_stop_early_pct == 0.3
    assert cfg.trailing_stop_mid_pct == 0.5
    assert cfg.trailing_stop_late_pct == 0.7
    assert cfg.max_hold_minutes == 30


def test_unknown_review_defaults_enabled():
    cfg = Config()
    assert cfg.unknown_shadow_review_enabled is True
    assert cfg.unknown_paper_promotion_enabled is True
    assert cfg.unknown_review_article_enrichment_enabled is True


def test_paper_tp_sl_defaults():
    cfg = Config()
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


def test_load_config_calls_validate():
    from kindshot.config import load_config
    import pytest
    with pytest.raises(ValueError):
        load_config(paper_take_profit_pct=-1.0)
