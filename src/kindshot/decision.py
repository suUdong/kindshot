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


class LlmTimeoutError(Exception):
    """LLM call timed out or failed."""


class LlmParseError(Exception):
    """LLM response could not be parsed."""


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
            raise LlmTimeoutError(str(e)) from e

        raw_text = resp.content[0].text
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

        # Cache
        self._cache[key] = _CacheEntry(
            result=record,
            expires_at=time.monotonic() + self._config.llm_cache_ttl_s,
        )

        return record
