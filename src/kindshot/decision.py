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


# Re-export from llm_client for backward compatibility
# LlmTimeoutError and LlmCallError are imported above
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

    return f"""event: [{bucket.value}] {corp_name}, {headline}
corp: {corp_name}({ticker})
detected_at: {detected_at} KST

ctx_price: {ctx_price}
ctx_micro: {ctx_micro}{market_line}

constraints: max_pos=10% no_overnight=true daily_loss_remaining=85%

strategy_guide:
- 수주·공급계약: 매출 대비 10%+ 대형수주 → BUY(88,L), 5-10% 중형 → BUY(78,M), <5% 소규모 → SKIP(55)
- 수주·공급계약 금액 불명 또는 100억 미만 → SKIP(50), 시장 임팩트 부족
- 수주·공급계약 금액 100억-500억 → confidence 상한 72, size_hint=S
- 바이오/제약 FDA허가·임상3상 성공 → BUY(92,L), 임상2상 완료 → BUY(80,M), 임상1상 결과 → SKIP(60)
- 유증·CB발행 → SKIP (희석 리스크)
- 자사주 소각·취득 "결정/공시" → BUY(78,M). 단, "추진/검토/계획" → SKIP(55) 확정 아님
- 대형 M&A·합작법인 설립 확정 → BUY(80,M). 단, "협의 중/검토 중" → SKIP(50) 불확실
- spread_bps>30 → size_hint 한 단계 낮춤 (L→M, M→S)
- 같은 종목 반복 뉴스(중복 보도) → 처음만 BUY, 후속은 SKIP

SKIP_필수_패턴 (아래 해당 시 반드시 SKIP):
- ret_today>3%: 이미 시장에 반영됨. 추격매수 금지. 예외 없음.
- ret_today>2% and 뉴스기사(공시 아님): 이미 반영+촉매 약함 → SKIP(45).
- 뉴스 기사/분석 리포트/전망 기사 → SKIP(40). 공시가 아닌 기사는 촉매 아님.
  감별법: "[TOP's Pick]", "[카드]", "[종합]", "전망", "보인다", "분석", "'X' 넘어", "파죽지세" 등 수식 표현, CEO 인터뷰 인용("..."), 마침표 3개(...) 포함 → 기사.
  공시 감별: "체결", "결정", "공시", "수주공시", "단일판매", "규모 공급계약" + 구체 금액 → 공시.
- 의도/계획 표현: "추진", "검토", "협의 중", "계획", "방안", "논의", "예정", "나서겠다" → SKIP(55). 확정된 사실만 BUY.
- 생산라인 전환/구조조정/조직개편 → SKIP(50). 신규 수주/계약이 아님.
- 1척 추가, 소량 추가 수주 등 점진적 물량 → SKIP(55). 시장에 이미 반영된 반복 수주.
- 주총/정관 변경/이사 선임 등 일반 기업 거버넌스 → SKIP(40).
- 기업가치 평가/시가총액 전망 → SKIP(40). 실제 거래가 아닌 추정.

adv_filter (반드시 적용 — 대형주는 뉴스 반영이 이미 완료):
- adv_20d > 5000억 (초대형주): confidence 상한 72 → 대부분 SKIP. "sell the news" 패턴 빈발.
- adv_20d 2000~5000억 (대형주): confidence -3. 뉴스 반영 빠름, edge 제한적.
- adv_20d 500~2000억 (중형주): 가장 유리한 구간. 뉴스 반영 지연 + 유동성 충분.
- adv_20d < 500억: quant 필터에서 제외됨 (ADV_TOO_LOW).

trend_filter (반드시 적용):
- ret_3d < -5%: 하락 추세 종목. confidence -10. 대형 공시라도 반등 실패 확률 높음.
- ret_3d < -3% and adv_20d > 1000억: 대형주 하락 추세. confidence -5. "sell the news" 위험.
- pos_20d < 20: 20일 중 대부분 하락. confidence -5. 추세 역행 진입 위험.

technical_indicators (참고 — 있을 때만 적용):
- rsi_14 > 75: 과매수 구간. confidence -5. 단기 조정 가능성.
- rsi_14 < 30: 과매도 구간. 뉴스 촉매와 결합 시 반등 가능성 → confidence +3.
- macd_hist > 0: 상승 모멘텀. 촉매와 방향 일치 → 긍정 신호.
- macd_hist < 0: 하락 모멘텀. 촉매가 추세 역행 → confidence -3.

market_adjustment (반드시 적용):
- KOSPI<-2%: confidence -5, size_hint 한 단계 낮춤 (L→M, M→S, S→SKIP)
- KOSPI<-1% and breadth_ratio<0.35: confidence -3
- KOSPI>+1% and breadth_ratio>0.6: confidence +3 (상한 95)

confidence & size_hint 매핑 (반드시 준수):
- 90-95: 확실한 촉매(FDA승인, 대형수주 매출10%+, 사상최대실적) → size_hint=L
- 82-89: 강한 촉매(임상2상성공, 대형공급계약 매출5%+, 기술수출) → size_hint=L
- 75-81: 보통 촉매(확정된 M&A, 자사주소각 결정, 중형계약 매출3-5%) → size_hint=M
- 68-74: 약한 촉매(소규모 확정 계약) → size_hint=S, 대부분 SKIP이 나음
- <68: 촉매 불충분 또는 불확실 → 반드시 SKIP

CRITICAL_CONFIDENCE_RULES:
1. confidence 70-75 사이 값을 남발하면 안 됨. 촉매가 확실하면 80+, 불확실하면 60 이하로 명확히 분리.
2. 같은 세션에서 모든 이벤트에 동일 confidence를 부여하는 것은 시스템 실패. 반드시 차별화.
3. "POS_STRONG이니까 BUY"는 근거 불충분. 구체적 금액, 매출비중, 확정 여부를 기준으로 판단.
4. 헤드라인이 공시(DART 공시, "체결", "결정")인지 뉴스기사(분석, 전망, 인터뷰)인지 구분. 뉴스기사는 촉매가 아님.

실전_사례 (실제 거래 결과 기반 — 반드시 학습):

WIN 사례:
- "HD현대중공업, 8237억원 규모 공급계약(컨테이너선 10척) 체결" ret_today=-2.5% → BUY(85,L) → close +0.26%. 대형 확정 수주+하락 중 저가 매수.
- "알테오젠, 키트루다 SC 조성물 특허 미국 등록" ret_today=0.0% → BUY(88,L) → t+5m +3.68% TP. 바이오 규제 촉매+가격 미반영.

LOSS 사례 (이런 패턴 SKIP):
- "서진시스템, 2702억 규모 ESS 장비 공급계약" ret_today=+0.65% spread=21.5 → BUY(72,M) → close -2.30%. spread 넓고 ESS 섹터 변동성 큼. SKIP(60)이 정답.
- "삼성SDI, 1.5조 규모 ESS 공급 계약 체결" ret_today=+0.26% adv=3811억 → BUY(72,M) → close -1.61%. 대형주는 이미 시장에 반영. SKIP(55)이 정답.
- "포스코퓨처엠 1.01조 음극재 공급" ret_3d=-9.03% → BUY(72,M) → close -0.23%. 3일간 -9% 하락 추세에서 뉴스 효과 없음. SKIP(55)이 정답.
- "[TOP's Pick] 엔씨, 저스트플레이 인수" → BUY(72,M) → close -2.33%. 기사일 뿐 공시 아님. SKIP(35)이 정답.
- "RF시스템즈, 166억 규모 공급계약" ret_today=+19.3% → BUY(72,M) → close -9.50%. 이미 폭등+소규모 계약. SKIP(40)이 정답.

핵심 패턴:
- 대형 확정 수주 + 가격 미반영(ret_today<1%) = BUY 유력
- 기사/미확정 + 이미 상승(ret_today>2%) = SKIP 필수
- 하락 추세(ret_3d<-5%) + 대형주 = SKIP (sell the news)
- spread>20bps 종목 = confidence -5 추가 감점

decision_bias:
- POS_STRONG이라도 SKIP 근거(추격매수 ret>3%, 뉴스기사, 소규모, 미확정)가 있으면 반드시 SKIP.
- 확정된 대형 공시(매출비중 5%+, 체결/결정 문구)만 BUY. 나머지는 SKIP이 안전.
- POS_WEAK: confidence<75이면 SKIP. ret_today>2%이면 SKIP.
- SKIP reason에 구체적 근거 필수: "ret_today=4.5% 추격", "100억 미만 소규모", "'추진' 미확정" 등.
- "불확실하다" 같은 모호한 이유 금지.

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
