from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "auto_tune_strategy.py"
    spec = spec_from_file_location("auto_tune_strategy", path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_derive_recommendations_uses_best_exit_candidate():
    mod = _load_module()
    analysis = {
        "runtime_defaults": {
            "paper_take_profit_pct": 2.0,
            "paper_stop_loss_pct": -1.5,
            "trailing_stop_activation_pct": 0.5,
            "trailing_stop_early_pct": 0.5,
            "trailing_stop_mid_pct": 0.8,
            "trailing_stop_late_pct": 1.0,
            "max_hold_minutes": 15,
            "t5m_loss_exit_enabled": True,
            "opening_min_confidence": 82,
            "afternoon_min_confidence": 80,
            "closing_min_confidence": 85,
            "fast_profile_hold_minutes": 20,
            "fast_profile_no_buy_after_kst_hour": 14,
        },
        "total_trades": 4,
        "win_rate": 50.0,
        "total_pnl_pct": 1.2,
        "trade_rows": [
            {"confidence": 84, "exit_pnl_pct": 2.1, "detected_at": "2026-03-27T08:45:00+09:00", "hold_profile_minutes": 20},
            {"confidence": 78, "exit_pnl_pct": -0.8, "detected_at": "2026-03-27T09:10:00+09:00", "hold_profile_minutes": 20},
            {"confidence": 88, "exit_pnl_pct": 1.5, "detected_at": "2026-03-27T13:20:00+09:00", "hold_profile_minutes": 30},
            {"confidence": 86, "exit_pnl_pct": -0.4, "detected_at": "2026-03-27T14:40:00+09:00", "hold_profile_minutes": 20},
        ],
        "condition_scores": {
            "exit": {
                "candidates": [
                    {
                        "params": {
                            "paper_take_profit_pct": 2.5,
                            "paper_stop_loss_pct": -2.0,
                            "trailing_stop_activation_pct": 0.8,
                            "trailing_stop_early_pct": 0.6,
                            "trailing_stop_mid_pct": 0.8,
                            "trailing_stop_late_pct": 1.0,
                            "max_hold_minutes": 20,
                            "t5m_loss_exit_enabled": True,
                        },
                        "score": 1.23,
                        "total_pnl": 2.4,
                    }
                ]
            }
        },
    }

    rec = mod.derive_recommendations(analysis)
    assert rec["recommended_params"]["PAPER_TAKE_PROFIT_PCT"] == 2.5
    assert rec["recommended_params"]["PAPER_STOP_LOSS_PCT"] == -2.0
    assert "Exit parameters follow the top-ranked simulation candidate" in rec["rationale"][-1]
    assert "export PAPER_TAKE_PROFIT_PCT=2.5" in rec["env_block"]


def test_parse_kst_normalizes_utc_to_kst():
    mod = _load_module()
    dt = mod._parse_kst("2026-03-26T23:45:00Z")
    assert dt is not None
    assert dt.hour == 8
    assert dt.minute == 45


def test_fast_profile_cutoff_uses_exact_fast_profile_hold_minutes():
    mod = _load_module()
    analysis = {
        "runtime_defaults": {
            "paper_take_profit_pct": 2.0,
            "paper_stop_loss_pct": -1.5,
            "trailing_stop_activation_pct": 0.5,
            "trailing_stop_early_pct": 0.5,
            "trailing_stop_mid_pct": 0.8,
            "trailing_stop_late_pct": 1.0,
            "max_hold_minutes": 15,
            "t5m_loss_exit_enabled": True,
            "opening_min_confidence": 82,
            "afternoon_min_confidence": 80,
            "closing_min_confidence": 85,
            "fast_profile_hold_minutes": 20,
            "fast_profile_no_buy_after_kst_hour": 14,
        },
        "trade_rows": [
            {"confidence": 84, "exit_pnl_pct": -1.0, "detected_at": "2026-03-27T14:10:00+09:00", "hold_profile_minutes": 15},
            {"confidence": 84, "exit_pnl_pct": 1.2, "detected_at": "2026-03-27T13:00:00+09:00", "hold_profile_minutes": 20},
            {"confidence": 84, "exit_pnl_pct": -0.9, "detected_at": "2026-03-27T15:10:00+09:00", "hold_profile_minutes": 20},
            {"confidence": 84, "exit_pnl_pct": 0.4, "detected_at": "2026-03-27T14:20:00+09:00", "hold_profile_minutes": 30},
        ],
        "condition_scores": {"exit": {"candidates": [{"params": {"paper_take_profit_pct": 2.0, "paper_stop_loss_pct": -1.5, "trailing_stop_activation_pct": 0.5, "trailing_stop_early_pct": 0.5, "trailing_stop_mid_pct": 0.8, "trailing_stop_late_pct": 1.0, "max_hold_minutes": 15, "t5m_loss_exit_enabled": True}, "score": 0.0, "total_pnl": -0.3}]}},
    }

    rec = mod.derive_recommendations(analysis)
    assert rec["recommended_params"]["FAST_PROFILE_NO_BUY_AFTER_KST_HOUR"] == 14
    assert rec["evidence"]["fast_profile_cutoff"]["filtered_count"] == 1.0


def test_render_text_includes_env_block(tmp_path):
    mod = _load_module()
    rec = {
        "recommended_params": {"MIN_BUY_CONFIDENCE": 80},
        "rationale": ["example rationale"],
        "env_block": "export MIN_BUY_CONFIDENCE=80",
    }
    rendered = mod.render_text(rec, tmp_path / "analysis.json")
    assert "Recommended params:" in rendered
    assert "Rationale:" in rendered
    assert "export MIN_BUY_CONFIDENCE=80" in rendered
