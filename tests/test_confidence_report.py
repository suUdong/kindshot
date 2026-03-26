from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "confidence_report.py"
    spec = spec_from_file_location("confidence_report", path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_summarize_decision_rows_detects_collapsed_mode():
    mod = _load_module()
    rows = [
        {"type": "decision", "decision_source": "LLM", "action": "SKIP", "confidence": 50},
        {"type": "decision", "decision_source": "LLM", "action": "SKIP", "confidence": 50},
        {"type": "decision", "decision_source": "LLM", "action": "SKIP", "confidence": 50},
        {"type": "decision", "decision_source": "LLM", "action": "SKIP", "confidence": 50},
        {"type": "decision", "decision_source": "LLM", "action": "SKIP", "confidence": 72},
    ]
    summary = mod.summarize_decision_rows(rows)
    assert summary["mode_confidence"] == 50
    assert summary["mode_count"] == 4
    assert round(summary["mode_share"], 2) == 0.80
    assert summary["collapse_flag"] == "collapsed"
    assert summary["band_counts"]["50-59"] == 4
    assert summary["action_band_counts"]["SKIP"]["70-79"] == 1


def test_analyze_log_builds_source_specific_summaries(tmp_path):
    mod = _load_module()
    log_path = tmp_path / "kindshot_20260327.jsonl"
    log_path.write_text(
        "\n".join(
            [
                '{"type":"decision","decision_source":"LLM","action":"SKIP","confidence":50}',
                '{"type":"decision","decision_source":"LLM","action":"BUY","confidence":82}',
                '{"type":"decision","decision_source":"RULE_FALLBACK","action":"BUY","confidence":78}',
            ]
        ),
        encoding="utf-8",
    )
    summary = mod.analyze_log(log_path)
    assert summary["decision_rows"] == 3
    assert summary["overall"]["source_counts"]["LLM"] == 2
    assert summary["by_source"]["LLM"]["decision_count"] == 2
    assert summary["by_source"]["RULE_FALLBACK"]["mode_confidence"] == 78


def test_render_report_includes_comparison_section(tmp_path):
    mod = _load_module()
    cohorts = [
        {
            "path": str(tmp_path / "a.jsonl"),
            "exists": True,
            "line_count": 3,
            "decision_rows": 3,
            "overall": {
                "decision_count": 3,
                "source_counts": {"LLM": 3},
                "top_exact": [(50, 3)],
                "band_counts": {"<50": 0, "50-59": 3, "60-69": 0, "70-79": 0, "80-89": 0, "90+": 0},
                "action_band_counts": {"SKIP": {"<50": 0, "50-59": 3, "60-69": 0, "70-79": 0, "80-89": 0, "90+": 0}},
                "mode_confidence": 50,
                "mode_share": 1.0,
                "collapse_flag": "collapsed",
                "min_confidence": 50,
                "median_confidence": 50,
                "max_confidence": 50,
            },
            "by_source": {
                "LLM": {
                    "decision_count": 3,
                    "mode_confidence": 50,
                    "mode_share": 1.0,
                    "collapse_flag": "collapsed",
                }
            },
        },
        {
            "path": str(tmp_path / "b.jsonl"),
            "exists": True,
            "line_count": 3,
            "decision_rows": 3,
            "overall": {
                "decision_count": 3,
                "source_counts": {"LLM": 2, "RULE_FALLBACK": 1},
                "top_exact": [(82, 1), (78, 1), (64, 1)],
                "band_counts": {"<50": 0, "50-59": 0, "60-69": 1, "70-79": 1, "80-89": 1, "90+": 0},
                "action_band_counts": {"BUY": {"<50": 0, "50-59": 0, "60-69": 1, "70-79": 1, "80-89": 1, "90+": 0}},
                "mode_confidence": 82,
                "mode_share": 1 / 3,
                "collapse_flag": "spread",
                "min_confidence": 64,
                "median_confidence": 78,
                "max_confidence": 82,
            },
            "by_source": {
                "LLM": {
                    "decision_count": 2,
                    "mode_confidence": 82,
                    "mode_share": 0.5,
                    "collapse_flag": "spread",
                }
            },
        },
    ]
    rendered = mod.render_report(cohorts)
    assert "Comparison:" in rendered
    assert "llm_mode=50" in rendered
    assert "llm_flag=collapsed" in rendered


def test_resolve_log_paths_requires_input():
    mod = _load_module()
    try:
        mod.resolve_log_paths(dates=[], log_files=[])
    except ValueError as exc:
        assert "provide at least one" in str(exc)
    else:
        raise AssertionError("expected ValueError")
