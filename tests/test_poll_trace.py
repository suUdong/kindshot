"""Tests for poll_trace.py — PollTracer tracing and singleton."""

import json
from pathlib import Path

from kindshot.poll_trace import PollTracer, init_tracer, get_tracer


def test_poll_start_and_end_writes_jsonl(tmp_path):
    tracer = PollTracer(tmp_path)
    t0 = tracer.poll_start(from_time="093500")
    tracer.poll_end(
        t0, 3,
        raw_count=12, seen_dup=4, noise_filtered=5,
        last_time_before="093521", last_time_after="093612",
        raw_min_time="093400", raw_max_time="093612",
    )
    tracer.close()

    files = list(tmp_path.glob("polling_trace_*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    start_rec = json.loads(lines[0])
    assert start_rec["phase"] == "poll_start"
    assert start_rec["from_time"] == "093500"
    assert "ts" in start_rec

    end_rec = json.loads(lines[1])
    assert end_rec["phase"] == "poll_end"
    assert end_rec["items"] == 3
    assert end_rec["raw"] == 12
    assert end_rec["seen_dup"] == 4
    assert end_rec["noise_filtered"] == 5
    assert end_rec["last_time_before"] == "093521"
    assert end_rec["raw_max_time"] == "093612"
    assert end_rec["elapsed_ms"] >= 0


def test_sleep_start_and_end(tmp_path):
    tracer = PollTracer(tmp_path)
    t0 = tracer.sleep_start(3.5)
    tracer.sleep_end(t0)
    tracer.close()

    files = list(tmp_path.glob("polling_trace_*.jsonl"))
    lines = files[0].read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["phase"] == "sleep_start"
    assert json.loads(lines[0])["planned_s"] == 3.5
    assert json.loads(lines[1])["phase"] == "sleep_end"
    assert json.loads(lines[1])["actual_ms"] >= 0


def test_process_start_and_end(tmp_path):
    tracer = PollTracer(tmp_path)
    long_id = "evt_abc1234567890_extra"
    t0 = tracer.process_start(long_id, "005930", "삼성전자 공급계약 체결 — 반도체 대형 계약")
    tracer.process_end(t0, long_id, "BUY")
    tracer.close()

    files = list(tmp_path.glob("polling_trace_*.jsonl"))
    lines = files[0].read_text().strip().splitlines()
    start = json.loads(lines[0])
    assert start["phase"] == "process_start"
    assert start["event_id"] == long_id[:16]  # truncated to 16 chars
    assert start["ticker"] == "005930"
    assert len(start["headline"]) <= 40

    end = json.loads(lines[1])
    assert end["phase"] == "process_end"
    assert end["result"] == "BUY"


def test_llm_start_and_end_with_error(tmp_path):
    tracer = PollTracer(tmp_path)
    t0 = tracer.llm_start("035720")
    tracer.llm_end(t0, "035720", error="timeout")
    tracer.close()

    files = list(tmp_path.glob("polling_trace_*.jsonl"))
    lines = files[0].read_text().strip().splitlines()
    end = json.loads(lines[1])
    assert end["phase"] == "llm_end"
    assert end["error"] == "timeout"
    assert end["ticker"] == "035720"


def test_context_card_tracing(tmp_path):
    tracer = PollTracer(tmp_path)
    t0 = tracer.context_card_start("005930")
    tracer.context_card_end(t0, "005930")
    tracer.close()

    files = list(tmp_path.glob("polling_trace_*.jsonl"))
    lines = files[0].read_text().strip().splitlines()
    assert json.loads(lines[0])["phase"] == "ctx_card_start"
    assert json.loads(lines[1])["phase"] == "ctx_card_end"


def test_queue_put_no_block(tmp_path):
    """queue_put_done only writes if blocked > 100ms."""
    tracer = PollTracer(tmp_path)
    t0 = tracer.queue_put(5, 512)
    tracer.queue_put_done(t0)  # instant — should NOT write blocked record
    tracer.close()

    files = list(tmp_path.glob("polling_trace_*.jsonl"))
    lines = files[0].read_text().strip().splitlines()
    assert len(lines) == 1  # only queue_put, no queue_put_blocked
    assert json.loads(lines[0])["phase"] == "queue_put"
    assert json.loads(lines[0])["qsize"] == 5


def test_init_and_get_tracer(tmp_path):
    tracer = init_tracer(tmp_path)
    assert get_tracer() is tracer
    assert isinstance(tracer, PollTracer)


def test_close_idempotent(tmp_path):
    tracer = PollTracer(tmp_path)
    tracer.poll_start()
    tracer.close()
    tracer.close()  # should not raise
