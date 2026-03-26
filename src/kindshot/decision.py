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
_CONTRACT_FAMILY_KEYWORDS = ("수주", "공급계약", "공급 계약", "납품계약", "단일판매")
_CONTRACT_ARTICLE_MARKERS = (
    "[카드]", "[종합]", "[TOP's Pick]", "[클릭e종목]", "[특징주]",
    "파죽지세", "보인다", "전망", "목표",
    "추진", "검토", "계획", "예정", "Preview",
)
_INCREMENTAL_ORDER_MARKERS = ("1척 추가", "추가 수주", "추가 공급", "추가 계약")


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


def _is_contract_family(headline: str, keyword_hits: list[str]) -> bool:
    haystacks = [headline, *keyword_hits]
    return any(term in text for text in haystacks for term in _CONTRACT_FAMILY_KEYWORDS)


def _looks_like_contract_article(headline: str) -> bool:
    if any(marker in headline for marker in _CONTRACT_ARTICLE_MARKERS):
        return True
    if "넘어" in headline and any(mark in headline for mark in ('"', "'", "“", "”", "‘", "’")):
        return True
    return False


def _looks_like_incremental_order(headline: str) -> bool:
    return any(marker in headline for marker in _INCREMENTAL_ORDER_MARKERS)


def _contract_preflight_skip(
    headline: str,
    keyword_hits: list[str],
    ctx: ContextCard,
) -> dict[str, object] | None:
    if not _is_contract_family(headline, keyword_hits):
        return None

    if _looks_like_contract_article(headline):
        return {"confidence": 35, "reason": "rule_preflight:contract_article"}

    if _looks_like_incremental_order(headline):
        return {"confidence": 45, "reason": "rule_preflight:contract_incremental"}

    if ctx.ret_today is not None and ctx.ret_today >= 3.0:
        return {"confidence": 40, "reason": f"rule_preflight:contract_chase ret={ctx.ret_today:.1f}%"}

    if ctx.ret_3d is not None and ctx.ret_3d <= -5.0:
        # 대형 계약(1000억+ 또는 매출액대비 10%+)은 하락장에서도 LLM 판단 허용
        is_large, _ = _has_large_contract_signal(headline, keyword_hits)
        if not is_large:
            return {"confidence": 50, "reason": f"rule_preflight:contract_downtrend ret_3d={ctx.ret_3d:.1f}%"}

    if ctx.adv_value_20d is not None and ctx.adv_value_20d > 200_000_000_000:
        adv_eok = ctx.adv_value_20d / 1e8
        return {"confidence": 45, "reason": f"rule_preflight:contract_large_cap adv={adv_eok:.0f}억"}

    return None


@dataclass
class _CacheEntry:
    result: DecisionRecord
    expires_at: float


_HIGH_CONVICTION_KEYWORDS: list[tuple[str, int]] = [
    # 주주환원: 가장 신뢰도 높음 — 폭락장(-3%+)에서도 통과하도록 82+
    ("자사주 소각", 82), ("자사주소각", 82), ("자기주식 소각", 82), ("자기주식소각", 82),
    ("주식 소각 결정", 82), ("주식소각결정", 82), ("주식 소각", 82),
    ("주식소각 결정", 82), ("주식소각", 82), ("소각 결정", 82),
    ("자사주 매입", 80), ("자사주매입", 80), ("자기주식 취득", 80), ("자기주식취득", 80),
    ("자기주식취득 신탁계약", 80),
    ("공개매수", 82), ("대항 공개매수", 84),
    ("경영권 분쟁", 82), ("경영권분쟁", 82), ("위임장 대결", 82),
    # 실적 서프라이즈
    ("어닝 서프라이즈", 80), ("어닝서프라이즈", 80),
    ("사상최대 실적", 80), ("사상 최대 실적", 80), ("사상최대 영업이익", 80), ("사상 최대 영업이익", 80),
    ("사상최대 매출", 80), ("사상 최대 매출", 80),
    ("역대 최대 실적", 80), ("역대 최대 영업이익", 80), ("역대 최대 매출", 80),
    ("흑자전환", 80), ("흑자 전환", 80),
    ("깜짝 실적", 80),
    # 계약/수주 — 확정 체결 패턴 (KIND 정규공시 포함)
    ("대형 계약", 79), ("대형계약", 79), ("대규모 수주", 79), ("대규모수주", 79),
    ("설계 계약", 79), ("설계계약", 79),
    ("단일판매ㆍ공급계약", 77), ("단일판매·공급계약", 77), ("단일판매", 77),
    ("규모의 공급계약", 77), ("규모 공급계약", 77),
    ("해외 수주", 78), ("해외수주", 78),
    ("방산 수주", 78), ("방산수주", 78),
    ("독점 공급", 78), ("독점공급", 78),
    ("장기 공급", 77), ("장기공급", 77),
    # 바이오
    ("임상 3상 성공", 80), ("임상3상 성공", 80),
    ("FDA 승인", 82), ("FDA승인", 82), ("FDA 허가", 82), ("FDA허가", 82),
    ("품목허가 승인", 79), ("품목허가 획득", 79), ("허가 획득", 79), ("식약처 허가", 79),
    ("CDMO 계약", 79), ("CDMO계약", 79),
    ("기술수출 계약", 79), ("기술수출 계약 체결", 80), ("기술이전 계약", 79),
    ("라이선스 아웃", 79),
    ("임상 2상 완료", 78), ("임상2상 완료", 78), ("임상 2상 성공", 79), ("임상2상 성공", 79),
    # 특허 (주요국 등록)
    ("특허 등록", 78), ("특허등록", 78), ("특허 취득", 78), ("특허취득", 78),
    ("특허 확보", 77), ("특허확보", 77),
    # M&A — 구체적 표현만
    ("지분 취득", 79), ("지분취득", 79),
    ("인수 완료", 80), ("인수완료", 80), ("인수 결정", 79),
    # 첫 수주/매출 — 모멘텀 전환 신호
    ("최초 수주", 79), ("첫 수주", 79), ("첫 매출", 78),
    ("첫 양산", 78), ("양산 개시", 78),
    # 정부/조달 계약 — 안정적 매출
    ("정부 조달", 78), ("조달청 계약", 78),
    # 역대/사상 최대 수주
    ("역대 최대 수주", 80), ("사상 최대 수주", 80),
    ("수주 잔고 최대", 79), ("수주잔고 최대", 79),
]


