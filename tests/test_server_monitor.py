from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_server_monitor_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "server_monitor.py"
    spec = spec_from_file_location("server_monitor", path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_summarize_runtime_log_counts_decisions(tmp_path):
    mod = _load_server_monitor_module()
    log_path = tmp_path / "kindshot_20260327.jsonl"
    log_path.write_text(
        "\n".join(
            [
                '{"type":"event","event_id":"e1"}',
                '{"type":"decision","event_id":"e1","action":"SKIP","decision_source":"LLM"}',
                '{"type":"decision","event_id":"e2","action":"BUY","decision_source":"RULE_FALLBACK"}',
                '{"type":"price_snapshot","event_id":"e1","horizon":"t0"}',
            ]
        ),
        encoding="utf-8",
    )

    summary = mod.summarize_runtime_log(log_path)
    assert summary["exists"] is True
    assert summary["record_types"]["event"] == 1
    assert summary["record_types"]["decision"] == 2
    assert summary["record_types"]["price_snapshot"] == 1
    assert summary["decision_actions"]["BUY"] == 1
    assert summary["decision_actions"]["SKIP"] == 1
    assert summary["decision_sources"]["LLM"] == 1
    assert summary["decision_sources"]["RULE_FALLBACK"] == 1


def test_summarize_poll_trace_tracks_positive_polls(tmp_path):
    mod = _load_server_monitor_module()
    path = tmp_path / "polling_trace_20260327.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"phase":"poll_end","items":0,"ts":"2026-03-27T04:00:00+09:00"}',
                '{"phase":"poll_end","items":2,"raw":40,"last_time_after":"235851","ts":"2026-03-27T04:21:12+09:00"}',
                '{"phase":"poll_end","items":1,"raw":40,"last_time_after":"235851","ts":"2026-03-27T04:28:18+09:00"}',
            ]
        ),
        encoding="utf-8",
    )

    summary = mod.summarize_poll_trace(path)
    assert summary["poll_end_count"] == 3
    assert summary["items_total"] == 3
    assert summary["positive_poll_count"] == 2
    assert summary["last_poll_end_ts"] == "2026-03-27T04:28:18+09:00"
    assert summary["latest_positive_poll"]["items"] == 1


def test_summarize_journal_text_extracts_monitor_signals():
    mod = _load_server_monitor_module()
    text = "\n".join(
        [
            "Mar 27 04:20:18 host python[1]: Heartbeat: last_poll=04:20:06, events_seen=0",
            "Mar 27 04:21:37 host systemd[1]: Started kindshot KRX news-driven trading MVP.",
            "Mar 27 04:22:00 host app[2]: POST https://integrate.api.nvidia.com/v1/chat/completions 200 OK",
            "Mar 27 04:22:30 host systemd[1]: kindshot.service: Failed with result 'timeout'.",
        ]
    )

    summary = mod.summarize_journal_text(text)
    assert summary["line_count"] == 4
    assert summary["nvidia_200"] == 1
    assert summary["service_starts"] == 1
    assert summary["timeout_failures"] == 1
    assert "events_seen=0" in summary["latest_heartbeat"]


def test_render_summary_reports_missing_runtime_log(tmp_path):
    mod = _load_server_monitor_module()
    summary = {
        "date": "20260327",
        "runtime": {"exists": False, "path": str(tmp_path / "kindshot_20260327.jsonl")},
        "polling": {
            "exists": True,
            "path": str(tmp_path / "polling_trace_20260327.jsonl"),
            "size_bytes": 123,
            "mtime": "2026-03-27T04:30:29",
            "poll_end_count": 10,
            "items_total": 5,
            "positive_poll_count": 4,
            "last_poll_end_ts": "2026-03-27T04:30:29+09:00",
            "latest_positive_poll": {"ts": "2026-03-27T04:28:48+09:00", "items": 1, "raw": 40, "last_time_after": "235851"},
        },
        "journal": {
            "line_count": 100,
            "nvidia_200": 0,
            "service_starts": 10,
            "timeout_failures": 0,
            "latest_heartbeat": "Heartbeat: last_poll=04:30:02, events_seen=2",
        },
    }

    rendered = mod.render_summary(summary)
    assert "runtime_log: missing" in rendered
    assert "raw_items=5" in rendered
    assert "latest_heartbeat: Heartbeat: last_poll=04:30:02, events_seen=2" in rendered
    assert "Verdict: polling active but no structured runtime log yet" in rendered


def test_journal_text_falls_back_to_sudo_without_stderr_noise(monkeypatch):
    mod = _load_server_monitor_module()
    calls = []

    class _Proc:
        def __init__(self, returncode, stdout):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = "ignored"

    def _fake_run(cmd, capture_output, text, errors, check):
        calls.append(cmd)
        if cmd[0] == "journalctl":
            return _Proc(1, "")
        return _Proc(0, "heartbeat line")

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)

    text = mod._journal_text("20260327")
    assert text == "heartbeat line"
    assert calls[0][0] == "journalctl"
    assert calls[1][0] == "sudo"
