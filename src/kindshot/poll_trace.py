"""Polling trace logger — 폴링 지연 원인 추적용 별도 JSONL 파일."""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))


class PollTracer:
    """폴링/파이프라인 타이밍을 별도 JSONL 파일에 기록."""

    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir
        self._fh = None
        self._current_date: Optional[str] = None

    def _ensure_file(self) -> None:
        today = datetime.now(_KST).strftime("%Y%m%d")
        if today != self._current_date:
            if self._fh:
                self._fh.close()
            path = self._log_dir / f"polling_trace_{today}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(path, "a", encoding="utf-8", buffering=1)  # line-buffered
            self._current_date = today

    def _write(self, record: dict[str, Any]) -> None:
        try:
            self._ensure_file()
            record["ts"] = datetime.now(_KST).isoformat(timespec="milliseconds")
            self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            logger.debug("poll_trace write failed", exc_info=True)

    def poll_start(self, from_time: str = "") -> float:
        """poll_once 시작. 반환값을 poll_end에 전달."""
        t = time.monotonic()
        self._write({"phase": "poll_start", "from_time": from_time})
        return t

    def poll_end(
        self,
        t_start: float,
        item_count: int,
        error: Optional[str] = None,
        raw_count: Optional[int] = None,
        seen_dup: int = 0,
        noise_filtered: int = 0,
    ) -> None:
        elapsed_ms = (time.monotonic() - t_start) * 1000
        rec: dict[str, Any] = {
            "phase": "poll_end",
            "elapsed_ms": round(elapsed_ms, 1),
            "items": item_count,
            "error": error,
        }
        if raw_count is not None:
            rec["raw"] = raw_count
        if seen_dup:
            rec["seen_dup"] = seen_dup
        if noise_filtered:
            rec["noise_filtered"] = noise_filtered
        self._write(rec)

    def sleep_start(self, interval_s: float) -> float:
        t = time.monotonic()
        self._write({"phase": "sleep_start", "planned_s": round(interval_s, 2)})
        return t

    def sleep_end(self, t_start: float) -> None:
        elapsed_ms = (time.monotonic() - t_start) * 1000
        self._write({"phase": "sleep_end", "actual_ms": round(elapsed_ms, 1)})

    def queue_put(self, queue_size: int, maxsize: int) -> float:
        t = time.monotonic()
        self._write({"phase": "queue_put", "qsize": queue_size, "maxsize": maxsize})
        return t

    def queue_put_done(self, t_start: float) -> None:
        elapsed_ms = (time.monotonic() - t_start) * 1000
        if elapsed_ms > 100:  # 100ms 이상 블로킹만 기록
            self._write({"phase": "queue_put_blocked", "blocked_ms": round(elapsed_ms, 1)})

    def process_start(self, event_id: str, ticker: str, headline: str) -> float:
        t = time.monotonic()
        self._write({
            "phase": "process_start",
            "event_id": event_id[:16],
            "ticker": ticker,
            "headline": headline[:40],
        })
        return t

    def context_card_start(self, ticker: str) -> float:
        t = time.monotonic()
        self._write({"phase": "ctx_card_start", "ticker": ticker})
        return t

    def context_card_end(self, t_start: float, ticker: str) -> None:
        elapsed_ms = (time.monotonic() - t_start) * 1000
        self._write({"phase": "ctx_card_end", "ticker": ticker, "elapsed_ms": round(elapsed_ms, 1)})

    def llm_start(self, ticker: str) -> float:
        t = time.monotonic()
        self._write({"phase": "llm_start", "ticker": ticker})
        return t

    def llm_end(self, t_start: float, ticker: str, error: Optional[str] = None) -> None:
        elapsed_ms = (time.monotonic() - t_start) * 1000
        self._write({
            "phase": "llm_end",
            "ticker": ticker,
            "elapsed_ms": round(elapsed_ms, 1),
            "error": error,
        })

    def process_end(self, t_start: float, event_id: str, result: str) -> None:
        elapsed_ms = (time.monotonic() - t_start) * 1000
        self._write({
            "phase": "process_end",
            "event_id": event_id[:16],
            "elapsed_ms": round(elapsed_ms, 1),
            "result": result,
        })

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


# 모듈 싱글턴 — None이면 트레이싱 비활성
_tracer: Optional[PollTracer] = None


def init_tracer(log_dir: Path) -> PollTracer:
    global _tracer
    _tracer = PollTracer(log_dir)
    return _tracer


def get_tracer() -> Optional[PollTracer]:
    return _tracer