def has_high_conviction_keyword(headline: str, keyword_hits: list[str], *, min_conf: int = 82) -> bool:
    """고확신 키워드(conf >= min_conf) 포함 여부. 하락장 바이패스 판단용."""
    for kw, conf in _HIGH_CONVICTION_KEYWORDS:
        if conf < min_conf:
            continue
        if kw in headline or any(kw in hit for hit in keyword_hits):
            return True
    return False


def _has_large_contract_signal(headline: str, keyword_hits: list[str]) -> tuple[bool, int]:
    """공급계약/수주/개발계약 + 금액 규모가 매출액 대비 큰 경우만 BUY.

    Returns (is_large, confidence).
    """
    _CONTRACT_KW = ("공급계약", "공급 계약", "수주", "개발 계약", "개발계약", "설계 계약", "설계계약")
    has_contract = any(
        kw in headline for kw in _CONTRACT_KW
    ) or any(
        kw in hit for hit in keyword_hits for kw in _CONTRACT_KW
    )
    if not has_contract:
        return False, 0

    # 매출액대비 N% — 높은 비율이면 고신뢰
    import re
    pct_match = re.search(r"매출액[대]?비\s*(\d+(?:\.\d+)?)\s*%", headline)
    if pct_match:
        pct = float(pct_match.group(1))
        if pct >= 10.0:
            return True, 80
        if pct >= 5.0:
            return True, 78
        # 5% 미만은 무시

    # 금액 파싱: 조 단위 + 억 단위
    # "2.89조원" → 28900억, "1.96조원" → 19600억
    cho_match = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*조", headline)
    if cho_match:
        amt_eok = float(cho_match.group(1).replace(",", "")) * 10000
        if amt_eok >= 10000:  # 1조+
            return True, 80
        if amt_eok >= 5000:  # 5000억+
            return True, 79

    amt_match = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*억", headline)
    if amt_match:
        amt = float(amt_match.group(1).replace(",", ""))
        if amt >= 1000:
            return True, 79
        if amt >= 500:
            return True, 77

    # 달러/USD 금액 파싱: 해외 수주
    # "1.5억달러", "150백만달러", "2억불", "100M USD"
    usd_eok_match = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*억\s*(?:달러|불|USD)", headline)
    if usd_eok_match:
        usd_eok = float(usd_eok_match.group(1).replace(",", ""))
        amt_eok = usd_eok * 1400 / 100  # 1억달러 ≈ 1400억원 (환율 1400원 기준)
        if amt_eok >= 1000:
            return True, 80
        if amt_eok >= 500:
            return True, 78

    usd_m_match = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(?:백만|million|M)\s*(?:달러|불|USD)", headline, re.IGNORECASE)
    if usd_m_match:
        usd_m = float(usd_m_match.group(1).replace(",", ""))
        amt_eok = usd_m * 1400 / 10000  # 100M USD ≈ 1400억원
        if amt_eok >= 1000:
            return True, 80
        if amt_eok >= 500:
            return True, 78

    # "단일판매ㆍ공급계약체결" 정규 공시는 금액 없어도 신뢰도 있음 (KIND 공시)
    if "단일판매" in headline and "공급계약" in headline:
        return True, 77

    return False, 0


