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
    IGNORE = "IGNORE"


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


class ReviewStatus(str, Enum):
    OK = "OK"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"


class ReviewPolarity(str, Enum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEUTRAL = "NEUTRAL"
    UNCLEAR = "UNCLEAR"


class PromotionStatus(str, Enum):
    REJECTED = "REJECTED"
    PROMOTED = "PROMOTED"
    ERROR = "ERROR"


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
    kospi_breadth_ratio: Optional[float] = None
    kosdaq_breadth_ratio: Optional[float] = None
    vkospi: Optional[float] = None
    macro_overall_regime: Optional[str] = None
    macro_overall_confidence: Optional[float] = None
    macro_kr_regime: Optional[str] = None
    macro_crypto_regime: Optional[str] = None
    macro_position_multiplier: Optional[float] = None


class ContextCard(BaseModel):
    ret_today: Optional[float] = None
    ret_1d: Optional[float] = None
    ret_3d: Optional[float] = None
    pos_20d: Optional[float] = None
    gap: Optional[float] = None
    adv_value_20d: Optional[float] = None
    spread_bps: Optional[float] = None
    vol_pct_20d: Optional[float] = None
    intraday_value_vs_adv20d: Optional[float] = None
    top_ask_notional: Optional[float] = None
    quote_temp_stop: Optional[bool] = None
    quote_liquidation_trade: Optional[bool] = None
    prior_volume_rate: Optional[float] = None  # 전일대비 거래량 비율 (e.g. 200.0 = 2배)
    rsi_14: Optional[float] = None
    macd_hist: Optional[float] = None
    bb_position: Optional[float] = None  # 볼린저밴드 위치 (0=하단, 100=상단)
    atr_14: Optional[float] = None  # ATR-14 변동성 (현재가 대비 %)
    support_price_5d: Optional[float] = None
    support_price_20d: Optional[float] = None
    support_reference_px: Optional[float] = None


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
    dorg: str = ""  # 공시/뉴스 제공기관
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
    promotion_original_event_id: Optional[str] = None
    promotion_original_bucket: Optional[Bucket] = None
    promotion_confidence: Optional[int] = None
    promotion_policy: Optional[str] = None
    # Inline decision (유실 방지: event record에도 decision 결과 포함)
    decision_action: Optional[str] = None
    decision_confidence: Optional[int] = None
    decision_size_hint: Optional[str] = None
    decision_reason: Optional[str] = None
    guardrail_result: Optional[str] = None


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
    decision_source: str = "LLM"  # "LLM" | "CACHE" | "RULE_FALLBACK" | "LLM_FALLBACK_HYBRID" | "RULE_PREFLIGHT"


class PriceSnapshot(BaseModel):
    type: str = "price_snapshot"
    mode: str = "live"  # "live" | "paper" | "dry_run"
    schema_version: str
    run_id: str
    event_id: str
    horizon: Literal["t0", "t+30s", "t+1m", "t+2m", "t+5m", "t+10m", "t+15m", "t+20m", "t+30m", "close"]
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


class UnknownInboxRecord(BaseModel):
    type: str = "unknown_inbox"
    event_id: str
    detected_at: datetime
    runtime_mode: str
    ticker: str
    corp_name: str
    headline: str
    rss_link: str
    source: str
    original_bucket: Bucket = Bucket.UNKNOWN


class UnknownReviewRecord(BaseModel):
    type: str = "unknown_review"
    event_id: str
    reviewed_at: datetime
    runtime_mode: str
    headline_only: bool
    review_iteration: str = "headline_initial"
    review_status: ReviewStatus
    suggested_bucket: Bucket = Bucket.UNKNOWN
    polarity: ReviewPolarity = ReviewPolarity.UNCLEAR
    confidence: int = Field(default=0, ge=0, le=100)
    promote_now: bool = False
    needs_article_body: bool = False
    body_fetch_status: str = "not_requested"
    body_source: str = ""
    body_text_chars: int = Field(default=0, ge=0)
    re_reviewed: bool = False
    canonical_headline: str = ""
    reason: str = ""
    reason_codes: list[str] = Field(default_factory=list)
    keyword_candidates: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    error: str = ""


class UnknownPromotionRecord(BaseModel):
    type: str = "unknown_promotion"
    event_id: str
    derived_event_id: str = ""
    promoted_at: datetime
    runtime_mode: str
    review_status: ReviewStatus
    original_bucket: Bucket = Bucket.UNKNOWN
    suggested_bucket: Bucket = Bucket.UNKNOWN
    confidence: int = Field(default=0, ge=0, le=100)
    promotion_status: PromotionStatus
    promotion_policy: str = ""
    gate_reasons: list[str] = Field(default_factory=list)
    decision_action: Optional[Action] = None
    skip_stage: Optional[SkipStage] = None
    skip_reason: str = ""
    error: str = ""
