import shutil
from pathlib import Path

from kindshot.poll_trace import PollTracer


def test_poll_end_writes_watermark_and_raw_time_fields():
    tmp_path = Path(".tmp_poll_trace_test")
    if tmp_path.exists():
        shutil.rmtree(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    tracer = PollTracer(tmp_path)

    t0 = tracer.poll_start(from_time="093500")
    tracer.poll_end(
        t0,
        3,
        raw_count=12,
        seen_dup=4,
        noise_filtered=5,
        last_time_before="093521",
        last_time_after="093612",
        raw_min_time="093400",
        raw_max_time="093612",
    )
    tracer.close()

    trace_files = list(tmp_path.glob("polling_trace_*.jsonl"))
    assert len(trace_files) == 1
    lines = trace_files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert '"from_time": "093500"' in lines[0]
    assert '"last_time_before": "093521"' in lines[1]
    assert '"last_time_after": "093612"' in lines[1]
    assert '"raw_min_time": "093400"' in lines[1]
    assert '"raw_max_time": "093612"' in lines[1]
    shutil.rmtree(tmp_path)
