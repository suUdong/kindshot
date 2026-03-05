"""Tests for guardrails interface (MVP stub)."""

from kindshot.guardrails import check_guardrails, GuardrailResult


def test_mvp_always_passes():
    """MVP stub: guardrails always pass."""
    r = check_guardrails(
        ticker="005930",
        spread_bps=10.0,
        adv_value_20d=10e9,
        ret_today=5.0,
    )
    assert r.passed is True
    assert r.reason is None


def test_interface_exists():
    """Ensure the function signature matches expected interface."""
    r = check_guardrails(
        ticker="005930",
        spread_bps=None,
        adv_value_20d=None,
        ret_today=None,
    )
    assert isinstance(r, GuardrailResult)
