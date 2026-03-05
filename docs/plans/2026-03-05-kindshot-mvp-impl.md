# kindshot MVP Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a KRX news-driven day trading MVP that polls KIND disclosures, classifies them into 5 buckets, applies quant filters, calls LLM for BUY/SKIP decisions on POS_STRONG events, and logs everything as JSONL for post-hoc simulation.

**Architecture:** Pipeline of async components: feed → event_registry → bucket → quant → decision → logger, with a separate price snapshot scheduler. All components communicate via function calls in a single asyncio event loop. JSONL append-only logging with 3 record types (event, decision, price_snapshot).

**Tech Stack:** Python 3.11+, asyncio, aiohttp, feedparser, anthropic SDK, pykrx, Pydantic v2

---

## Phase 1: Foundation (4 files)

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `src/kindshot/__init__.py`
- Create: `src/kindshot/__main__.py`

**Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "kindshot"
version = "0.1.2"
description = "KRX news-driven day trading MVP"
requires-python = ">=3.11"
dependencies = [
    "aiohttp>=3.9",
    "feedparser>=6.0",
    "anthropic>=0.40",
    "pykrx>=1.0",
    "pydantic>=2.0",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "aioresponses>=0.7",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

**Step 2: Create .env.example**

```env
# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# KIS (optional - price fetching disabled if missing)
KIS_APP_KEY=
KIS_APP_SECRET=
KIS_ACCOUNT_NO=
KIS_IS_PAPER=true

# Overrides
# ADV_THRESHOLD=5000000000
# SPREAD_CHECK_ENABLED=false
# LOG_DIR=logs
```

**Step 3: Create .gitignore**

```gitignore
__pycache__/
*.pyc
.env
logs/
*.egg-info/
dist/
.venv/
.pytest_cache/
```

**Step 4: Create src/kindshot/__init__.py**

```python
"""kindshot — KRX news-driven day trading MVP."""

__version__ = "0.1.2"
```

**Step 5: Create src/kindshot/__main__.py**

```python
"""Entry point for `python -m kindshot`."""

import asyncio
from kindshot.main import run

asyncio.run(run())
```

**Step 6: Install in dev mode and verify**

Run: `pip install -e ".[dev]"`
Run: `python -c "import kindshot; print(kindshot.__version__)"`
Expected: `0.1.2`

**Step 7: Commit**

```bash
git add pyproject.toml .env.example .gitignore src/
git commit -m "feat: project scaffolding with dependencies"
```

---

### Task 2: config.py — Settings and constants

**Files:**
- Create: `src/kindshot/config.py`

**Step 1: Write config.py**

```python
"""Configuration constants and environment loading."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_bool(key: str, default: bool = False) -> bool:
    return _env(key, str(default)).lower() in ("true", "1", "yes")


def _env_int(key: str, default: int = 0) -> int:
    v = _env(key, "")
    return int(v) if v else default


def _env_float(key: str, default: float = 0.0) -> float:
    v = _env(key, "")
    return float(v) if v else default


@dataclass(frozen=True)
class Config:
    # --- Anthropic ---
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "claude-haiku-4-5-20251001"))
    llm_sdk_timeout_s: float = 3.0
    llm_wait_for_s: float = 2.0
    llm_cache_ttl_s: float = 60.0
    llm_cache_sweep_s: float = 300.0

    # --- KIS ---
    kis_app_key: str = field(default_factory=lambda: _env("KIS_APP_KEY"))
    kis_app_secret: str = field(default_factory=lambda: _env("KIS_APP_SECRET"))
    kis_account_no: str = field(default_factory=lambda: _env("KIS_ACCOUNT_NO"))
    kis_is_paper: bool = field(default_factory=lambda: _env_bool("KIS_IS_PAPER", True))

    # --- Feed ---
    kind_rss_url: str = "https://kind.krx.co.kr/disclosure/todaydisclosure.do?method=searchTodayDisclosureRSS"
    feed_interval_market_s: float = field(default_factory=lambda: _env_float("FEED_INTERVAL_MARKET", 3.0))
    feed_interval_off_s: float = field(default_factory=lambda: _env_float("FEED_INTERVAL_OFF", 15.0))
    feed_jitter_pct: float = 0.20
    feed_backoff_threshold: int = 3
    feed_backoff_max_s: float = 60.0

    # --- Quant thresholds ---
    adv_threshold: float = field(default_factory=lambda: _env_float("ADV_THRESHOLD", 5_000_000_000))
    spread_bps_limit: float = 25.0
    extreme_move_pct: float = 20.0
    spread_check_enabled: bool = field(default_factory=lambda: _env_bool("SPREAD_CHECK_ENABLED", False))
    quant_fail_sample_rate: float = 0.10

    # --- Market ---
    kospi_halt_pct: float = -1.0

    # --- Price snapshots ---
    snapshot_horizons: tuple[str, ...] = ("t0", "t+1m", "t+5m", "t+30m", "close")
    close_snapshot_delay_s: float = 300.0  # 15:31~15:35

    # --- Logging ---
    log_dir: Path = field(default_factory=lambda: Path(_env("LOG_DIR", "logs")))
    schema_version: str = "0.1.2"

    # --- Runtime ---
    dry_run: bool = False

    @property
    def kis_enabled(self) -> bool:
        return bool(self.kis_app_key and self.kis_app_secret)


def load_config(**overrides: object) -> Config:
    return Config(**overrides)  # type: ignore[arg-type]
```

**Step 2: Commit**

```bash
git add src/kindshot/config.py
git commit -m "feat: config.py with all MVP thresholds and env loading"
```

---

### Task 3: models.py — Pydantic models and enums

**Files:**
- Create: `src/kindshot/models.py`

**Step 1: Write models.py**

```python
"""Pydantic models and enums for kindshot log records."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────

class Bucket(str, Enum):
    POS_STRONG = "POS_STRONG"
    POS_WEAK = "POS_WEAK"
    NEG_STRONG = "NEG_STRONG"
    NEG_WEAK = "NEG_WEAK"
    UNKNOWN = "UNKNOWN"


class EventKind(str, Enum):
    ORIGINAL = "ORIGINAL"
    CORRECTION = "CORRECTION"
    WITHDRAWAL = "WITHDRAWAL"


class Action(str, Enum):
    BUY = "BUY"
    SKIP = "SKIP"


class SizeHint(str, Enum):
    S = "S"
    M = "M"
    L = "L"


class SkipStage(str, Enum):
    DUPLICATE = "DUPLICATE"
    BUCKET = "BUCKET"
    QUANT = "QUANT"
    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_PARSE = "LLM_PARSE"
    GUARDRAIL = "GUARDRAIL"


class T0Basis(str, Enum):
    DECIDED_AT = "DECIDED_AT"
    DETECTED_AT = "DETECTED_AT"


class EventIdMethod(str, Enum):
    UID = "UID"
    FALLBACK = "FALLBACK"


class ParentMatchMethod(str, Enum):
    EXACT_TITLE = "EXACT_TITLE"
    FUZZY_TITLE = "FUZZY_TITLE"
    NONE = "NONE"


# ── Sub-models ─────────────────────────────────────────

class QuantCheckDetail(BaseModel):
    adv_value_20d_ok: bool
    spread_bps_ok: bool
    extreme_move_ok: bool


class ContextCard(BaseModel):
    ret_today: Optional[float] = None
    ret_1d: Optional[float] = None
    ret_3d: Optional[float] = None
    pos_20d: Optional[float] = None
    gap: Optional[float] = None
    adv_value_20d: Optional[float] = None
    spread_bps: Optional[float] = None
    vol_pct_20d: Optional[float] = None


# ── Log Records ────────────────────────────────────────

class EventRecord(BaseModel):
    type: str = "event"
    schema_version: str
    run_id: str
    event_id: str
    event_id_method: EventIdMethod
    event_kind: EventKind = EventKind.ORIGINAL
    parent_id: Optional[str] = None
    event_group_id: str
    parent_match_method: Optional[ParentMatchMethod] = None
    parent_match_score: Optional[float] = None
    parent_candidate_count: Optional[int] = None
    source: str = "KIND"
    rss_guid: Optional[str] = None
    rss_link: Optional[str] = None
    kind_uid: Optional[str] = None
    disclosed_at: Optional[datetime] = None
    disclosed_at_missing: bool = False
    detected_at: datetime
    delay_ms: Optional[int] = None
    ticker: str
    corp_name: str
    headline: str
    bucket: Bucket
    keyword_hits: list[str] = Field(default_factory=list)
    analysis_tag: Optional[str] = None
    skip_stage: Optional[SkipStage] = None
    skip_reason: Optional[str] = None
    quant_check_passed: Optional[bool] = None
    quant_check_detail: Optional[QuantCheckDetail] = None
    ctx: Optional[ContextCard] = None


class DecisionRecord(BaseModel):
    type: str = "decision"
    schema_version: str
    run_id: str
    event_id: str
    decided_at: datetime
    llm_model: str
    llm_latency_ms: int
    action: Action
    confidence: int = Field(ge=0, le=100)
    size_hint: SizeHint
    reason: str
    decision_source: str = "LLM"  # "LLM" | "CACHE"


class PriceSnapshot(BaseModel):
    type: str = "price_snapshot"
    schema_version: str
    run_id: str
    event_id: str
    horizon: str
    ts: datetime
    t0_basis: T0Basis
    t0_ts: datetime
    px: Optional[float] = None
    cum_value: Optional[float] = None
    ret_long_vs_t0: Optional[float] = None
    ret_short_vs_t0: Optional[float] = None
    value_since_t0: Optional[float] = None
    spread_bps: Optional[float] = None
    price_source: Optional[str] = None
    snapshot_fetch_latency_ms: Optional[int] = None
```

**Step 2: Commit**

```bash
git add src/kindshot/models.py
git commit -m "feat: Pydantic models and enums for all log record types"
```

---

### Task 4: logger.py — JSONL append logger + tests

**Files:**
- Create: `src/kindshot/logger.py`
- Create: `tests/__init__.py`
- Create: `tests/test_logger.py`

**Step 1: Write test_logger.py**

```python
"""Tests for JSONL logger."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kindshot.logger import JsonlLogger
from kindshot.models import EventRecord, Bucket, EventIdMethod


def _make_event(event_id: str = "test123") -> EventRecord:
    return EventRecord(
        schema_version="0.1.2",
        run_id="run_test",
        event_id=event_id,
        event_id_method=EventIdMethod.UID,
        event_group_id=event_id,
        detected_at=datetime.now(timezone.utc),
        ticker="005930",
        corp_name="삼성전자",
        headline="테스트 공시",
        bucket=Bucket.POS_STRONG,
    )


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    return tmp_path / "logs"


async def test_write_and_read_back(log_dir: Path):
    logger = JsonlLogger(log_dir, run_id="run_test")
    event = _make_event()
    await logger.write(event)

    files = list(log_dir.glob("*.jsonl"))
    assert len(files) == 1

    line = files[0].read_text(encoding="utf-8").strip()
    data = json.loads(line)
    assert data["type"] == "event"
    assert data["event_id"] == "test123"
    assert data["ticker"] == "005930"


async def test_multiple_writes_append(log_dir: Path):
    logger = JsonlLogger(log_dir, run_id="run_test")
    await logger.write(_make_event("a"))
    await logger.write(_make_event("b"))

    files = list(log_dir.glob("*.jsonl"))
    lines = files[0].read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2


async def test_daily_rotation(log_dir: Path):
    logger = JsonlLogger(log_dir, run_id="run_test")
    await logger.write(_make_event())
    # File name should contain today's date
    files = list(log_dir.glob("*.jsonl"))
    assert len(files) == 1
    today = datetime.now().strftime("%Y%m%d")
    assert today in files[0].name
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_logger.py -v`
Expected: FAIL (kindshot.logger not found)

**Step 3: Write logger.py**

```python
"""JSONL append-only logger with asyncio safety."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel


class JsonlLogger:
    """Append-only JSONL logger. Thread-safe via asyncio.Lock + to_thread."""

    def __init__(self, log_dir: Path, run_id: str) -> None:
        self._log_dir = log_dir
        self._run_id = run_id
        self._lock = asyncio.Lock()
        self._log_dir.mkdir(parents=True, exist_ok=True)

    def _today_file(self) -> Path:
        today = datetime.now().strftime("%Y%m%d")
        return self._log_dir / f"kindshot_{today}.jsonl"

    def _write_sync(self, line: str) -> None:
        path = self._today_file()
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    async def write(self, record: BaseModel) -> None:
        line = record.model_dump_json(exclude_none=False)
        async with self._lock:
            await asyncio.to_thread(self._write_sync, line)
```

**Step 4: Run tests**

Run: `pytest tests/test_logger.py -v`
Expected: all 3 PASS

**Step 5: Commit**

```bash
git add src/kindshot/logger.py tests/
git commit -m "feat: JSONL logger with async lock and daily rotation"
```

---

## Phase 2: Event Pipe (4 files)

### Task 5: feed.py — KIND RSS adaptive polling

**Files:**
- Create: `src/kindshot/feed.py`

**Step 1: Write feed.py**

```python
"""KIND RSS adaptive polling with ETag, jitter, and backoff."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

import aiohttp
import feedparser

from kindshot.config import Config

logger = logging.getLogger(__name__)


@dataclass
class RawDisclosure:
    """Parsed RSS item before event_id generation."""

    title: str
    link: str
    rss_guid: Optional[str]
    published: Optional[str]  # raw pubDate string
    ticker: str
    corp_name: str
    detected_at: datetime


def _extract_ticker_corp(title: str) -> tuple[str, str]:
    """Extract ticker and corp name from KIND title format.

    KIND titles often look like: "삼성전자(005930) - 공급계약 체결"
    or the ticker/corp may be in other fields. This is a best-effort parse.
    """
    # Pattern: 회사명(종목코드)
    m = re.search(r"(.+?)\((\d{6})\)", title)
    if m:
        return m.group(2), m.group(1).strip()
    return "", ""


def _extract_kind_uid(link: str) -> Optional[str]:
    """Extract unique ID from KIND link URL."""
    # e.g. rcpNo=20260305000123 or similar param
    m = re.search(r"rcpNo=(\d+)", link)
    if m:
        return m.group(1)
    # Try generic unique-ish path segment
    m = re.search(r"/(\d{14,20})", link)
    if m:
        return m.group(1)
    return None


class KindFeed:
    """Adaptive KIND RSS poller."""

    def __init__(self, config: Config, session: aiohttp.ClientSession) -> None:
        self._config = config
        self._session = session
        self._etag: Optional[str] = None
        self._last_modified: Optional[str] = None
        self._consecutive_failures = 0

    def _is_market_hours(self) -> bool:
        from datetime import time as dt_time
        now = datetime.now().time()
        return dt_time(9, 0) <= now <= dt_time(15, 30)

    def _base_interval(self) -> float:
        if self._is_market_hours():
            return self._config.feed_interval_market_s
        return self._config.feed_interval_off_s

    def _interval_with_backoff(self) -> float:
        base = self._base_interval()
        if self._consecutive_failures >= self._config.feed_backoff_threshold:
            multiplier = 2 ** (self._consecutive_failures - self._config.feed_backoff_threshold + 1)
            base = min(base * multiplier, self._config.feed_backoff_max_s)
        # jitter ±20%
        jitter = base * self._config.feed_jitter_pct
        return base + random.uniform(-jitter, jitter)

    async def poll_once(self) -> list[RawDisclosure]:
        """Single poll. Returns list of new disclosures (may be empty on 304)."""
        headers: dict[str, str] = {}
        if self._etag:
            headers["If-None-Match"] = self._etag
        if self._last_modified:
            headers["If-Modified-Since"] = self._last_modified

        try:
            async with self._session.get(
                self._config.kind_rss_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 304:
                    self._consecutive_failures = 0
                    return []

                if resp.status != 200:
                    self._consecutive_failures += 1
                    logger.warning("KIND RSS status=%d", resp.status)
                    return []

                self._etag = resp.headers.get("ETag")
                self._last_modified = resp.headers.get("Last-Modified")
                self._consecutive_failures = 0

                body = await resp.text()

        except Exception:
            self._consecutive_failures += 1
            logger.exception("KIND RSS fetch error")
            return []

        feed = feedparser.parse(body)
        now = datetime.now(timezone.utc)
        results: list[RawDisclosure] = []

        for entry in feed.entries:
            title = getattr(entry, "title", "")
            link = getattr(entry, "link", "")
            guid = getattr(entry, "id", None)
            published = getattr(entry, "published", None)
            ticker, corp = _extract_ticker_corp(title)

            results.append(
                RawDisclosure(
                    title=title,
                    link=link,
                    rss_guid=guid,
                    published=published,
                    ticker=ticker,
                    corp_name=corp,
                    detected_at=now,
                )
            )

        return results

    async def stream(self) -> AsyncIterator[list[RawDisclosure]]:
        """Infinite polling loop yielding batches of disclosures."""
        while True:
            items = await self.poll_once()
            if items:
                yield items
            await asyncio.sleep(self._interval_with_backoff())
```

**Step 2: Commit**

```bash
git add src/kindshot/feed.py
git commit -m "feat: KIND RSS adaptive polling with ETag, jitter, backoff"
```

---

### Task 6: event_registry.py — Dedup + correction linking + tests

**Files:**
- Create: `src/kindshot/event_registry.py`
- Create: `tests/test_event_registry.py`

**Step 1: Write test_event_registry.py**

```python
"""Tests for event registry: dedup, event_id, correction linking."""

from datetime import datetime, timezone

import pytest

from kindshot.event_registry import EventRegistry
from kindshot.feed import RawDisclosure
from kindshot.models import EventKind


def _raw(title: str = "삼성전자(005930) - 공급계약 체결", link: str = "https://kind.krx.co.kr/?rcpNo=20260305000001", guid: str = "guid1") -> RawDisclosure:
    return RawDisclosure(
        title=title,
        link=link,
        rss_guid=guid,
        published="2026-03-05T09:12:04+09:00",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=datetime.now(timezone.utc),
    )


def test_dedup_same_uid():
    reg = EventRegistry()
    r1 = _raw()
    e1 = reg.process(r1)
    assert e1 is not None
    e2 = reg.process(r1)
    assert e2 is None  # duplicate


def test_different_uid():
    reg = EventRegistry()
    r1 = _raw(link="https://kind.krx.co.kr/?rcpNo=20260305000001")
    r2 = _raw(link="https://kind.krx.co.kr/?rcpNo=20260305000002", guid="guid2")
    assert reg.process(r1) is not None
    assert reg.process(r2) is not None


def test_correction_detected():
    reg = EventRegistry()
    original = _raw(title="삼성전자(005930) - 공급계약 체결")
    reg.process(original)

    correction = _raw(
        title="삼성전자(005930) - [정정] 공급계약 체결",
        link="https://kind.krx.co.kr/?rcpNo=20260305000002",
        guid="guid2",
    )
    result = reg.process(correction)
    assert result is not None
    assert result.event_kind == EventKind.CORRECTION
    assert result.parent_id is not None


def test_withdrawal_detected():
    reg = EventRegistry()
    r = _raw(title="삼성전자(005930) - 정정(취소) 유상증자")
    result = reg.process(r)
    assert result is not None
    assert result.event_kind == EventKind.WITHDRAWAL


def test_fallback_event_id_no_uid():
    reg = EventRegistry()
    r = _raw(link="https://example.com/no-uid-here")
    result = reg.process(r)
    assert result is not None
    assert result.event_id_method.value == "FALLBACK"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_event_registry.py -v`
Expected: FAIL

**Step 3: Write event_registry.py**

```python
"""Event ID generation, dedup, correction/withdrawal detection."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Optional

from kindshot.feed import RawDisclosure, _extract_kind_uid
from kindshot.models import (
    EventIdMethod,
    EventKind,
    ParentMatchMethod,
)


@dataclass
class ProcessedEvent:
    """Enriched event after registry processing."""

    event_id: str
    event_id_method: EventIdMethod
    event_kind: EventKind
    parent_id: Optional[str]
    event_group_id: str
    parent_match_method: Optional[ParentMatchMethod]
    parent_match_score: Optional[float]
    parent_candidate_count: Optional[int]
    kind_uid: Optional[str]
    raw: RawDisclosure


def _hash(*parts: str) -> str:
    joined = "|".join(parts)
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def _normalize_title(title: str) -> str:
    """Remove correction markers and whitespace for comparison."""
    t = re.sub(r"\[정정\]", "", title)
    t = re.sub(r"정정\(취소\)", "", t)
    t = re.sub(r"정정", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _is_correction(title: str) -> bool:
    return "정정" in title or "[정정]" in title


def _is_withdrawal(title: str) -> bool:
    return "철회" in title or "취소" in title or "정정(취소)" in title


class EventRegistry:
    """Tracks seen events for dedup and links corrections to parents."""

    def __init__(self) -> None:
        self._seen_ids: set[str] = set()
        # ticker -> list of (event_id, normalized_title, detected_at)
        self._history: dict[str, list[tuple[str, str, datetime]]] = {}

    def process(self, raw: RawDisclosure) -> Optional[ProcessedEvent]:
        """Process a raw disclosure. Returns None if duplicate."""
        kind_uid = _extract_kind_uid(raw.link)

        # Generate event_id
        if kind_uid:
            event_id = _hash("KIND", kind_uid)
            method = EventIdMethod.UID
        else:
            # Fallback
            if raw.rss_guid:
                event_id = _hash("KIND", raw.rss_guid)
            elif raw.published:
                event_id = _hash("KIND", raw.published, raw.ticker, _normalize_title(raw.title))
            else:
                event_id = _hash("KIND", raw.detected_at.isoformat(), raw.ticker, _normalize_title(raw.title))
            method = EventIdMethod.FALLBACK

        # Dedup
        if event_id in self._seen_ids:
            return None
        self._seen_ids.add(event_id)

        # Determine event_kind
        if _is_withdrawal(raw.title):
            event_kind = EventKind.WITHDRAWAL
        elif _is_correction(raw.title):
            event_kind = EventKind.CORRECTION
        else:
            event_kind = EventKind.ORIGINAL

        # Correction parent linking
        parent_id: Optional[str] = None
        parent_match_method: Optional[ParentMatchMethod] = None
        parent_match_score: Optional[float] = None
        parent_candidate_count: Optional[int] = None
        norm_title = _normalize_title(raw.title)

        if event_kind in (EventKind.CORRECTION, EventKind.WITHDRAWAL):
            candidates = self._history.get(raw.ticker, [])
            parent_candidate_count = len(candidates)

            best_score = 0.0
            best_id: Optional[str] = None
            for cand_id, cand_title, _ts in candidates:
                # Exact match
                if cand_title == norm_title:
                    best_id = cand_id
                    best_score = 100.0
                    parent_match_method = ParentMatchMethod.EXACT_TITLE
                    break
                # Fuzzy match
                score = SequenceMatcher(None, norm_title, cand_title).ratio() * 100
                if score > best_score:
                    best_score = score
                    best_id = cand_id

            if best_id and best_score >= 60:
                parent_id = best_id
                parent_match_score = round(best_score, 1)
                if parent_match_method is None:
                    parent_match_method = ParentMatchMethod.FUZZY_TITLE
            else:
                parent_match_method = ParentMatchMethod.NONE
                parent_match_score = round(best_score, 1) if best_score > 0 else None

        event_group_id = parent_id if parent_id else event_id

        # Store in history
        self._history.setdefault(raw.ticker, []).append(
            (event_id, norm_title, raw.detected_at)
        )

        return ProcessedEvent(
            event_id=event_id,
            event_id_method=method,
            event_kind=event_kind,
            parent_id=parent_id,
            event_group_id=event_group_id,
            parent_match_method=parent_match_method,
            parent_match_score=parent_match_score,
            parent_candidate_count=parent_candidate_count,
            kind_uid=kind_uid,
            raw=raw,
        )
```

**Step 4: Run tests**

Run: `pytest tests/test_event_registry.py -v`
Expected: all 5 PASS

**Step 5: Commit**

```bash
git add src/kindshot/event_registry.py tests/test_event_registry.py
git commit -m "feat: event registry with dedup, correction/withdrawal linking"
```

---

### Task 7: bucket.py — Keyword 5-bucket classification + tests

**Files:**
- Create: `src/kindshot/bucket.py`
- Create: `tests/test_bucket.py`

**Step 1: Write test_bucket.py**

```python
"""Tests for keyword bucketing with NEG-first override."""

import pytest

from kindshot.bucket import classify
from kindshot.models import Bucket


def test_pos_strong_supply_contract():
    result = classify("삼성전자, 신규 공급계약 체결")
    assert result.bucket == Bucket.POS_STRONG
    assert "공급계약" in result.keyword_hits


def test_neg_strong_override():
    """NEG keyword overrides POS keyword."""
    result = classify("A사, 공급계약 해지 결정")
    assert result.bucket == Bucket.NEG_STRONG
    assert "해지" in result.keyword_hits


def test_neg_strong_cb():
    result = classify("전환사채(CB) 발행 결정")
    assert result.bucket == Bucket.NEG_STRONG


def test_pos_strong_buyback():
    result = classify("자기주식 취득 결정")
    # "취소" is NEG but "자기주식 취득" is POS_STRONG pattern
    # "취득" != "취소" so this should be POS_STRONG
    result = classify("자사주 매입 결정")
    assert result.bucket == Bucket.POS_STRONG


def test_unknown_no_keywords():
    result = classify("주주총회 소집 결과")
    assert result.bucket == Bucket.UNKNOWN


def test_matched_positions_logged():
    result = classify("대형 수주 및 공급계약 체결")
    assert len(result.keyword_hits) >= 2
    assert len(result.matched_positions) >= 2


def test_withdrawal_still_neg():
    result = classify("정정(취소) 유상증자 결정")
    assert result.bucket == Bucket.NEG_STRONG
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_bucket.py -v`
Expected: FAIL

**Step 3: Write bucket.py**

```python
"""Keyword-based 5-bucket classification with NEG-first override."""

from __future__ import annotations

from dataclasses import dataclass, field
from kindshot.models import Bucket


# ── Keyword lists ──────────────────────────────────────

NEG_STRONG_KEYWORDS: list[str] = [
    "유증", "유상증자",
    "CB발행", "CB 발행", "전환사채",
    "전환가 조정", "전환가조정",
    "대주주 매각", "대주주매각",
    "블록딜",
    "소송",
    "규제",
    "해지",
    "철회",
    "취소",
]

POS_STRONG_KEYWORDS: list[str] = [
    "수주",
    "공급계약",
    "실적 상향", "실적상향",
    "자사주 매입", "자사주매입", "자기주식 취득", "자기주식취득",
    "신규사업", "신규 사업",
    "합작",
    "대형 계약", "대형계약",
]

POS_WEAK_KEYWORDS: list[str] = [
    "리포트",
    "전망",
    "테마",
]

NEG_WEAK_KEYWORDS: list[str] = [
    "루머",
    "풍문",
]


@dataclass
class BucketResult:
    bucket: Bucket
    keyword_hits: list[str] = field(default_factory=list)
    matched_positions: list[tuple[str, int]] = field(default_factory=list)


def _find_keywords(text: str, keywords: list[str]) -> list[tuple[str, int]]:
    """Find all keyword matches with their positions."""
    matches: list[tuple[str, int]] = []
    for kw in keywords:
        idx = text.find(kw)
        if idx >= 0:
            matches.append((kw, idx))
    return matches


def classify(headline: str) -> BucketResult:
    """Classify headline into one of 5 buckets. NEG-first override."""
    text = headline

    # Priority 1: NEG_STRONG
    neg_strong = _find_keywords(text, NEG_STRONG_KEYWORDS)
    if neg_strong:
        return BucketResult(
            bucket=Bucket.NEG_STRONG,
            keyword_hits=[kw for kw, _ in neg_strong],
            matched_positions=neg_strong,
        )

    # Priority 2: POS_STRONG
    pos_strong = _find_keywords(text, POS_STRONG_KEYWORDS)
    if pos_strong:
        return BucketResult(
            bucket=Bucket.POS_STRONG,
            keyword_hits=[kw for kw, _ in pos_strong],
            matched_positions=pos_strong,
        )

    # Priority 3: POS_WEAK
    pos_weak = _find_keywords(text, POS_WEAK_KEYWORDS)
    if pos_weak:
        return BucketResult(
            bucket=Bucket.POS_WEAK,
            keyword_hits=[kw for kw, _ in pos_weak],
            matched_positions=pos_weak,
        )

    # Priority 4: NEG_WEAK
    neg_weak = _find_keywords(text, NEG_WEAK_KEYWORDS)
    if neg_weak:
        return BucketResult(
            bucket=Bucket.NEG_WEAK,
            keyword_hits=[kw for kw, _ in neg_weak],
            matched_positions=neg_weak,
        )

    # Priority 5: UNKNOWN
    return BucketResult(bucket=Bucket.UNKNOWN)
```

**Step 4: Run tests**

Run: `pytest tests/test_bucket.py -v`
Expected: all PASS

**Step 5: Commit**

```bash
git add src/kindshot/bucket.py tests/test_bucket.py
git commit -m "feat: keyword 5-bucket classification with NEG-first override"
```

---

### Task 8: quant.py — Quant 3-second check + tests

**Files:**
- Create: `src/kindshot/quant.py`
- Create: `tests/test_quant.py`

**Step 1: Write test_quant.py**

```python
"""Tests for quant 3-second check."""

import pytest

from kindshot.quant import quant_check, QuantResult
from kindshot.config import Config


def _cfg(**kw) -> Config:
    return Config(**kw)


def test_all_pass():
    r = quant_check(
        adv_value_20d=10_000_000_000,
        spread_bps=10.0,
        ret_today=5.0,
        config=_cfg(),
    )
    assert r.passed is True
    assert r.detail.adv_value_20d_ok is True
    assert r.detail.spread_bps_ok is True
    assert r.detail.extreme_move_ok is True


def test_adv_too_low():
    r = quant_check(
        adv_value_20d=3_000_000_000,
        spread_bps=10.0,
        ret_today=5.0,
        config=_cfg(),
    )
    assert r.passed is False
    assert r.skip_reason == "ADV_TOO_LOW"


def test_spread_too_wide():
    r = quant_check(
        adv_value_20d=10_000_000_000,
        spread_bps=30.0,
        ret_today=5.0,
        config=_cfg(spread_check_enabled=True),
    )
    assert r.passed is False
    assert r.skip_reason == "SPREAD_TOO_WIDE"


def test_spread_check_disabled():
    """When spread check is disabled, wide spread should pass."""
    r = quant_check(
        adv_value_20d=10_000_000_000,
        spread_bps=30.0,
        ret_today=5.0,
        config=_cfg(spread_check_enabled=False),
    )
    assert r.detail.spread_bps_ok is True


def test_extreme_move():
    r = quant_check(
        adv_value_20d=10_000_000_000,
        spread_bps=10.0,
        ret_today=25.0,
        config=_cfg(),
    )
    assert r.passed is False
    assert r.skip_reason == "EXTREME_MOVE"


def test_extreme_move_negative():
    r = quant_check(
        adv_value_20d=10_000_000_000,
        spread_bps=10.0,
        ret_today=-22.0,
        config=_cfg(),
    )
    assert r.passed is False


def test_should_track_price_sampling(monkeypatch):
    """10% sampling of quant fails for price tracking."""
    import kindshot.quant as qmod
    monkeypatch.setattr(qmod.random, "random", lambda: 0.05)  # < 0.10

    r = quant_check(
        adv_value_20d=3_000_000_000,
        spread_bps=10.0,
        ret_today=5.0,
        config=_cfg(),
    )
    assert r.passed is False
    assert r.should_track_price is True


def test_no_tracking_when_not_sampled(monkeypatch):
    import kindshot.quant as qmod
    monkeypatch.setattr(qmod.random, "random", lambda: 0.50)  # > 0.10

    r = quant_check(
        adv_value_20d=3_000_000_000,
        spread_bps=10.0,
        ret_today=5.0,
        config=_cfg(),
    )
    assert r.should_track_price is False
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_quant.py -v`
Expected: FAIL

**Step 3: Write quant.py**

```python
"""Quant 3-second check: ADV, spread, extreme move."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

from kindshot.config import Config
from kindshot.models import QuantCheckDetail


@dataclass
class QuantResult:
    passed: bool
    detail: QuantCheckDetail
    skip_reason: Optional[str]
    should_track_price: bool  # True if failed but sampled for tracking
    analysis_tag: Optional[str]


def quant_check(
    adv_value_20d: float,
    spread_bps: Optional[float],
    ret_today: float,
    config: Config,
) -> QuantResult:
    """Run 3 quant filters. Returns result with pass/fail and skip reason."""

    adv_ok = adv_value_20d >= config.adv_threshold

    if config.spread_check_enabled and spread_bps is not None:
        spread_ok = spread_bps <= config.spread_bps_limit
    else:
        spread_ok = True  # skip check when disabled or unavailable

    extreme_ok = abs(ret_today) <= config.extreme_move_pct

    detail = QuantCheckDetail(
        adv_value_20d_ok=adv_ok,
        spread_bps_ok=spread_ok,
        extreme_move_ok=extreme_ok,
    )

    passed = adv_ok and spread_ok and extreme_ok

    # Determine skip reason (first failure wins)
    skip_reason: Optional[str] = None
    if not passed:
        if not adv_ok:
            skip_reason = "ADV_TOO_LOW"
        elif not spread_ok:
            skip_reason = "SPREAD_TOO_WIDE"
        elif not extreme_ok:
            skip_reason = "EXTREME_MOVE"

    # 10% sampling of quant fails for price tracking
    should_track = False
    analysis_tag: Optional[str] = None
    if not passed and random.random() < config.quant_fail_sample_rate:
        should_track = True
        analysis_tag = "QUANT_FAIL_SAMPLE"

    return QuantResult(
        passed=passed,
        detail=detail,
        skip_reason=skip_reason,
        should_track_price=should_track,
        analysis_tag=analysis_tag,
    )
```

**Step 4: Run tests**

Run: `pytest tests/test_quant.py -v`
Expected: all PASS

**Step 5: Commit**

```bash
git add src/kindshot/quant.py tests/test_quant.py
git commit -m "feat: quant 3-second check with sampling for missed opportunities"
```

---

## Phase 3: Decision + KIS (4 files)

### Task 9: kis_client.py — KIS REST client (graceful when disabled)

**Files:**
- Create: `src/kindshot/kis_client.py`

**Step 1: Write kis_client.py**

```python
"""KIS REST API client. Gracefully returns None when credentials are missing."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import aiohttp

from kindshot.config import Config

logger = logging.getLogger(__name__)

BASE_URL_REAL = "https://openapi.koreainvestment.com:9443"
BASE_URL_PAPER = "https://openapivts.koreainvestment.com:29443"


@dataclass
class PriceInfo:
    px: float
    spread_bps: Optional[float]
    cum_value: Optional[float]
    fetch_latency_ms: int


class KisClient:
    """KIS REST API client with token management."""

    def __init__(self, config: Config, session: aiohttp.ClientSession) -> None:
        self._config = config
        self._session = session
        self._base = BASE_URL_PAPER if config.kis_is_paper else BASE_URL_REAL
        self._token: Optional[str] = None
        self._token_expires: float = 0.0

    async def _ensure_token(self) -> Optional[str]:
        if self._token and time.time() < self._token_expires:
            return self._token

        try:
            async with self._session.post(
                f"{self._base}/oauth2/tokenP",
                json={
                    "grant_type": "client_credentials",
                    "appkey": self._config.kis_app_key,
                    "appsecret": self._config.kis_app_secret,
                },
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                data = await resp.json()
                self._token = data.get("access_token")
                # Expire 23h to be safe (actual: 24h)
                self._token_expires = time.time() + 23 * 3600
                return self._token
        except Exception:
            logger.exception("KIS token fetch failed")
            return None

    def _headers(self, token: str, tr_id: str) -> dict[str, str]:
        return {
            "authorization": f"Bearer {token}",
            "appkey": self._config.kis_app_key,
            "appsecret": self._config.kis_app_secret,
            "tr_id": tr_id,
            "content-type": "application/json; charset=utf-8",
        }

    async def get_price(self, ticker: str) -> Optional[PriceInfo]:
        """Get current price for a ticker. Returns None on any failure."""
        token = await self._ensure_token()
        if not token:
            return None

        t0 = time.monotonic()
        try:
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
            }
            async with self._session.get(
                f"{self._base}/uapi/domestic-stock/v1/quotations/inquire-price",
                headers=self._headers(token, "FHKST01010100"),
                params=params,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                data = await resp.json()
                output = data.get("output", {})

                px = float(output.get("stck_prpr", 0))
                ask = float(output.get("stck_sdpr", 0))  # best ask
                bid = float(output.get("stck_hgpr", 0))  # best bid — field names may vary
                cum_value = float(output.get("acml_tr_pbmn", 0))

                spread_bps = None
                if ask > 0 and bid > 0 and px > 0:
                    spread_bps = ((ask - bid) / px) * 10000

                latency = int((time.monotonic() - t0) * 1000)
                return PriceInfo(px=px, spread_bps=spread_bps, cum_value=cum_value, fetch_latency_ms=latency)

        except Exception:
            logger.exception("KIS price fetch failed for %s", ticker)
            return None

    async def get_kospi_index(self) -> Optional[float]:
        """Get current KOSPI change %. Returns None on failure."""
        token = await self._ensure_token()
        if not token:
            return None

        try:
            params = {
                "FID_COND_MRKT_DIV_CODE": "U",
                "FID_INPUT_ISCD": "0001",  # KOSPI
            }
            async with self._session.get(
                f"{self._base}/uapi/domestic-stock/v1/quotations/inquire-index-price",
                headers=self._headers(token, "FHPUP02100000"),
                params=params,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                data = await resp.json()
                output = data.get("output", {})
                return float(output.get("prdy_ctrt", 0))
        except Exception:
            logger.exception("KIS KOSPI fetch failed")
            return None
```

**Step 2: Commit**

```bash
git add src/kindshot/kis_client.py
git commit -m "feat: KIS REST client with token management and graceful failure"
```

---

### Task 10: context_card.py — pykrx batch + KIS realtime features

**Files:**
- Create: `src/kindshot/context_card.py`

**Step 1: Write context_card.py**

```python
"""Context Card: pykrx historical features + KIS realtime features."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from kindshot.kis_client import KisClient
from kindshot.models import ContextCard

logger = logging.getLogger(__name__)


async def _pykrx_features(ticker: str) -> dict:
    """Fetch historical features from pykrx. Runs in thread (blocking I/O)."""

    def _fetch() -> dict:
        try:
            from pykrx import stock

            today = datetime.now().strftime("%Y%m%d")
            start_20d = (datetime.now() - timedelta(days=40)).strftime("%Y%m%d")

            df = stock.get_market_ohlcv(start_20d, today, ticker)
            if df.empty or len(df) < 2:
                return {}

            close = df["종가"]
            volume = df["거래량"]
            value = df["거래대금"]

            prev_close = close.iloc[-2] if len(close) >= 2 else None
            cur_close = close.iloc[-1]
            close_3d = close.iloc[-4] if len(close) >= 4 else None

            ret_1d = ((cur_close / close.iloc[-2]) - 1) * 100 if len(close) >= 2 else None
            ret_3d = ((cur_close / close_3d) - 1) * 100 if close_3d else None

            last_20 = close.tail(20)
            if len(last_20) >= 2:
                low_20 = last_20.min()
                high_20 = last_20.max()
                rng = high_20 - low_20
                pos_20d = ((cur_close - low_20) / rng * 100) if rng > 0 else 50.0
            else:
                pos_20d = None

            adv_20d = value.tail(20).mean() if len(value) >= 20 else value.mean()

            # vol_pct_20d: current volume percentile in 20-day window
            vol_20 = volume.tail(20)
            cur_vol = volume.iloc[-1]
            vol_pct = (vol_20 < cur_vol).sum() / len(vol_20) * 100 if len(vol_20) > 0 else None

            return {
                "ret_1d": round(ret_1d, 2) if ret_1d is not None else None,
                "ret_3d": round(ret_3d, 2) if ret_3d is not None else None,
                "pos_20d": round(pos_20d, 1) if pos_20d is not None else None,
                "adv_value_20d": round(adv_20d) if adv_20d is not None else None,
                "vol_pct_20d": round(vol_pct, 1) if vol_pct is not None else None,
                "prev_close": prev_close,
            }
        except Exception:
            logger.exception("pykrx fetch failed for %s", ticker)
            return {}

    return await asyncio.to_thread(_fetch)


async def build_context_card(
    ticker: str,
    kis: Optional[KisClient] = None,
) -> tuple[ContextCard, dict]:
    """Build context card for a ticker.

    Returns (ContextCard, raw_data_dict) where raw_data_dict has
    additional fields like prev_close needed by quant check.
    """
    hist = await _pykrx_features(ticker)

    # KIS realtime features (optional)
    spread_bps: Optional[float] = None
    ret_today: Optional[float] = None
    gap: Optional[float] = None

    if kis:
        price_info = await kis.get_price(ticker)
        if price_info:
            spread_bps = price_info.spread_bps
            prev_close = hist.get("prev_close")
            if prev_close and prev_close > 0:
                ret_today = round(((price_info.px / prev_close) - 1) * 100, 2)

    card = ContextCard(
        ret_today=ret_today,
        ret_1d=hist.get("ret_1d"),
        ret_3d=hist.get("ret_3d"),
        pos_20d=hist.get("pos_20d"),
        gap=gap,
        adv_value_20d=hist.get("adv_value_20d"),
        spread_bps=spread_bps,
        vol_pct_20d=hist.get("vol_pct_20d"),
    )

    raw = {**hist, "spread_bps": spread_bps, "ret_today": ret_today}
    return card, raw
```

**Step 2: Commit**

```bash
git add src/kindshot/context_card.py
git commit -m "feat: context card builder with pykrx history + KIS realtime"
```

---

### Task 11: decision.py — LLM 1-shot with cache + tests

**Files:**
- Create: `src/kindshot/decision.py`
- Create: `tests/test_decision.py`

**Step 1: Write test_decision.py**

```python
"""Tests for LLM decision engine."""

import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kindshot.config import Config
from kindshot.decision import DecisionEngine, _parse_llm_response, _build_prompt
from kindshot.models import Bucket, ContextCard, Action, SizeHint


def test_parse_valid_json():
    raw = '{"action": "BUY", "confidence": 82, "size_hint": "M", "reason": "good signal"}'
    result = _parse_llm_response(raw)
    assert result is not None
    assert result["action"] == "BUY"
    assert result["confidence"] == 82


def test_parse_json_with_backticks():
    raw = '```json\n{"action": "SKIP", "confidence": 30, "size_hint": "S", "reason": "already priced in"}\n```'
    result = _parse_llm_response(raw)
    assert result is not None
    assert result["action"] == "SKIP"


def test_parse_invalid_json():
    result = _parse_llm_response("not json at all")
    assert result is None


def test_parse_invalid_action():
    raw = '{"action": "SELL", "confidence": 50, "size_hint": "M", "reason": "test"}'
    result = _parse_llm_response(raw)
    assert result is None


def test_build_prompt():
    ctx = ContextCard(ret_today=6.1, ret_1d=0.8, ret_3d=4.2, pos_20d=87, gap=0.3, adv_value_20d=82e9, spread_bps=9, vol_pct_20d=88)
    prompt = _build_prompt(
        bucket=Bucket.POS_STRONG,
        headline="반도체 사업 미국 대형 공급계약 체결",
        ticker="005930",
        corp_name="삼성전자",
        detected_at="09:12:04",
        ctx=ctx,
    )
    assert "POS_STRONG" in prompt
    assert "005930" in prompt
    assert "BUY" in prompt


async def test_cache_hit():
    cfg = Config(anthropic_api_key="test")
    engine = DecisionEngine(cfg)

    mock_client = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text='{"action":"BUY","confidence":80,"size_hint":"M","reason":"test"}')]
    mock_msg.usage = MagicMock(input_tokens=100, output_tokens=50)
    mock_client.messages.create = AsyncMock(return_value=mock_msg)
    engine._client = mock_client

    ctx = ContextCard()
    # First call
    r1 = await engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:00")
    # Second call (should be cached)
    r2 = await engine.decide("005930", "삼성전자", "공급계약 체결", Bucket.POS_STRONG, ctx, "09:00:01")

    assert r1 is not None
    assert r2 is not None
    assert r2.decision_source == "CACHE"
    assert mock_client.messages.create.call_count == 1
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_decision.py -v`
Expected: FAIL

**Step 3: Write decision.py**

```python
"""LLM 1-shot Decision Engine with caching."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from kindshot.config import Config
from kindshot.models import (
    Action,
    Bucket,
    ContextCard,
    DecisionRecord,
    SizeHint,
)

logger = logging.getLogger(__name__)


def _build_prompt(
    bucket: Bucket,
    headline: str,
    ticker: str,
    corp_name: str,
    detected_at: str,
    ctx: ContextCard,
) -> str:
    ctx_price = (
        f"ret_today={ctx.ret_today} ret_1d={ctx.ret_1d} ret_3d={ctx.ret_3d} "
        f"pos_20d={ctx.pos_20d} gap={ctx.gap}"
    )
    adv_display = f"{ctx.adv_value_20d/1e8:.0f}억" if ctx.adv_value_20d else "N/A"
    ctx_micro = f"adv_20d={adv_display} spread_bps={ctx.spread_bps} vol_pct_20d={ctx.vol_pct_20d}"

    return f"""event: [{bucket.value}] {corp_name}, {headline}
corp: {corp_name}({ticker})
detected_at: {detected_at} KST

ctx_price: {ctx_price}
ctx_micro: {ctx_micro}

constraints: max_pos=10% no_overnight=true daily_loss_remaining=85%

task: decide BUY or SKIP. no speculation on cause. no narrative.
output: {{"action":"BUY|SKIP","confidence":0-100,"size_hint":"S|M|L","reason":"≤15 words"}}"""


def _parse_llm_response(raw: str) -> Optional[dict]:
    """Parse LLM JSON response, stripping backticks if present."""
    text = raw.strip()
    # Remove markdown code fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None

    # Validate required fields
    action = data.get("action")
    if action not in ("BUY", "SKIP"):
        return None

    confidence = data.get("confidence")
    if not isinstance(confidence, (int, float)) or not (0 <= confidence <= 100):
        return None

    size_hint = data.get("size_hint")
    if size_hint not in ("S", "M", "L"):
        return None

    return data


@dataclass
class _CacheEntry:
    result: DecisionRecord
    expires_at: float


class DecisionEngine:
    """LLM 1-shot decision with caching."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._cache: dict[str, _CacheEntry] = {}
        self._last_sweep: float = time.monotonic()
        self._client: Optional[object] = None

    def _get_client(self):
        if self._client is None:
            from anthropic import AsyncAnthropic
            self._client = AsyncAnthropic(
                api_key=self._config.anthropic_api_key,
                timeout=self._config.llm_sdk_timeout_s,
            )
        return self._client

    def _cache_key(self, ticker: str, headline: str, bucket: Bucket) -> str:
        h = hashlib.md5(headline.encode()).hexdigest()[:8]
        return f"{ticker}:{h}:{bucket.value}"

    def _sweep_cache(self) -> None:
        now = time.monotonic()
        if now - self._last_sweep < self._config.llm_cache_sweep_s:
            return
        expired = [k for k, v in self._cache.items() if v.expires_at < now]
        for k in expired:
            del self._cache[k]
        self._last_sweep = now

    async def decide(
        self,
        ticker: str,
        corp_name: str,
        headline: str,
        bucket: Bucket,
        ctx: ContextCard,
        detected_at_str: str,
        *,
        run_id: str = "",
        schema_version: str = "0.1.2",
    ) -> Optional[DecisionRecord]:
        """Call LLM for BUY/SKIP decision. Returns None on timeout/parse failure."""

        self._sweep_cache()
        key = self._cache_key(ticker, headline, bucket)

        # Cache hit
        if key in self._cache and self._cache[key].expires_at > time.monotonic():
            cached = self._cache[key].result
            return DecisionRecord(
                schema_version=cached.schema_version,
                run_id=run_id or cached.run_id,
                event_id=cached.event_id,
                decided_at=datetime.now(timezone.utc),
                llm_model=cached.llm_model,
                llm_latency_ms=0,
                action=cached.action,
                confidence=cached.confidence,
                size_hint=cached.size_hint,
                reason=cached.reason,
                decision_source="CACHE",
            )

        prompt = _build_prompt(bucket, headline, ticker, corp_name, detected_at_str, ctx)
        client = self._get_client()

        t0 = time.monotonic()
        try:
            resp = await asyncio.wait_for(
                client.messages.create(
                    model=self._config.llm_model,
                    max_tokens=200,
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=self._config.llm_wait_for_s,
            )
            latency_ms = int((time.monotonic() - t0) * 1000)
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning("LLM call failed: %s", e)
            return None

        raw_text = resp.content[0].text
        parsed = _parse_llm_response(raw_text)
        if parsed is None:
            logger.warning("LLM parse failed: %s", raw_text[:200])
            return None

        record = DecisionRecord(
            schema_version=schema_version,
            run_id=run_id,
            event_id="",  # filled by caller
            decided_at=datetime.now(timezone.utc),
            llm_model=self._config.llm_model,
            llm_latency_ms=latency_ms,
            action=Action(parsed["action"]),
            confidence=int(parsed["confidence"]),
            size_hint=SizeHint(parsed["size_hint"]),
            reason=parsed.get("reason", ""),
            decision_source="LLM",
        )

        # Cache
        self._cache[key] = _CacheEntry(
            result=record,
            expires_at=time.monotonic() + self._config.llm_cache_ttl_s,
        )

        return record
```

**Step 4: Run tests**

Run: `pytest tests/test_decision.py -v`
Expected: all PASS

**Step 5: Commit**

```bash
git add src/kindshot/decision.py tests/test_decision.py
git commit -m "feat: LLM 1-shot decision engine with caching and timeout"
```

---

### Task 12: guardrails.py — Interface stub + tests

**Files:**
- Create: `src/kindshot/guardrails.py`
- Create: `tests/test_guardrails.py`

**Step 1: Write test_guardrails.py**

```python
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
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_guardrails.py -v`
Expected: FAIL

**Step 3: Write guardrails.py**

```python
"""Hard guardrails — MVP: interface stub only.

MVP boundary:
    Quant 3-second check (quant.py) runs BEFORE LLM call as a pre-filter.
    Guardrails run AFTER LLM call as the final safety net.
    Both use the same thresholds (spread_bps=25, adv=50억, extreme=20%).

    In MVP, guardrails always pass (stub). Real implementation in v0.4
    when actual order execution is added.

Post-MVP guardrail checklist:
    1. spread_bps > 25 → BLOCK
    2. adv_20d < 50억 → BLOCK
    3. VI / 상한가 근접 (+25%) / 극단과열 (±20%) → BLOCK
    4. 일일 손실 한도 초과 → BLOCK
    5. 동일 종목 당일 재매수 → BLOCK
    6. 동일 섹터 동시 2개 → BLOCK
    7. 포지션 > 계좌 10% → BLOCK
    8. 관리종목 / 투자경고 / 투자위험 → BLOCK
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class GuardrailResult:
    passed: bool
    reason: Optional[str] = None


def check_guardrails(
    ticker: str,
    spread_bps: Optional[float],
    adv_value_20d: Optional[float],
    ret_today: Optional[float],
    **kwargs: object,
) -> GuardrailResult:
    """MVP stub: always passes. Real checks added in v0.4."""
    return GuardrailResult(passed=True)
```

**Step 4: Run tests**

Run: `pytest tests/test_guardrails.py -v`
Expected: all PASS

**Step 5: Commit**

```bash
git add src/kindshot/guardrails.py tests/test_guardrails.py
git commit -m "feat: guardrails interface stub (real checks in v0.4)"
```

---

## Phase 4: Price + Market (2 files)

### Task 13: price.py — PriceFetcher + SnapshotScheduler

**Files:**
- Create: `src/kindshot/price.py`

**Step 1: Write price.py**

```python
"""Price fetching and snapshot scheduling."""

from __future__ import annotations

import asyncio
import heapq
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from kindshot.config import Config
from kindshot.kis_client import KisClient, PriceInfo
from kindshot.logger import JsonlLogger
from kindshot.models import PriceSnapshot, T0Basis

logger = logging.getLogger(__name__)

# Horizon offsets in seconds from t0
HORIZON_OFFSETS: dict[str, float] = {
    "t+1m": 60,
    "t+5m": 300,
    "t+30m": 1800,
}


@dataclass
class PriceFetcher:
    """Fetches price from KIS or returns UNAVAILABLE."""

    kis: Optional[KisClient]

    async def fetch(self, ticker: str) -> Optional[PriceInfo]:
        if self.kis is None:
            return None
        return await self.kis.get_price(ticker)


@dataclass(order=True)
class ScheduledSnapshot:
    fire_at: float
    event_id: str = field(compare=False)
    ticker: str = field(compare=False)
    horizon: str = field(compare=False)
    t0_basis: T0Basis = field(compare=False)
    t0_ts: datetime = field(compare=False)
    t0_px: Optional[float] = field(compare=False, default=None)
    t0_cum_value: Optional[float] = field(compare=False, default=None)
    run_id: str = field(compare=False, default="")
    schema_version: str = field(compare=False, default="0.1.2")


class SnapshotScheduler:
    """Schedules and fires price snapshots at t0, t+1m, t+5m, t+30m, close."""

    def __init__(
        self,
        config: Config,
        fetcher: PriceFetcher,
        log: JsonlLogger,
    ) -> None:
        self._config = config
        self._fetcher = fetcher
        self._logger = log
        self._heap: list[ScheduledSnapshot] = []
        self._running = True

    def schedule_t0(
        self,
        event_id: str,
        ticker: str,
        t0_basis: T0Basis,
        t0_ts: datetime,
        run_id: str,
    ) -> None:
        """Schedule t0 snapshot immediately + future horizons."""
        now = time.monotonic()

        # t0: fire immediately (will be fetched in the run loop)
        heapq.heappush(self._heap, ScheduledSnapshot(
            fire_at=now,
            event_id=event_id,
            ticker=ticker,
            horizon="t0",
            t0_basis=t0_basis,
            t0_ts=t0_ts,
            run_id=run_id,
            schema_version=self._config.schema_version,
        ))

        # Future horizons
        for horizon, offset_s in HORIZON_OFFSETS.items():
            heapq.heappush(self._heap, ScheduledSnapshot(
                fire_at=now + offset_s,
                event_id=event_id,
                ticker=ticker,
                horizon=horizon,
                t0_basis=t0_basis,
                t0_ts=t0_ts,
                run_id=run_id,
                schema_version=self._config.schema_version,
            ))

        # Close snapshot: 15:31~15:35 KST
        today_close = datetime.now().replace(hour=15, minute=31, second=0, microsecond=0)
        close_mono = now + max(0, (today_close - datetime.now()).total_seconds())
        heapq.heappush(self._heap, ScheduledSnapshot(
            fire_at=close_mono,
            event_id=event_id,
            ticker=ticker,
            horizon="close",
            t0_basis=t0_basis,
            t0_ts=t0_ts,
            run_id=run_id,
            schema_version=self._config.schema_version,
        ))

    async def _fire(self, snap: ScheduledSnapshot) -> None:
        """Execute a single snapshot fetch and log."""
        price = await self._fetcher.fetch(snap.ticker)

        px: Optional[float] = None
        spread_bps: Optional[float] = None
        cum_value: Optional[float] = None
        latency_ms: Optional[int] = None
        price_source: Optional[str] = None

        if price:
            px = price.px
            spread_bps = price.spread_bps
            cum_value = price.cum_value
            latency_ms = price.fetch_latency_ms
            price_source = "KIS_REST"

        # Calculate returns vs t0
        ret_long: Optional[float] = None
        ret_short: Optional[float] = None
        value_since: Optional[float] = None

        if snap.horizon == "t0":
            ret_long = 0.0
            ret_short = 0.0
            value_since = 0
            # Store t0 values for future snapshots
            snap.t0_px = px
            snap.t0_cum_value = cum_value
        elif px is not None and snap.t0_px and snap.t0_px > 0:
            ret_long = (px - snap.t0_px) / snap.t0_px
            ret_short = -ret_long
            if cum_value is not None and snap.t0_cum_value is not None:
                value_since = cum_value - snap.t0_cum_value

        record = PriceSnapshot(
            schema_version=snap.schema_version,
            run_id=snap.run_id,
            event_id=snap.event_id,
            horizon=snap.horizon,
            ts=datetime.now(timezone.utc),
            t0_basis=snap.t0_basis,
            t0_ts=snap.t0_ts,
            px=px,
            cum_value=cum_value,
            ret_long_vs_t0=ret_long,
            ret_short_vs_t0=ret_short,
            value_since_t0=value_since,
            spread_bps=spread_bps,
            price_source=price_source,
            snapshot_fetch_latency_ms=latency_ms,
        )

        await self._logger.write(record)

    async def run(self) -> None:
        """Main loop: fire snapshots as they become due."""
        while self._running:
            now = time.monotonic()

            while self._heap and self._heap[0].fire_at <= now:
                snap = heapq.heappop(self._heap)
                try:
                    await self._fire(snap)
                except Exception:
                    logger.exception("Snapshot fire failed: %s/%s", snap.event_id, snap.horizon)

            await asyncio.sleep(1.0)

    def stop(self) -> None:
        self._running = False

    @property
    def pending_count(self) -> int:
        return len(self._heap)
```

**Step 2: Commit**

```bash
git add src/kindshot/price.py
git commit -m "feat: price fetcher + snapshot scheduler with heapq"
```

---

### Task 14: market.py — KOSPI -1% halt rule

**Files:**
- Create: `src/kindshot/market.py`

**Step 1: Write market.py**

```python
"""Market environment check: KOSPI -1% halt rule."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from kindshot.config import Config
from kindshot.kis_client import KisClient

logger = logging.getLogger(__name__)


class MarketMonitor:
    """Monitors KOSPI for halt condition.

    When KIS is unavailable, market check is disabled (always allows trading).
    Operator should monitor manually in that case.
    """

    def __init__(self, config: Config, kis: Optional[KisClient] = None) -> None:
        self._config = config
        self._kis = kis
        self._halted = False
        self._last_check: Optional[float] = None
        self._kospi_change: Optional[float] = None

    @property
    def is_halted(self) -> bool:
        return self._halted

    @property
    def enabled(self) -> bool:
        return self._kis is not None

    async def update(self) -> None:
        """Check KOSPI and update halt status."""
        if not self._kis:
            return

        change = await self._kis.get_kospi_index()
        if change is not None:
            self._kospi_change = change
            was_halted = self._halted
            self._halted = change <= self._config.kospi_halt_pct
            if self._halted and not was_halted:
                logger.warning("MARKET HALT: KOSPI %.2f%% <= %.1f%%", change, self._config.kospi_halt_pct)
            elif not self._halted and was_halted:
                logger.info("Market halt lifted: KOSPI %.2f%%", change)
```

**Step 2: Commit**

```bash
git add src/kindshot/market.py
git commit -m "feat: KOSPI -1% market halt monitor"
```

---

## Phase 5: Orchestration + Tests

### Task 15: main.py — Asyncio orchestrator with supervisor

**Files:**
- Create: `src/kindshot/main.py`

**Step 1: Write main.py**

```python
"""Main entry point: asyncio supervisor orchestrating all components."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from kindshot.bucket import classify
from kindshot.config import Config, load_config
from kindshot.context_card import build_context_card
from kindshot.decision import DecisionEngine
from kindshot.event_registry import EventRegistry
from kindshot.feed import KindFeed
from kindshot.guardrails import check_guardrails
from kindshot.kis_client import KisClient
from kindshot.logger import JsonlLogger
from kindshot.market import MarketMonitor
from kindshot.models import (
    Bucket,
    EventRecord,
    SkipStage,
    T0Basis,
)
from kindshot.price import PriceFetcher, SnapshotScheduler
from kindshot.quant import quant_check

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-20s %(levelname)-5s %(message)s",
)
logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="kindshot MVP")
    p.add_argument("--dry-run", action="store_true", help="Skip LLM calls, log events only")
    return p.parse_args()


async def _pipeline_loop(
    feed: KindFeed,
    registry: EventRegistry,
    decision_engine: DecisionEngine,
    market: MarketMonitor,
    scheduler: SnapshotScheduler,
    log: JsonlLogger,
    config: Config,
    run_id: str,
    kis: Optional[KisClient],
) -> None:
    """Main pipeline: feed → registry → bucket → quant → decision → log."""

    async for batch in feed.stream():
        for raw in batch:
            detected_at = raw.detected_at

            # 1. Registry: dedup + correction
            processed = registry.process(raw)
            if processed is None:
                # Duplicate — log skip
                logger.debug("DUPLICATE: %s", raw.title[:60])
                continue

            # 2. Bucket classification
            bucket_result = classify(raw.title)

            # 3. Build event record (partial — will fill quant/ctx later)
            # Parse disclosed_at
            disclosed_at: Optional[datetime] = None
            disclosed_at_missing = True
            delay_ms: Optional[int] = None
            if raw.published:
                try:
                    from dateutil.parser import parse as dt_parse
                    disclosed_at = dt_parse(raw.published)
                    disclosed_at_missing = False
                    delay_ms = int((detected_at - disclosed_at).total_seconds() * 1000)
                except Exception:
                    pass

            # Skip non-actionable buckets early
            skip_stage: Optional[SkipStage] = None
            skip_reason: Optional[str] = None
            analysis_tag: Optional[str] = None
            quant_passed: Optional[bool] = None
            quant_detail = None
            ctx = None
            should_track_price = False

            if bucket_result.bucket == Bucket.NEG_STRONG:
                skip_stage = SkipStage.BUCKET
                skip_reason = "NEG_BUCKET"
                analysis_tag = "SHORT_WATCH"
                should_track_price = True

            elif bucket_result.bucket == Bucket.POS_STRONG:
                # Build context card
                ctx_card, raw_data = await build_context_card(raw.ticker, kis)
                ctx = ctx_card

                # Quant check
                adv = raw_data.get("adv_value_20d") or 0
                spread = raw_data.get("spread_bps")
                ret_today = raw_data.get("ret_today") or 0

                qr = quant_check(adv, spread, ret_today, config)
                quant_passed = qr.passed
                quant_detail = qr.detail

                if not qr.passed:
                    skip_stage = SkipStage.QUANT
                    skip_reason = qr.skip_reason
                    should_track_price = qr.should_track_price
                    analysis_tag = qr.analysis_tag

            else:
                skip_stage = SkipStage.BUCKET
                skip_reason = f"{bucket_result.bucket.value}_BUCKET"

            # Log event record
            event_rec = EventRecord(
                schema_version=config.schema_version,
                run_id=run_id,
                event_id=processed.event_id,
                event_id_method=processed.event_id_method,
                event_kind=processed.event_kind,
                parent_id=processed.parent_id,
                event_group_id=processed.event_group_id,
                parent_match_method=processed.parent_match_method,
                parent_match_score=processed.parent_match_score,
                parent_candidate_count=processed.parent_candidate_count,
                source="KIND",
                rss_guid=raw.rss_guid,
                rss_link=raw.link,
                kind_uid=processed.kind_uid,
                disclosed_at=disclosed_at,
                disclosed_at_missing=disclosed_at_missing,
                detected_at=detected_at,
                delay_ms=delay_ms,
                ticker=raw.ticker,
                corp_name=raw.corp_name,
                headline=raw.title,
                bucket=bucket_result.bucket,
                keyword_hits=bucket_result.keyword_hits,
                analysis_tag=analysis_tag,
                skip_stage=skip_stage,
                skip_reason=skip_reason,
                quant_check_passed=quant_passed,
                quant_check_detail=quant_detail,
                ctx=ctx,
            )
            await log.write(event_rec)

            # Schedule price tracking if needed
            if should_track_price:
                scheduler.schedule_t0(
                    event_id=processed.event_id,
                    ticker=raw.ticker,
                    t0_basis=T0Basis.DETECTED_AT,
                    t0_ts=detected_at,
                    run_id=run_id,
                )

            # 4. Decision (POS_STRONG + quant pass only)
            if bucket_result.bucket != Bucket.POS_STRONG or not quant_passed:
                continue

            # Market halt check
            if market.is_halted:
                logger.info("SKIP (market halted): %s", raw.title[:60])
                continue

            # Dry run: skip LLM
            if config.dry_run:
                logger.info("DRY-RUN SKIP decision: %s", raw.title[:60])
                continue

            detected_str = detected_at.strftime("%H:%M:%S")
            decision = await decision_engine.decide(
                ticker=raw.ticker,
                corp_name=raw.corp_name,
                headline=raw.title,
                bucket=bucket_result.bucket,
                ctx=ctx or build_context_card.__defaults__,  # type: ignore
                detected_at_str=detected_str,
                run_id=run_id,
                schema_version=config.schema_version,
            )

            if decision is None:
                # LLM timeout or parse failure — already logged in decision.py
                continue

            decision.event_id = processed.event_id
            await log.write(decision)

            # Schedule price snapshots with DECIDED_AT basis
            scheduler.schedule_t0(
                event_id=processed.event_id,
                ticker=raw.ticker,
                t0_basis=T0Basis.DECIDED_AT,
                t0_ts=decision.decided_at,
                run_id=run_id,
            )

            action_str = decision.action.value
            logger.info(
                "%s [%s] conf=%d hint=%s: %s",
                action_str, raw.ticker, decision.confidence, decision.size_hint.value, decision.reason,
            )


async def run() -> None:
    args = _parse_args()
    config = load_config(dry_run=args.dry_run)
    run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    logger.info("kindshot %s starting (run_id=%s, dry_run=%s)", config.schema_version, run_id, config.dry_run)

    log = JsonlLogger(config.log_dir, run_id=run_id)

    async with aiohttp.ClientSession() as session:
        # KIS client (optional)
        kis: Optional[KisClient] = None
        if config.kis_enabled:
            kis = KisClient(config, session)
            logger.info("KIS client enabled")
        else:
            logger.warning("KIS client disabled — price snapshots will be UNAVAILABLE")

        feed = KindFeed(config, session)
        registry = EventRegistry()
        decision_engine = DecisionEngine(config)
        market = MarketMonitor(config, kis)
        fetcher = PriceFetcher(kis=kis)
        scheduler = SnapshotScheduler(config, fetcher, log)

        # Graceful shutdown
        stop_event = asyncio.Event()

        def _signal_handler() -> None:
            logger.info("Shutdown signal received, pending snapshots: %d", scheduler.pending_count)
            stop_event.set()
            scheduler.stop()

        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

        # Market monitor task (update every 60s)
        async def _market_loop() -> None:
            while not stop_event.is_set():
                try:
                    await market.update()
                except Exception:
                    logger.exception("Market monitor error")
                await asyncio.sleep(60)

        tasks = [
            asyncio.create_task(_pipeline_loop(
                feed, registry, decision_engine, market, scheduler, log, config, run_id, kis,
            ), name="pipeline"),
            asyncio.create_task(scheduler.run(), name="snapshots"),
            asyncio.create_task(_market_loop(), name="market"),
        ]

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            for t in tasks:
                t.cancel()
            logger.info("Shutdown complete. Pending snapshots lost: %d", scheduler.pending_count)
```

**Step 2: Commit**

```bash
git add src/kindshot/main.py src/kindshot/__main__.py
git commit -m "feat: asyncio orchestrator with supervisor, graceful shutdown"
```

---

### Task 16: Final verification

**Step 1: Run all tests**

Run: `pytest tests/ -v`
Expected: all tests PASS

**Step 2: Verify dry-run mode starts**

Run: `python -m kindshot --dry-run`
Expected: starts polling, logs events, no LLM calls. Ctrl+C to stop.

**Step 3: Verify full mode starts (with ANTHROPIC_API_KEY set)**

Run: `python -m kindshot`
Expected: starts polling, classifies events, calls LLM for POS_STRONG.

**Step 4: Check log output**

Run: `ls logs/` and inspect the JSONL file
Expected: event records with schema_version, run_id, buckets, etc.

**Step 5: Final commit**

```bash
git add -A
git commit -m "feat: kindshot MVP v0.1.2 complete"
```

---

## Summary of commits

| # | Commit message | Files |
|---|---------------|-------|
| 1 | project scaffolding | pyproject.toml, .env.example, .gitignore, __init__.py, __main__.py |
| 2 | config.py | config.py |
| 3 | models.py | models.py |
| 4 | logger.py + tests | logger.py, test_logger.py |
| 5 | feed.py | feed.py |
| 6 | event_registry.py + tests | event_registry.py, test_event_registry.py |
| 7 | bucket.py + tests | bucket.py, test_bucket.py |
| 8 | quant.py + tests | quant.py, test_quant.py |
| 9 | kis_client.py | kis_client.py |
| 10 | context_card.py | context_card.py |
| 11 | decision.py + tests | decision.py, test_decision.py |
| 12 | guardrails.py + tests | guardrails.py, test_guardrails.py |
| 13 | price.py | price.py |
| 14 | market.py | market.py |
| 15 | main.py | main.py, __main__.py |
| 16 | final verification | — |
