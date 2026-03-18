"""LLM 1-shot Decision Engine with caching."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
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


class LlmCallError(Exception):
    """LLM call failed (non-timeout, e.g. auth, rate limit, network)."""


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
    ctx_micro = (
        f"adv_20d={adv_display} spread_bps={ctx.spread_bps} vol_pct_20d={ctx.vol_pct_20d} "
        f"intraday_value_vs_adv20d={ctx.intraday_value_vs_adv20d} "
        f"top_ask_notional={ctx.top_ask_notional} "
        f"temp_stop={ctx.quote_temp_stop} liquidation_trade={ctx.quote_liquidation_trade}"
    )

    return f"""event: [{bucket.value}] {corp_name}, {headline}
corp: {corp_name}({ticker})
detected_at: {detected_at} KST

ctx_price: {ctx_price}
ctx_micro: {ctx_micro}

constraints: max_pos=10% no_overnight=true daily_loss_remaining=85%

strategy_guide:
- 수주·공급계약: 매출 대비 10%+ → BUY(80+), 5-10% → BUY(70), <5% → SKIP
- 바이오/제약 FDA허가·임상3상 성공 → BUY(85+), 임상1-2상 결과 → BUY(70)
- 유증·CB발행 → SKIP (희석 리스크)
- 자사주 소각·취득 → BUY(70-75), 주주환원 호재
- 대형 M&A·합작법인 → BUY(70), 뉴스 초기 반응 후 차익실현 주의
- 목표가 상향 리포트 → BUY(65-70), 이미 시장 반영 가능
- 이미 당일 5%+ 상승(ret_today>5) → SKIP, 추격 매수 위험
- spread_bps>30 → size_hint=S, 슬리피지 주의
- confidence는 실제 수익 확률 반영: 85+=매우 확신, 70-80=보통, <65=SKIP 권장
- 같은 종목 반복 뉴스(중복 보도) → 처음만 BUY, 후속은 SKIP

task: decide BUY or SKIP. respond with ONLY a JSON object.
example: {{"action":"BUY","confidence":85,"size_hint":"L","reason":"FDA 허가 획득, 바이오 강한 촉매"}}
fields: action="BUY" or "SKIP", confidence=0-100, size_hint="S" or "M" or "L", reason=max 100 chars"""


def _parse_llm_response(raw: str) -> Optional[dict]:
    """Parse LLM JSON response, stripping backticks if present."""
    text = raw.strip()
    # Remove markdown code fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

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

    size_hint = data.get("size_hint")
    if size_hint not in ("S", "M", "L"):
        # size_hint 누락/잘못된 경우 confidence 기반 기본값 적용 (파싱 실패 방지)
        if isinstance(confidence, (int, float)):
            if confidence >= 80:
                data["size_hint"] = "L"
            elif confidence >= 50:
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


class DecisionEngine:
    """LLM 1-shot decision with caching."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._cache: dict[str, _CacheEntry] = {}
        self._last_sweep: float = time.monotonic()
        self._client: Optional[object] = None
        # In-flight dedup: same key requests await a single upstream LLM call.
        self._inflight: dict[str, asyncio.Task[DecisionRecord]] = {}
        self._llm_semaphore = asyncio.Semaphore(max(1, config.llm_max_concurrency))

    def _get_client(self):
        if self._client is None:
            from anthropic import AsyncAnthropic
            self._client = AsyncAnthropic(
                api_key=self._config.anthropic_api_key,
                timeout=self._config.llm_sdk_timeout_s,
            )
        return self._client

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
            prompt = _build_prompt(bucket, headline, ticker, corp_name, detected_at_str, ctx)
            client = self._get_client()

            last_err: Exception | None = None
            resp = None
            t0 = time.monotonic()
            for attempt in range(2):  # 1회 재시도
                try:
                    async with self._llm_semaphore:
                        resp = await asyncio.wait_for(
                            client.messages.create(
                                model=self._config.llm_model,
                                max_tokens=200,
                                temperature=0,
                                messages=[{"role": "user", "content": prompt}],
                            ),
                            timeout=self._config.llm_wait_for_s,
                        )
                    break
                except asyncio.TimeoutError as e:
                    last_err = e
                    if attempt == 0:
                        logger.info("LLM timeout (retry 1): %s", e)
                        await asyncio.sleep(1.0)
                        continue
                    logger.warning("LLM timeout (final): %s", e)
                    raise LlmTimeoutError(str(e)) from e
                except Exception as e:
                    last_err = e
                    if attempt == 0:
                        logger.info("LLM call error (retry 1): %s", e)
                        await asyncio.sleep(1.0)
                        continue
                    logger.warning("LLM call error (final): %s", e)
                    raise LlmCallError(str(e)) from e
            latency_ms = int((time.monotonic() - t0) * 1000)
            if resp is None:
                raise LlmCallError(f"LLM failed after retries: {last_err}")

            try:
                raw_text = resp.content[0].text
            except (IndexError, AttributeError) as e:
                logger.warning("LLM response structure unexpected: %s", e)
                raise LlmParseError(f"unexpected response structure: {e}") from e

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
