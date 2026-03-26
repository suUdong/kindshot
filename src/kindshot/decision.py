"""LLM 1-shot Decision Engine with caching."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from kindshot.config import Config
from kindshot.llm_client import LlmClient, LlmCallError, LlmTimeoutError
from kindshot.models import (
    Action,
    Bucket,
    ContextCard,
    DecisionRecord,
    MarketContext,
    SizeHint,
)

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_STRATEGY_PROMPT: Optional[str] = None


def _load_strategy_prompt() -> str:
    global _STRATEGY_PROMPT
    if _STRATEGY_PROMPT is None:
        _STRATEGY_PROMPT = (_PROMPTS_DIR / "decision_strategy.txt").read_text(encoding="utf-8")
    return _STRATEGY_PROMPT


# Re-export from llm_client for backward compatibility
# LlmTimeoutError and LlmCallError are imported above
class LlmParseError(Exception):
    """LLM response could not be parsed."""


_MAX_HEADLINE_LEN = 500


def _build_prompt(
    bucket: Bucket,
    headline: str,
    ticker: str,
    corp_name: str,
    detected_at: str,
    ctx: ContextCard,
    market_ctx: Optional[MarketContext] = None,
) -> str:
    # Truncate headline to prevent prompt injection via excessively long input
    headline = headline[:_MAX_HEADLINE_LEN]
    rsi_str = f" rsi_14={ctx.rsi_14}" if ctx.rsi_14 is not None else ""
    macd_str = f" macd_hist={ctx.macd_hist}" if ctx.macd_hist is not None else ""
    ctx_price = (
        f"ret_today={ctx.ret_today} ret_1d={ctx.ret_1d} ret_3d={ctx.ret_3d} "
        f"pos_20d={ctx.pos_20d} gap={ctx.gap}{rsi_str}{macd_str}"
    )
    adv_display = f"{ctx.adv_value_20d/1e8:.0f}억" if ctx.adv_value_20d else "N/A"
    ctx_micro = (
        f"adv_20d={adv_display} spread_bps={ctx.spread_bps} vol_pct_20d={ctx.vol_pct_20d} "
        f"intraday_value_vs_adv20d={ctx.intraday_value_vs_adv20d} "
        f"top_ask_notional={ctx.top_ask_notional} "
        f"temp_stop={ctx.quote_temp_stop} liquidation_trade={ctx.quote_liquidation_trade}"
    )

    # 시장 환경 요약
    market_line = ""
    if market_ctx:
        kospi = f"{market_ctx.kospi_change_pct:+.1f}%" if market_ctx.kospi_change_pct is not None else "N/A"
        kosdaq = f"{market_ctx.kosdaq_change_pct:+.1f}%" if market_ctx.kosdaq_change_pct is not None else "N/A"
        breadth = f"{market_ctx.kospi_breadth_ratio:.2f}" if market_ctx.kospi_breadth_ratio is not None else "N/A"
        market_line = f"\nctx_market: KOSPI={kospi} KOSDAQ={kosdaq} breadth_ratio={breadth}"

    strategy = _load_strategy_prompt()

    return f"""event: [{bucket.value}] {corp_name}, {headline}
corp: {corp_name}({ticker})
detected_at: {detected_at} KST

ctx_price: {ctx_price}
ctx_micro: {ctx_micro}{market_line}

constraints: max_pos=10% no_overnight=true daily_loss_remaining=85%

