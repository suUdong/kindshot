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


def test_summarize_service_infers_paper_mode(monkeypatch):
    mod = _load_server_monitor_module()
    calls = []

    class _Proc:
        def __init__(self, returncode, stdout):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    def _fake_run(cmd, capture_output, text, errors, check):
        calls.append(cmd)
        if cmd[:3] == ["systemctl", "is-active", "kindshot"]:
            return _Proc(0, "active\n")
        if cmd[:3] == ["systemctl", "show", "kindshot"]:
            return _Proc(0, "MainPID=157326\nSubState=running\nActiveEnterTimestamp=Sun 2026-03-29 09:37:41 KST\n")
        if cmd[:2] == ["ps", "-p"]:
            return _Proc(0, "/opt/kindshot/.venv/bin/python -m kindshot --paper\n")
        return _Proc(1, "")

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)

    summary = mod.summarize_service("kindshot")
    assert summary["active_state"] == "active"
    assert summary["sub_state"] == "running"
    assert summary["main_pid"] == 157326
    assert summary["mode"] == "paper"
    assert "--paper" in summary["cmdline"]
    assert calls[0][:3] == ["systemctl", "is-active", "kindshot"]


def test_summarize_health_extracts_compact_fields():
    mod = _load_server_monitor_module()
    summary = mod.summarize_health(
        {
            "status": "healthy",
            "last_poll_age_seconds": 13,
            "events_seen": 2,
            "events_processed": 1,
            "buy_count": 1,
            "skip_count": 0,
            "error_count": 0,
            "llm_calls": 1,
            "kis_calls": 3,
            "guardrail_state": {"position_count": 1, "configured_max_positions": 4},
            "circuit_breaker": {"nvidia_open": False, "anthropic_open": False},
        },
        url="http://127.0.0.1:8080/health",
    )
    assert summary["reachable"] is True
    assert summary["status"] == "healthy"
    assert summary["last_poll_age_seconds"] == 13
    assert summary["position_count"] == 1
    assert summary["configured_max_positions"] == 4
    assert summary["kis_calls"] == 3


def test_render_summary_reports_missing_runtime_log(tmp_path):
    mod = _load_server_monitor_module()
    summary = {
        "date": "20260327",
        "services": {
            "kindshot": {
                "active_state": "active",
                "sub_state": "running",
                "mode": "paper",
                "main_pid": 157326,
                "active_enter_timestamp": "Sun 2026-03-29 09:37:41 KST",
            },
            "kindshot-dashboard": {
                "active_state": "active",
                "sub_state": "running",
                "main_pid": 144309,
            },
        },
        "health": {
            "reachable": True,
            "status": "healthy",
            "last_poll_age_seconds": 13,
            "events_seen": 0,
            "error_count": 0,
            "llm_calls": 0,
            "kis_calls": 0,
            "position_count": 0,
            "configured_max_positions": 4,
            "buy_count": 0,
            "skip_count": 0,
            "nvidia_open": False,
            "anthropic_open": False,
        },
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
    assert "kindshot: active sub=running mode=paper pid=157326" in rendered
    assert "status=healthy last_poll_age_s=13 events_seen=0 errors=0 llm_calls=0 kis_calls=0" in rendered
    assert "runtime_log: missing" in rendered
    assert "raw_items=5" in rendered
    assert "latest_heartbeat: Heartbeat: last_poll=04:30:02, events_seen=2" in rendered
    assert "Verdict: service alive, polling active, no structured runtime log yet" in rendered


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
