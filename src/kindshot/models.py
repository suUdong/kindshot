"""Pydantic models and enums for kindshot log records."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

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
    LLM_ERROR = "LLM_ERROR"
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


class MarketContext(BaseModel):
    kospi_change_pct: Optional[float] = None
    kosdaq_change_pct: Optional[float] = None
    vkospi: Optional[float] = None


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
    mode: str = "live"  # "live" | "paper" | "dry_run"
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
    market_ctx: Optional[MarketContext] = None


class DecisionRecord(BaseModel):
    type: str = "decision"
    mode: str = "live"  # "live" | "paper" | "dry_run"
    schema_version: str
    run_id: str
    event_id: str
    decided_at: datetime
    llm_model: str
    llm_latency_ms: int
    action: Action
    confidence: int = Field(ge=0, le=100)
    size_hint: SizeHint
    reason: str = Field(max_length=100)
    decision_source: str = "LLM"  # "LLM" | "CACHE"


class PriceSnapshot(BaseModel):
    type: str = "price_snapshot"
    mode: str = "live"  # "live" | "paper" | "dry_run"
    schema_version: str
    run_id: str
    event_id: str
    horizon: Literal["t0", "t+1m", "t+5m", "t+30m", "close"]
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
