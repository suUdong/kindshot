from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "trading_log_report.py"
    spec = spec_from_file_location("trading_log_report", path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_analyze_log_collects_decision_and_inline_stats(tmp_path):
    mod = _load_module()
    log_path = tmp_path / "kindshot_20260327.jsonl"
    log_path.write_text(
        "\n".join(
            [
                '{"type":"event","event_id":"e1","decision_action":"BUY","guardrail_reason":"LOW_CONFIDENCE"}',
                '{"type":"event","event_id":"e2","decision_action":"SKIP"}',
                '{"type":"decision","event_id":"e1","action":"SKIP","decision_source":"LLM","reason":"too weak","decided_at":"2026-03-27T04:30:00+09:00"}',
                '{"type":"decision","event_id":"e2","action":"BUY","decision_source":"RULE_FALLBACK","reason":"fallback pass","decided_at":"2026-03-27T05:30:00+09:00"}',
                '{"type":"price_snapshot","event_id":"e1","horizon":"t0"}',
            ]
        ),
        encoding="utf-8",
    )

    summary = mod.analyze_log(log_path)
    assert summary["record_types"]["event"] == 2
    assert summary["record_types"]["decision"] == 2
    assert summary["decision_actions"]["BUY"] == 1
    assert summary["decision_actions"]["SKIP"] == 1
    assert summary["decision_sources"]["LLM"] == 1
    assert summary["decision_sources"]["RULE_FALLBACK"] == 1
    assert summary["inline_actions"]["BUY"] == 1
    assert summary["inline_actions"]["SKIP"] == 1
    assert summary["buy_guardrails"]["LOW_CONFIDENCE"] == 1
    assert summary["hour_source"]["04"]["LLM"] == 1
    assert summary["hour_source"]["05"]["RULE_FALLBACK"] == 1


def test_analyze_log_converts_utc_decision_hours_to_kst(tmp_path):
    mod = _load_module()
    log_path = tmp_path / "kindshot_20260327.jsonl"
    log_path.write_text(
        "\n".join(
            [
                '{"type":"decision","event_id":"e1","action":"SKIP","decision_source":"LLM","reason":"utc ts","decided_at":"2026-03-26T02:04:40Z"}',
            ]
        ),
        encoding="utf-8",
    )

    summary = mod.analyze_log(log_path)
    assert summary["hour_source"]["11"]["LLM"] == 1


def test_render_report_includes_sections(tmp_path):
    mod = _load_module()
    summary = {
        "path": str(tmp_path / "kindshot_20260327.jsonl"),
        "exists": True,
        "size_bytes": 123,
        "line_count": 5,
        "record_types": {"event": 2, "decision": 2, "price_snapshot": 1},
        "decision_actions": {"BUY": 1, "SKIP": 1},
        "decision_sources": {"LLM": 1, "RULE_FALLBACK": 1},
        "inline_actions": {"BUY": 1, "SKIP": 1},
        "buy_guardrails": {"LOW_CONFIDENCE": 1},
        "hour_source": {"04": {"LLM": 1}, "05": {"RULE_FALLBACK": 1}},
        "source_reasons": {"LLM": {"too weak": 1}},
    }

    rendered = mod.render_report(summary)
    assert "Structured Decisions:" in rendered
    assert "Inline Intent:" in rendered
    assert "Time Of Day:" in rendered
    assert "Top Reasons:" in rendered
    assert "Bottom Line: mixed" in rendered


def test_summarize_verdict_handles_no_buys():
    mod = _load_module()
    summary = {"decision_actions": {"SKIP": 5}}
    assert mod.summarize_verdict(summary) == "fully defensive"


def test_resolve_log_path_prefers_explicit_file(tmp_path):
    mod = _load_module()
    path = tmp_path / "sample.jsonl"
    resolved = mod.resolve_log_path(date_str=None, log_file=str(path))
    assert resolved == path.resolve()