_ARTICLE_MARKERS = (
    "전망", "보인다", "기대", "파죽지세", "분석", "평가",
    "목표", "수혜", "기대감", "가속화", "본격화",
    "CEO ", "대표 ", "회장 ", "의장 ",
    "Preview", "preview", "리뷰", "프리뷰",
    "추진", "검토", "계획", "협의 중", "논의", "예정",
    "우려", "주목", "관심", "포인트", "키워드",
)


def has_article_pattern(headline: str) -> bool:
    """헤드라인에 기사/미확정 패턴이 있으면 True. LLM BUY에도 post-check 적용."""
    return any(marker in headline for marker in _ARTICLE_MARKERS)


def _rule_based_decide(
    bucket: Bucket,
    headline: str,
    keyword_hits: list[str],
    ctx: ContextCard,
) -> dict:
    """LLM 없이 키워드 + quant context로 BUY/SKIP 결정.

    POS_STRONG: 고확신 키워드 → BUY
    POS_WEAK: 매우 고확신(conf>=80) 키워드만 BUY (보수적)
    기타: SKIP
    """
    if bucket not in (Bucket.POS_STRONG, Bucket.POS_WEAK):
        return {"action": "SKIP", "confidence": 70, "size_hint": "S",
                "reason": "rule_fallback:weak_bucket"}

    # 기사 패턴 감지: rule_fallback은 LLM보다 보수적이어야 함
    if any(marker in headline for marker in _ARTICLE_MARKERS):
        return {"action": "SKIP", "confidence": 55, "size_hint": "S",
                "reason": "rule_fallback:article_pattern"}

    # 고확신 키워드 매칭 (가장 높은 confidence 사용)
    best_conf = 0
    matched_kw = ""
    for kw, conf in _HIGH_CONVICTION_KEYWORDS:
        if any(kw in hit for hit in keyword_hits) or kw in headline:
            if conf > best_conf:
                best_conf = conf
                matched_kw = kw

    # 대형 계약/수주: 금액 규모 기반 판단
    is_large, contract_conf = _has_large_contract_signal(headline, keyword_hits)
    if is_large and contract_conf > best_conf:
        best_conf = contract_conf
        matched_kw = "대형계약"

    # POS_WEAK은 매우 고확신(80+)만 BUY, POS_STRONG은 76+
    min_conf = 80 if bucket == Bucket.POS_WEAK else 76

    if best_conf < min_conf:
        reason = "rule_fallback:no_high_conviction_kw"
        if bucket == Bucket.POS_WEAK and best_conf >= 77:
            reason = "rule_fallback:pos_weak_below_80"
        return {"action": "SKIP", "confidence": 72, "size_hint": "S",
                "reason": reason}

    # Quant 보정: 당일 이미 2%+ 상승이면 추격매수 방지
    if ctx.ret_today is not None and ctx.ret_today > 2.0:
        return {"action": "SKIP", "confidence": best_conf - 10, "size_hint": "S",
                "reason": f"rule_fallback:chase_buy ret={ctx.ret_today:.1f}%"}

    # Size hint — POS_WEAK은 한단계 보수적
    if bucket == Bucket.POS_WEAK:
        size = "S"
    elif best_conf >= 80:
        size = "M"  # fallback에서는 L 안 줌 (보수적)
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

    def _preflight_decide(
        self,
        headline: str,
        ctx: ContextCard,
        keyword_hits: list[str],
        *,
        run_id: str = "",
        schema_version: str = "0.1.2",
    ) -> DecisionRecord | None:
        parsed = _contract_preflight_skip(headline, keyword_hits, ctx)
        if parsed is None:
            return None

        return DecisionRecord(
            schema_version=schema_version,
            run_id=run_id,
            event_id="",
            decided_at=datetime.now(timezone.utc),
            llm_model="rule_preflight",
            llm_latency_ms=0,
            action=Action.SKIP,
            confidence=int(parsed["confidence"]),
            size_hint=SizeHint.S,
            reason=str(parsed["reason"]),
            decision_source="RULE_PREFLIGHT",
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
        keyword_hits: Optional[list[str]] = None,
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
        preflight = self._preflight_decide(
            headline,
            ctx,
            list(keyword_hits or []),
            run_id=run_id,
            schema_version=schema_version,
        )
        if preflight is not None:
            return preflight

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