{strategy}"""


def _parse_llm_response(raw: str) -> Optional[dict]:
    """Parse LLM JSON response, stripping backticks if present."""
    text = raw.strip()
    # Remove markdown code fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    # LLM이 {} 없이 bare key-value JSON 반환하는 경우 보정
    if text and not text.startswith("{") and text.startswith('"'):
        text = "{" + text + "}"

    def _load_json_candidate(candidate: str) -> Optional[dict]:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    data = _load_json_candidate(text)
    if data is None:
        # Some models add one-line commentary before/after the JSON.
        start = text.find("{")
        while start >= 0:
            depth = 0
            in_string = False
            escape = False
            for idx in range(start, len(text)):
                ch = text[idx]
                if escape:
                    escape = False
                    continue
                if ch == "\\" and in_string:
                    escape = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        data = _load_json_candidate(text[start:idx + 1])
                        if data is not None:
                            break
            if data is not None:
                break
            start = text.find("{", start + 1)
        if data is None:
            return None

    # Validate required fields
    action = data.get("action")
    if action not in ("BUY", "SKIP"):
        return None

    confidence = data.get("confidence")
    if not isinstance(confidence, (int, float)) or not (0 <= confidence <= 100):
        return None

    # Hard safety net: BUY with confidence < 75 is auto-converted to SKIP.
    # The LLM sometimes outputs BUY(72) despite prompt instructions forbidding it.
    if action == "BUY" and int(confidence) < 75:
        logger.warning("LLM returned BUY with confidence %d < 75, forcing SKIP", int(confidence))
        data["action"] = "SKIP"

    size_hint = data.get("size_hint")
    if size_hint not in ("S", "M", "L"):
        # size_hint 누락/잘못된 경우 confidence 기반 기본값 적용 (파싱 실패 방지)
        if isinstance(confidence, (int, float)):
            if confidence >= 80:
                data["size_hint"] = "L"
            elif confidence >= 75:
                data["size_hint"] = "M"
            else:
                data["size_hint"] = "S"
        else:
            return None

    reason = data.get("reason", "")
    if not isinstance(reason, str):
        reason = str(reason)
    # Truncate to max_length matching DecisionRecord.reason Field(max_length=100)
    data["reason"] = reason[:100]

    return data


@dataclass
class _CacheEntry:
    result: DecisionRecord
    expires_at: float


_HIGH_CONVICTION_KEYWORDS: list[tuple[str, int]] = [
    # 주주환원: 가장 신뢰도 높음
    ("자사주 소각", 80), ("자사주소각", 80), ("자기주식 소각", 80), ("자기주식소각", 80),
    ("자사주 매입", 79), ("자사주매입", 79), ("자기주식 취득", 79), ("자기주식취득", 79),
    ("공개매수", 80), ("대항 공개매수", 82),
    ("경영권 분쟁", 80), ("경영권분쟁", 80), ("위임장 대결", 80),
    # 실적 서프라이즈
    ("어닝 서프라이즈", 79), ("어닝서프라이즈", 79),
    ("사상최대 실적", 78), ("사상 최대 실적", 78), ("사상최대 영업이익", 78), ("사상 최대 영업이익", 78),
    ("흑자전환", 78), ("흑자 전환", 78),
    ("깜짝 실적", 78),
    # 계약/수주
    ("대형 계약", 78), ("대형계약", 78),
    ("수주", 77), ("공급계약", 77), ("공급 계약", 77),
    # 바이오
    ("임상 3상 성공", 79), ("임상3상 성공", 79), ("FDA 승인", 80), ("FDA승인", 80),
    ("품목허가 승인", 78), ("식약처 허가", 78),
    ("특허", 77),
    # M&A
    ("인수", 77), ("지분 취득", 77), ("지분취득", 77),
]


def _rule_based_decide(
    bucket: Bucket,
    headline: str,
    keyword_hits: list[str],
    ctx: ContextCard,
) -> dict:
    """LLM 없이 키워드 + quant context로 BUY/SKIP 결정.

    보수적 전략: POS_STRONG 중 고확신 키워드만 BUY, 나머지 SKIP.
    """
    if bucket != Bucket.POS_STRONG:
        return {"action": "SKIP", "confidence": 70, "size_hint": "S",
                "reason": "rule_fallback:weak_bucket"}

    # 고확신 키워드 매칭 (가장 높은 confidence 사용)
    best_conf = 0
    matched_kw = ""
    for kw, conf in _HIGH_CONVICTION_KEYWORDS:
        if any(kw in hit for hit in keyword_hits) or kw in headline:
            if conf > best_conf:
                best_conf = conf
                matched_kw = kw

    if best_conf < 75:
        return {"action": "SKIP", "confidence": 72, "size_hint": "S",
                "reason": "rule_fallback:no_high_conviction_kw"}

    # Quant 보정: 당일 이미 3%+ 상승이면 추격매수 방지
    if ctx.ret_today is not None and ctx.ret_today > 3.0:
        return {"action": "SKIP", "confidence": best_conf - 10, "size_hint": "S",
                "reason": f"rule_fallback:chase_buy ret={ctx.ret_today:.1f}%"}

    # Size hint
    if best_conf >= 80:
        size = "M"  # fallback에서는 L 안 줌 (보수적)
    elif best_conf >= 77:
        size = "S"
    else:
        size = "S"

    return {"action": "BUY", "confidence": best_conf, "size_hint": size,
            "reason": f"rule_fallback:{matched_kw}"}


class DecisionEngine:
    """LLM 1-shot decision with caching + rule-based fallback."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._cache: dict[str, _CacheEntry] = {}
        self._last_sweep: float = time.monotonic()
        self._llm = LlmClient(config)
        # In-flight dedup: same key requests await a single upstream LLM call.
        self._inflight: dict[str, asyncio.Task[DecisionRecord]] = {}

    def _cache_key(self, ticker: str, headline: str, bucket: Bucket, ctx: ContextCard) -> str:
        # Include context card data so market changes invalidate cache
        ctx_str = (
            f"{ctx.adv_value_20d}|{ctx.spread_bps}|{ctx.ret_today}|"
            f"{ctx.intraday_value_vs_adv20d}|{ctx.top_ask_notional}|"
            f"{ctx.quote_temp_stop}|{ctx.quote_liquidation_trade}"
        )
        h = hashlib.sha256(f"{headline}|{ctx_str}".encode()).hexdigest()[:16]
        return f"{ticker}:{h}:{bucket.value}"

    def _sweep_cache(self) -> None:
        now = time.monotonic()
        # 크기 제한: 1024 엔트리 초과 시 강제 sweep
        force = len(self._cache) > 1024
        if not force and now - self._last_sweep < self._config.llm_cache_sweep_s:
            return
        expired = [k for k, v in self._cache.items() if v.expires_at < now]
        for k in expired:
            del self._cache[k]
        # 여전히 초과 시 가장 오래된 절반 제거
        if len(self._cache) > 1024:
            sorted_keys = sorted(self._cache, key=lambda k: self._cache[k].expires_at)
            for k in sorted_keys[: len(sorted_keys) // 2]:
                del self._cache[k]
        self._last_sweep = now

    def _as_cache_result(self, source: DecisionRecord, run_id: str) -> DecisionRecord:
        return DecisionRecord(
            schema_version=source.schema_version,
            run_id=run_id or source.run_id,
            event_id=source.event_id,
            decided_at=datetime.now(timezone.utc),
            llm_model=source.llm_model,
            llm_latency_ms=0,
            action=source.action,
            confidence=source.confidence,
            size_hint=source.size_hint,
            reason=source.reason,
            decision_source="CACHE",
        )

    def fallback_decide(
        self,
        ticker: str,
        headline: str,
        bucket: Bucket,
        ctx: ContextCard,
        keyword_hits: list[str],
        *,
        run_id: str = "",
        schema_version: str = "0.1.2",
    ) -> DecisionRecord:
        """Rule-based fallback when LLM is unavailable."""
        parsed = _rule_based_decide(bucket, headline, keyword_hits, ctx)

        record = DecisionRecord(
            schema_version=schema_version,
            run_id=run_id,
            event_id="",
            decided_at=datetime.now(timezone.utc),
            llm_model="rule_fallback",
            llm_latency_ms=0,
            action=Action(parsed["action"]),
            confidence=int(parsed["confidence"]),
            size_hint=SizeHint(parsed["size_hint"]),
            reason=parsed.get("reason", ""),
            decision_source="RULE_FALLBACK",
        )
        return record

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
        market_ctx: Optional[MarketContext] = None,
    ) -> DecisionRecord:
        """Call LLM for BUY/SKIP decision.

        Raises:
            LlmTimeoutError: upstream timeout.
            LlmCallError: upstream call failure.
            LlmParseError: response parse/shape failure.
        """

        self._sweep_cache()
        key = self._cache_key(ticker, headline, bucket, ctx)

        # Cache hit
        if key in self._cache and self._cache[key].expires_at > time.monotonic():
            return self._as_cache_result(self._cache[key].result, run_id)

        # In-flight dedup for same key to prevent duplicate API calls.
        inflight = self._inflight.get(key)
        if inflight is not None:
            try:
                shared = await inflight
            except (LlmTimeoutError, LlmCallError, LlmParseError) as e:
                # Re-raise per-caller for clearer local traceback context.
                raise type(e)(str(e)) from e
            return self._as_cache_result(shared, run_id)

        async def _invoke_uncached() -> DecisionRecord:
            prompt = _build_prompt(bucket, headline, ticker, corp_name, detected_at_str, ctx, market_ctx)

            raw_text, latency_ms = await self._llm.call(prompt, max_tokens=200)

            parsed = _parse_llm_response(raw_text)
            if parsed is None:
                logger.warning("LLM parse failed: %s", raw_text[:200])
                raise LlmParseError(raw_text[:200])

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

            self._cache[key] = _CacheEntry(
                result=record,
                expires_at=time.monotonic() + self._config.llm_cache_ttl_s,
            )
            return record

        task = asyncio.create_task(_invoke_uncached())
        self._inflight[key] = task
        try:
            return await task
        finally:
            if self._inflight.get(key) is task:
                del self._inflight[key]
