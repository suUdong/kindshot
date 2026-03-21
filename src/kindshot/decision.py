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
    MarketContext,
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
    market_ctx: Optional[MarketContext] = None,
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

    # 시장 환경 요약
    market_line = ""
    if market_ctx:
        kospi = f"{market_ctx.kospi_change_pct:+.1f}%" if market_ctx.kospi_change_pct is not None else "N/A"
        kosdaq = f"{market_ctx.kosdaq_change_pct:+.1f}%" if market_ctx.kosdaq_change_pct is not None else "N/A"
        breadth = f"{market_ctx.kospi_breadth_ratio:.2f}" if market_ctx.kospi_breadth_ratio is not None else "N/A"
        market_line = f"\nctx_market: KOSPI={kospi} KOSDAQ={kosdaq} breadth_ratio={breadth}"

    return f"""event: [{bucket.value}] {corp_name}, {headline}
corp: {corp_name}({ticker})
detected_at: {detected_at} KST

ctx_price: {ctx_price}
ctx_micro: {ctx_micro}{market_line}

constraints: max_pos=10% no_overnight=true daily_loss_remaining=85%

strategy_guide:
- 수주·공급계약: 매출 대비 10%+ 대형수주 → BUY(88,L), 5-10% 중형 → BUY(78,M), <5% 소규모 → SKIP(60)
- 수주·공급계약 공시인데 ret_today>2%: 이미 시장 반영 → SKIP(55), 추격 위험
- 수주·공급계약 금액 불명 또는 수백억 미만 소규모: confidence 상한 70 → 사실상 SKIP 유도
- 바이오/제약 FDA허가·임상3상 성공 → BUY(92,L), 임상2상 완료 → BUY(80,M), 임상1상 결과 → BUY(68,S)
- 유증·CB발행 → SKIP (희석 리스크)
- 자사주 소각·취득 → BUY(78,M), 주주환원 호재
- 대형 M&A·합작법인 → BUY(80,M), 뉴스 초기 반응 후 차익실현 주의
- 이미 당일 3%+ 상승(ret_today>3) and POS_WEAK → SKIP, 추격 매수 위험
- 이미 당일 5%+ 상승(ret_today>5) → SKIP, POS_STRONG도 추격 금지
- spread_bps>30 → size_hint 한 단계 낮춤 (L→M, M→S)
- 같은 종목 반복 뉴스(중복 보도) → 처음만 BUY, 후속은 SKIP

market_adjustment (반드시 적용):
- KOSPI<-2%: confidence -5, size_hint 한 단계 낮춤 (L→M, M→S, S→SKIP)
- KOSPI<-1% and breadth_ratio<0.35: confidence -3
- KOSPI>+1% and breadth_ratio>0.6: confidence +3 (상한 95)

confidence & size_hint 매핑 (반드시 준수):
- 90-95: 확실한 촉매(FDA승인, 대형수주 매출10%+, 사상최대실적) → size_hint=L
- 82-89: 강한 촉매(임상2상성공, 대형공급계약, 기술수출) → size_hint=L
- 75-81: 보통 촉매(M&A, 자사주소각, 중형계약) → size_hint=M
- 68-74: 약한 촉매(소규모 계약, 단순 모멘텀) → size_hint=S
- <68: 촉매 불충분 → SKIP

CRITICAL: confidence는 촉매 강도에 따라 68-95 범위에서 적극 차별화할 것. 매번 비슷한 값(예: 70-75 반복)은 금지. 각 이벤트의 고유한 촉매 강도를 정확히 반영하라.

concrete_examples (confidence 차별화 필수 — 각 예시의 차이에 주목):
- "A사, 5000억 규모 LNG선 수주" → BUY(88,L) 대형수주+매출비중 높음
- "B사, 바이오시밀러 FDA 승인" → BUY(93,L) 확실한 규제 촉매
- "C사, 자사주 500억원 소각 결정" → BUY(80,M) 대규모 주주환원
- "D사, 임상2상 유효성 확인" → BUY(82,L) 바이오 중기 촉매
- "E사, 300억 설비투자 결정" → BUY(70,S) 장기 투자, 단기 촉매 약함
- "F사, MOU 체결…구체적 계약 미정" → SKIP(55) 실행 불확실
- "G사, 목표가 상향 리포트" → SKIP(40) 리포트일 뿐, 촉매 아님
- "H사, 200억원 규모 용역계약" → SKIP(62) 소규모+단기 임팩트 미미

decision_bias:
- POS_STRONG 이벤트: 뉴스 자체가 이미 긍정 필터를 통과함. 명확한 SKIP 근거(추격매수, 희석, 실행불확실, 소규모)가 없으면 BUY 권장.
- POS_WEAK 이벤트: 신호가 약하므로 보수적. confidence<70이면 SKIP. ret_today>3%이면 SKIP.
- SKIP 선택 시 reason에 구체적 근거 필수 (예: "ret_today=7% 추격매수 위험", "MOU만으로 실행 불확실")
- "불확실하다", "판단 어렵다" 같은 모호한 이유는 금지. 구체적 데이터/사실 기반으로 판단.

task: decide BUY or SKIP. respond with ONLY a JSON object (no markdown, no code fences).
example: {{"action":"BUY","confidence":85,"size_hint":"L","reason":"FDA 허가 획득, 바이오 강한 촉매"}}
fields: action="BUY" or "SKIP", confidence=0-100, size_hint="S" or "M" or "L", reason=max 100 chars"""


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
