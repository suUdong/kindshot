from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sys


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "llm_prompt_eval.py"
    spec = spec_from_file_location("llm_prompt_eval", path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_score_predictions_reports_accuracy_and_calibration():
    mod = _load_module()
    cases = [
        mod.EvalCase(
            event_id="e1",
            date="20260327",
            ticker="111111",
            corp_name="A",
            headline="A 공급계약 체결",
            bucket="POS_STRONG",
            keyword_hits=["공급계약"],
            dorg="",
            detected_at="2026-03-27T09:10:00+09:00",
            detected_hhmmss="09:10:00",
            ctx=mod.ContextCard(),
            market_ctx=mod.MarketContext(),
            hold_minutes=20,
            target_action="BUY",
            exit_pnl_pct=0.8,
            historical_action="BUY",
            historical_confidence=82,
            historical_reason="test",
            historical_source="LLM",
        ),
        mod.EvalCase(
            event_id="e2",
            date="20260327",
            ticker="222222",
            corp_name="B",
            headline="B 전망 기사",
            bucket="POS_WEAK",
            keyword_hits=[],
            dorg="",
            detected_at="2026-03-27T11:30:00+09:00",
            detected_hhmmss="11:30:00",
            ctx=mod.ContextCard(),
            market_ctx=mod.MarketContext(),
            hold_minutes=20,
            target_action="SKIP",
            exit_pnl_pct=-0.2,
            historical_action="SKIP",
            historical_confidence=58,
            historical_reason="test",
            historical_source="LLM",
        ),
    ]
    predictions = [
        mod.EvalPrediction(event_id="e1", action="BUY", confidence=84, size_hint="M", reason="ok", source="LLM"),
        mod.EvalPrediction(event_id="e2", action="BUY", confidence=78, size_hint="S", reason="bad", source="LLM"),
    ]

    metrics = mod.score_predictions(cases, predictions)

    assert metrics["case_count"] == 2
    assert metrics["accuracy"] == 0.5
    assert metrics["buy_precision"] == 0.5
    assert metrics["buy_recall"] == 1.0
    assert metrics["false_negative_rate"] == 0.0
    assert "70-79" in metrics["confidence_calibration"]
    assert "80-89" in metrics["confidence_calibration"]


def test_build_eval_cases_derives_hindsight_label(tmp_path):
    mod = _load_module()
    log_path = tmp_path / "kindshot_20260327.jsonl"
    log_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "event",
                        "event_id": "e1",
                        "ticker": "111111",
                        "corp_name": "A",
                        "headline": "A 공급계약 체결",
                        "bucket": "POS_STRONG",
                        "keyword_hits": ["공급계약"],
                        "dorg": "",
                        "detected_at": "2026-03-27T09:10:00+09:00",
                        "ctx": {"adv_value_20d": 10_000_000_000, "spread_bps": 10.0},
                        "market_ctx": {},
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "type": "decision",
                        "event_id": "e1",
                        "action": "BUY",
                        "confidence": 82,
                        "size_hint": "M",
                        "reason": "test",
                        "decision_source": "LLM",
                    },
                    ensure_ascii=False,
                ),
                json.dumps({"type": "price_snapshot", "event_id": "e1", "horizon": "t0", "px": 100.0}, ensure_ascii=False),
                json.dumps({"type": "price_snapshot", "event_id": "e1", "horizon": "t+5m", "px": 102.0}, ensure_ascii=False),
            ]
        ),
        encoding="utf-8",
    )

    cases = mod.build_eval_cases(
        [log_path],
        context_dir=tmp_path / "context_cards",
        runtime_defaults=mod.ExitSimulationConfig(),
    )

    assert len(cases) == 1
    assert cases[0].target_action == "BUY"
    assert cases[0].hold_minutes == 20
