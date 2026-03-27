"""LLM 1-shot Decision Engine with caching."""

from __future__ import annotations

import asyncio
from collections import Counter
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
from kindshot.guardrails import DailyLossBudgetSnapshot
from kindshot.headline_parser import (
    is_broker_note_headline,
    is_commentary_headline,
    is_contract_commentary_headline,
    is_direct_disclosure_headline,
)
from kindshot.hold_profile import resolve_hold_profile
from kindshot.llm_client import LlmClient, LlmCallError, LlmTimeoutError
from kindshot.models import (
    Action,
    Bucket,
    ContextCard,
    DecisionRecord,
    MarketContext,
    NewsSignalContext,
    SizeHint,
)
from kindshot.news_semantics import build_news_signal, extract_contract_amount_eok
from kindshot.news_category import classify_news_type

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_STRATEGY_PROMPT: Optional[str] = None


def _load_strategy_prompt() -> str:
    global _STRATEGY_PROMPT
    if _STRATEGY_PROMPT is None:
        _STRATEGY_PROMPT = (_PROMPTS_DIR / "decision_strategy.txt").read_text(encoding="utf-8")
    return _STRATEGY_PROMPT


def _resolve_strategy_prompt(strategy_override: str | None = None) -> str:
    if strategy_override is not None:
        return strategy_override
    return _load_strategy_prompt()


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
_INCREMENTAL_ORDER_MARKERS = (
    "1척 추가", "추가 수주", "추가 공급", "추가 계약",
    "추가 납품", "추가 발주", "연장 계약", "계약 연장",
    "옵션 행사", "후속 수주", "후속 계약",
)


def _build_prompt(
    bucket: Bucket,
    headline: str,
    ticker: str,
    corp_name: str,
    detected_at: str,
    ctx: ContextCard,
    market_ctx: Optional[MarketContext] = None,
    *,
    raw_headline: str | None = None,
    dorg: str = "",
    keyword_hits: Optional[list[str]] = None,
    hold_minutes: int | None = None,
    risk_budget: DailyLossBudgetSnapshot | None = None,
    consecutive_stop_losses: int = 0,
    news_signal: NewsSignalContext | None = None,
    strategy_override: str | None = None,
) -> str:
    # Truncate headline to prevent prompt injection via excessively long input
    headline = headline[:_MAX_HEADLINE_LEN]
    raw_headline = (raw_headline or headline)[:_MAX_HEADLINE_LEN]
    rsi_str = f" rsi_14={ctx.rsi_14}" if ctx.rsi_14 is not None else ""
    macd_str = f" macd_hist={ctx.macd_hist}" if ctx.macd_hist is not None else ""
    bb_str = f" bb_pos={ctx.bb_position}" if ctx.bb_position is not None else ""
    atr_str = f" atr_14={ctx.atr_14}%" if ctx.atr_14 is not None else ""
    ctx_price = (
        f"ret_today={ctx.ret_today} ret_1d={ctx.ret_1d} ret_3d={ctx.ret_3d} "
        f"pos_20d={ctx.pos_20d} gap={ctx.gap}{rsi_str}{macd_str}{bb_str}{atr_str}"
    )
    adv_display = f"{ctx.adv_value_20d/1e8:.0f}억" if ctx.adv_value_20d else "N/A"
    vol_rate_str = f" prior_vol_rate={ctx.prior_volume_rate:.0f}%" if ctx.prior_volume_rate is not None else ""
    ctx_micro = (
        f"adv_20d={adv_display} spread_bps={ctx.spread_bps} vol_pct_20d={ctx.vol_pct_20d} "
        f"intraday_value_vs_adv20d={ctx.intraday_value_vs_adv20d} "
        f"top_ask_notional={ctx.top_ask_notional} "
        f"temp_stop={ctx.quote_temp_stop} liquidation_trade={ctx.quote_liquidation_trade}"
        f"{vol_rate_str}"
    )

    # 시장 환경 요약
    market_line = ""
    if market_ctx:
        kospi = f"{market_ctx.kospi_change_pct:+.1f}%" if market_ctx.kospi_change_pct is not None else "N/A"
        kosdaq = f"{market_ctx.kosdaq_change_pct:+.1f}%" if market_ctx.kosdaq_change_pct is not None else "N/A"
        breadth = f"{market_ctx.kospi_breadth_ratio:.2f}" if market_ctx.kospi_breadth_ratio is not None else "N/A"
        market_line = f"\nctx_market: KOSPI={kospi} KOSDAQ={kosdaq} breadth_ratio={breadth}"
        if market_ctx.macro_overall_regime:
            macro_conf = (
                f"{market_ctx.macro_overall_confidence:.0%}"
                if market_ctx.macro_overall_confidence is not None
                else "N/A"
            )
            market_line += (
                " "
                f"macro={market_ctx.macro_overall_regime}"
                f" macro_conf={macro_conf}"
                f" kr_macro={market_ctx.macro_kr_regime or 'N/A'}"
                f" crypto_macro={market_ctx.macro_crypto_regime or 'N/A'}"
            )
            if market_ctx.macro_position_multiplier is not None:
                market_line += f" macro_size_mult={market_ctx.macro_position_multiplier:.2f}x"

    derived_hold_minutes = hold_minutes
    hold_match = None
    if derived_hold_minutes is None:
        holder = type("_HoldConfig", (), {"max_hold_minutes": 15})()
        derived_hold_minutes, hold_match = resolve_hold_profile(headline, list(keyword_hits or []), holder)
    hold_label = "EOD" if derived_hold_minutes == 0 else f"{derived_hold_minutes}m"
    derived_signal = news_signal or build_news_signal(
        headline=raw_headline,
        ticker=ticker,
        corp_name=corp_name,
        detected_at=datetime.now(timezone.utc),
        dorg=dorg,
        keyword_hits=list(keyword_hits or []),
    )
    news_category = derived_signal.news_category or classify_news_type(headline, keyword_hits or [])
    commentary = (
        derived_signal.commentary
        if derived_signal.commentary is not None
        else is_commentary_headline(raw_headline, dorg=dorg)
    )
    broker_note = (
        derived_signal.broker_note
        if derived_signal.broker_note is not None
        else is_broker_note_headline(raw_headline, dorg=dorg)
    )
    contract_commentary = is_contract_commentary_headline(raw_headline, dorg=dorg)
    direct_disclosure = (
        derived_signal.direct_disclosure
        if derived_signal.direct_disclosure is not None
        else is_direct_disclosure_headline(raw_headline, dorg=dorg)
    )
    amount_eok = derived_signal.contract_amount_eok
    signal_parts = [
        f"news_category={news_category}",
        f"direct_disclosure={str(direct_disclosure).lower()}",
        f"commentary={str(commentary).lower()}",
        f"broker_note={str(broker_note).lower()}",
        f"contract_commentary={str(contract_commentary).lower()}",
        f"hold_profile={hold_label}",
    ]
    if hold_match:
        signal_parts.append(f"hold_keyword={hold_match}")
    if dorg:
        signal_parts.append(f"dorg={dorg}")
    signal_parts.append(
        f"contract_amount_eok={amount_eok:.0f}" if amount_eok is not None else "contract_amount_eok=N/A"
    )
    signal_parts.append(
        f"revenue_eok={derived_signal.revenue_eok:.0f}"
        if derived_signal.revenue_eok is not None
        else "revenue_eok=N/A"
    )
    signal_parts.append(
        f"operating_profit_eok={derived_signal.operating_profit_eok:.0f}"
        if derived_signal.operating_profit_eok is not None
        else "operating_profit_eok=N/A"
    )
    signal_parts.append(
        f"sales_ratio_pct={derived_signal.sales_ratio_pct:.1f}"
        if derived_signal.sales_ratio_pct is not None
        else "sales_ratio_pct=N/A"
    )
    signal_parts.append(
        f"impact_score={derived_signal.impact_score}"
        if derived_signal.impact_score is not None
        else "impact_score=N/A"
    )
    if derived_signal.cluster is not None:
        signal_parts.append(f"cluster_size={derived_signal.cluster.cluster_size}")
        signal_parts.append(f"cluster_corroborated={str(derived_signal.cluster.corroborated).lower()}")
    if ctx.alpha_signal is not None and ctx.alpha_signal.signal_type == "STRONG_BUY":
        signal_parts.append("alpha_signal=STRONG_BUY")
        if ctx.alpha_signal.score_current is not None:
            signal_parts.append(f"alpha_score={ctx.alpha_signal.score_current:.1f}")
        if ctx.alpha_signal.confidence is not None:
            signal_parts.append(f"alpha_confidence={ctx.alpha_signal.confidence}")
        if ctx.alpha_signal.size_hint:
            signal_parts.append(f"alpha_size={ctx.alpha_signal.size_hint}")
        if ctx.alpha_signal.age_hours is not None:
            signal_parts.append(f"alpha_age_h={ctx.alpha_signal.age_hours:.1f}")

    risk_line = ""
    if risk_budget is not None:
        risk_parts = [
            f"daily_pnl_won={int(round(risk_budget.remaining_budget_won + risk_budget.effective_floor_won))}",
            f"loss_floor_won={int(round(risk_budget.effective_floor_won))}",
            f"remaining_loss_budget_won={int(round(risk_budget.remaining_budget_won))}",
            f"consecutive_stop_losses={consecutive_stop_losses}",
        ]
        if risk_budget.effective_floor_pct is not None:
            risk_parts.append(f"loss_floor_pct={risk_budget.effective_floor_pct:.2f}")
        risk_line = f"\nctx_risk: {' '.join(risk_parts)}"

    strategy = _resolve_strategy_prompt(strategy_override)

    return f"""event: [{bucket.value}] {corp_name}, {headline}
corp: {corp_name}({ticker})
detected_at: {detected_at} KST

ctx_price: {ctx_price}
ctx_micro: {ctx_micro}{market_line}
ctx_signal: {' '.join(signal_parts)}{risk_line}

constraints: no_overnight=true respond_with_json_only=true prefer_ctx_signal_over_headline_tone=true macro_regime_guides_size=true

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


def _looks_like_contract_article(headline: str, *, raw_headline: str | None = None, dorg: str = "") -> bool:
    source_text = raw_headline or headline
    if is_contract_commentary_headline(source_text, dorg=dorg):
        return True
    if any(marker in headline for marker in _CONTRACT_ARTICLE_MARKERS):
        return True
    if "넘어" in source_text and any(mark in source_text for mark in ('"', "'", "“", "”", "‘", "’")):
        return True
    return False


def _looks_like_incremental_order(headline: str) -> bool:
    return any(marker in headline for marker in _INCREMENTAL_ORDER_MARKERS)


def _parse_contract_amount_eok(headline: str) -> float | None:
    """헤드라인에서 계약/수주 금액(억원)을 파싱. 없으면 None."""
    return extract_contract_amount_eok(headline)


def _contract_preflight_skip(
    headline: str,
    keyword_hits: list[str],
    ctx: ContextCard,
    *,
    raw_headline: str | None = None,
    dorg: str = "",
    contract_amount_eok: float | None = None,
) -> dict[str, object] | None:
    if not _is_contract_family(headline, keyword_hits):
        return None

    if _looks_like_contract_article(headline, raw_headline=raw_headline, dorg=dorg):
        return {"confidence": 35, "reason": "rule_preflight:contract_article"}

    if _looks_like_incremental_order(headline):
        return {"confidence": 45, "reason": "rule_preflight:contract_incremental"}

    # 소규모 계약 (<200억): SKIP — 주가 영향 미미 (v65: 100→200억 상향, 공급계약 -2.30%/건 손실 데이터)
    amt_eok = contract_amount_eok if contract_amount_eok is not None else _parse_contract_amount_eok(headline)
    if amt_eok is not None and amt_eok < 200:
        return {"confidence": 45, "reason": f"rule_preflight:small_contract {amt_eok:.0f}억"}

    if ctx.ret_today is not None and ctx.ret_today >= 3.0:
        return {"confidence": 40, "reason": f"rule_preflight:contract_chase ret={ctx.ret_today:.1f}%"}

    if ctx.ret_3d is not None and ctx.ret_3d <= -5.0:
        # 대형 계약(1000억+ 또는 매출액대비 10%+)은 하락장에서도 LLM 판단 허용
        is_large, _ = _has_large_contract_signal(headline, keyword_hits)
        if not is_large:
            return {"confidence": 50, "reason": f"rule_preflight:contract_downtrend ret_3d={ctx.ret_3d:.1f}%"}

    if ctx.adv_value_20d is not None and ctx.adv_value_20d > 200_000_000_000:
        # 대형주지만 대형 계약(1000억+)이면 LLM 판단 허용 (v63: cap 76 적용됨)
        is_large, _ = _has_large_contract_signal(headline, keyword_hits)
        if not is_large:
            adv_eok = ctx.adv_value_20d / 1e8
            return {"confidence": 45, "reason": f"rule_preflight:contract_large_cap adv={adv_eok:.0f}억"}

    # v65: 중형 계약(200-500억) + 중대형주(ADV 1000억+) → sell-the-news 가능성
    if amt_eok is not None and amt_eok < 500 and ctx.adv_value_20d is not None and ctx.adv_value_20d > 100_000_000_000:
        adv_eok = ctx.adv_value_20d / 1e8
        return {"confidence": 50, "reason": f"rule_preflight:mid_contract_large_cap {amt_eok:.0f}억/adv={adv_eok:.0f}억"}

    return None


@dataclass
class _CacheEntry:
    result: DecisionRecord
    expires_at: float


_HIGH_CONVICTION_KEYWORDS: list[tuple[str, int]] = [
    # 주주환원: 가장 신뢰도 높음 — 감점(-5~-8) 후에도 BUY 유지되도록 86+
    ("자사주 소각", 86), ("자사주소각", 86), ("자기주식 소각", 86), ("자기주식소각", 86),
    ("주식 소각 결정", 86), ("주식소각결정", 86), ("주식 소각", 86),
    ("주식소각 결정", 86), ("주식소각", 86), ("소각 결정", 86),
    ("소각 결의", 86), ("소각결의", 86), ("전량 소각", 88), ("전량소각", 88),
    ("자사주 매입", 84), ("자사주매입", 84), ("자사주 추가 매입", 84), ("자사주추가매입", 84),
    ("자기주식 취득", 84), ("자기주식취득", 84), ("자사주 추가 취득", 84),
    ("자기주식취득 신탁계약", 84),
    ("공개매수", 86), ("대항 공개매수", 88),
    ("경영권 분쟁", 86), ("경영권분쟁", 86), ("위임장 대결", 86),
    # 실적 서프라이즈 — 감점 후 78+ 유지 위해 86+
    ("어닝 서프라이즈", 86), ("어닝서프라이즈", 86),
    ("사상최대 실적", 88), ("사상 최대 실적", 88), ("사상최대 영업이익", 88), ("사상 최대 영업이익", 88),
    ("사상최대 매출", 88), ("사상 최대 매출", 88),
    ("역대 최대 실적", 88), ("역대 최대 영업이익", 88), ("역대 최대 매출", 88),
    ("흑자전환", 88), ("흑자 전환", 88),
    ("깜짝 실적", 86),
    # 계약/수주 — 확정 체결 패턴 (감점 -5~-8 감안)
    ("대형 계약", 85), ("대형계약", 85), ("대규모 수주", 85), ("대규모수주", 85),
    ("설계 계약", 84), ("설계계약", 84),
    ("단일판매ㆍ공급계약", 83), ("단일판매·공급계약", 83), ("단일판매", 83),
    ("규모의 공급계약", 83), ("규모 공급계약", 83),
    ("해외 수주", 84), ("해외수주", 84),
    ("방산 수주", 84), ("방산수주", 84),
    ("독점 공급", 84), ("독점공급", 84),
    ("장기 공급", 83), ("장기공급", 83),
    # 바이오 — FDA/임상은 최고 등급
    ("임상 3상 성공", 92), ("임상3상 성공", 92),
    ("FDA 승인", 92), ("FDA승인", 92), ("FDA 허가", 92), ("FDA허가", 92),
    ("품목허가 승인", 86), ("품목허가 획득", 86), ("허가 획득", 86), ("허가 취득", 86), ("식약처 허가", 86),
    ("CDMO 계약", 85), ("CDMO계약", 85),
    ("기술수출 계약", 88), ("기술수출 계약 체결", 88), ("기술이전 계약", 88),
    ("라이선스 아웃", 88),
    ("임상 2상 완료", 84), ("임상2상 완료", 84), ("임상 2상 성공", 85), ("임상2상 성공", 85),
    # 특허 — 감점 감안 +4
    ("특허 등록", 83), ("특허등록", 83), ("특허 취득", 83), ("특허취득", 83),
    ("특허 확보", 82), ("특허확보", 82),
    # M&A — 감점 감안 +5
    ("지분 취득", 86), ("지분취득", 86),
    ("인수 완료", 87), ("인수완료", 87), ("인수 결정", 86),
    # 첫 수주/매출 — 모멘텀 전환 신호
    ("최초 수주", 84), ("첫 수주", 84), ("첫 매출", 83),
    ("첫 양산", 83), ("양산 개시", 83),
    # 정부/조달 계약 — 안정적 매출
    ("정부 조달", 83), ("조달청 계약", 83), ("국책과제 선정", 82),
    # 역대/사상 최대 수주
    ("역대 최대 수주", 88), ("사상 최대 수주", 88),
    ("수주 잔고 최대", 85), ("수주잔고 최대", 85),
]


def has_high_conviction_keyword(headline: str, keyword_hits: list[str], *, min_conf: int = 86) -> bool:
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
        if pct >= 15.0:
            return True, 93
        if pct >= 10.0:
            return True, 90
        if pct >= 5.0:
            return True, 86
        # 5% 미만은 무시

    # 금액 파싱: 조 단위 + 억 단위
    # "2.89조원" → 28900억, "1.96조원" → 19600억
    cho_match = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*조", headline)
    if cho_match:
        amt_eok = float(cho_match.group(1).replace(",", "")) * 10000
        if amt_eok >= 10000:  # 1조+
            return True, 90
        if amt_eok >= 5000:  # 5000억+
            return True, 87

    amt_match = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*억", headline)
    if amt_match:
        amt = float(amt_match.group(1).replace(",", ""))
        if amt >= 1000:
            return True, 85
        if amt >= 500:
            return True, 83

    # 달러/USD 금액 파싱: 해외 수주
    # "1.5억달러", "150백만달러", "2억불", "100M USD"
    usd_eok_match = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*억\s*(?:달러|불|USD)", headline)
    if usd_eok_match:
        usd_eok = float(usd_eok_match.group(1).replace(",", ""))
        amt_eok = usd_eok * 1400  # 1억달러 ≈ 1400억원 (환율 1400원 기준)
        if amt_eok >= 1000:
            return True, 87
        if amt_eok >= 500:
            return True, 84

    usd_m_match = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(?:백만|million|M)\s*(?:달러|불|USD)", headline, re.IGNORECASE)
    if usd_m_match:
        usd_m = float(usd_m_match.group(1).replace(",", ""))
        amt_eok = usd_m * 14  # 1M USD ≈ 14억원 (환율 1400원)
        if amt_eok >= 1000:
            return True, 87
        if amt_eok >= 500:
            return True, 84

    # "단일판매ㆍ공급계약체결" 정규 공시는 금액 없어도 신뢰도 있음 (KIND 공시)
    if "단일판매" in headline and "공급계약" in headline:
        return True, 83

    return False, 0


_ARTICLE_MARKERS = (
    "전망", "보인다", "기대", "파죽지세", "분석", "평가",
    "목표", "수혜", "기대감", "가속화", "본격화",
    "CEO ", "대표 ", "회장 ", "의장 ",
    "Preview", "preview", "리뷰", "프리뷰",
    "추진", "검토", "계획", "협의 중", "논의", "예정",
    "우려", "주목", "관심", "포인트", "키워드",
    # v65 추가: 리포트/분석 기사 패턴 (-2.26%/건 손실)
    "주가 전망", "실적 전망", "호실적", "수혜주",
    "테마", "급등", "폭등", "급락", "상한가",
    "모멘텀", "랠리", "반등 기대", "저점 매수",
)


def has_article_pattern(headline: str, *, raw_headline: str | None = None, dorg: str = "") -> bool:
    """헤드라인에 기사/미확정 패턴이 있으면 True. LLM BUY에도 post-check 적용."""
    source_text = raw_headline or headline
    if is_commentary_headline(source_text, dorg=dorg):
        return True
    return any(marker in headline for marker in _ARTICLE_MARKERS)


def _rule_based_decide(
    bucket: Bucket,
    headline: str,
    keyword_hits: list[str],
    ctx: ContextCard,
    *,
    raw_headline: str | None = None,
    dorg: str = "",
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
    if has_article_pattern(headline, raw_headline=raw_headline, dorg=dorg):
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

    # POS_WEAK은 고확신(84+)만 BUY, POS_STRONG은 80+
    # 감점 파이프라인(-5~-8) 후에도 78+ 유지하려면 fallback 자체가 높아야 함
    min_conf = 84 if bucket == Bucket.POS_WEAK else 80

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
        self._inflight: dict[str, asyncio.Task[DecisionRecord]] = {}
        self._stats: Counter[str] = Counter()
        self._cache_dir = config.llm_cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_key(
        self,
        ticker: str,
        headline: str,
        bucket: Bucket,
        ctx: ContextCard,
        *,
        corp_name: str = "",
        detected_at_str: str = "",
        dorg: str = "",
        market_ctx: Optional[MarketContext] = None,
        risk_budget: DailyLossBudgetSnapshot | None = None,
        consecutive_stop_losses: int = 0,
        strategy_override: str | None = None,
    ) -> str:
        detected_at_bucket = detected_at_str[:5] if detected_at_str else ""
        ctx_str = (
            f"{ctx.adv_value_20d}|{ctx.spread_bps}|{ctx.ret_today}|"
            f"{ctx.intraday_value_vs_adv20d}|{ctx.top_ask_notional}|"
            f"{ctx.quote_temp_stop}|{ctx.quote_liquidation_trade}"
        )
        market_str = ""
        if market_ctx is not None:
            market_str = (
                f"|{market_ctx.kospi_change_pct}|{market_ctx.kosdaq_change_pct}"
                f"|{market_ctx.kospi_breadth_ratio}|{market_ctx.kosdaq_breadth_ratio}"
                f"|{market_ctx.macro_overall_regime}|{market_ctx.macro_overall_confidence}"
                f"|{market_ctx.macro_kr_regime}|{market_ctx.macro_crypto_regime}"
                f"|{market_ctx.macro_position_multiplier}"
            )
        risk_str = ""
        if risk_budget is not None:
            risk_str = (
                f"|{risk_budget.effective_floor_won}|{risk_budget.remaining_budget_won}"
                f"|{risk_budget.effective_floor_pct}|{consecutive_stop_losses}"
            )
        h = hashlib.sha256(
            f"{ticker}|{corp_name}|{headline}|{bucket.value}|{detected_at_bucket}|{dorg}|{ctx_str}{market_str}{risk_str}|{strategy_override or ''}".encode()
        ).hexdigest()[:32]
        return f"{ticker}:{h}:{bucket.value}"

    def _sweep_cache(self) -> None:
        now = time.monotonic()
        force = len(self._cache) > self._config.llm_cache_max_entries
        if not force and now - self._last_sweep < self._config.llm_cache_sweep_s:
            return
        expired = [k for k, v in self._cache.items() if v.expires_at < now]
        for k in expired:
            del self._cache[k]
        if len(self._cache) > self._config.llm_cache_max_entries:
            sorted_keys = sorted(self._cache, key=lambda k: self._cache[k].expires_at)
            overflow = len(self._cache) - self._config.llm_cache_max_entries
            for k in sorted_keys[:overflow]:
                del self._cache[k]
        self._sweep_disk_cache(force=force)
        self._last_sweep = now

    def _disk_cache_path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode()).hexdigest()
        return self._cache_dir / f"{digest}.json"

    def _load_disk_cache(self, key: str, run_id: str) -> DecisionRecord | None:
        path = self._disk_cache_path(key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            expires_at = float(payload.get("expires_at_unix", 0.0))
            if expires_at <= time.time():
                path.unlink(missing_ok=True)
                return None
            record_payload = payload.get("record")
            if not isinstance(record_payload, dict):
                return None
            source = DecisionRecord.model_validate(record_payload)
            self._stats["disk_hits"] += 1
            return self._as_cache_result(source, run_id, cache_layer="disk")
        except Exception:
            self._stats["disk_errors"] += 1
            logger.warning("Failed to read LLM disk cache for %s", key, exc_info=True)
            return None

    def _write_disk_cache(self, key: str, record: DecisionRecord) -> None:
        payload = {
            "expires_at_unix": time.time() + float(self._config.llm_cache_ttl_s),
            "record": record.model_dump(mode="json"),
        }
        path = self._disk_cache_path(key)
        tmp_path = path.with_suffix(".tmp")
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp_path.replace(path)
            self._stats["writes"] += 1
        except Exception:
            self._stats["disk_errors"] += 1
            logger.warning("Failed to write LLM disk cache for %s", key, exc_info=True)
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _sweep_disk_cache(self, *, force: bool = False) -> None:
        if not force and time.monotonic() - self._last_sweep < self._config.llm_cache_sweep_s:
            return
        files = sorted(self._cache_dir.glob("*.json"))
        now_unix = time.time()
        active_files: list[Path] = []
        for path in files:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if float(payload.get("expires_at_unix", 0.0)) <= now_unix:
                    path.unlink(missing_ok=True)
                    continue
            except Exception:
                path.unlink(missing_ok=True)
                continue
            active_files.append(path)
        if len(active_files) > self._config.llm_cache_max_entries:
            overflow = len(active_files) - self._config.llm_cache_max_entries
            active_files.sort(key=lambda item: item.stat().st_mtime)
            for path in active_files[:overflow]:
                path.unlink(missing_ok=True)

    def _as_cache_result(self, source: DecisionRecord, run_id: str, *, cache_layer: str) -> DecisionRecord:
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
            cache_layer=cache_layer,
        )

    def cache_stats(self) -> dict[str, int]:
        return {
            "memory_entries": len(self._cache),
            "memory_hits": self._stats["memory_hits"],
            "disk_hits": self._stats["disk_hits"],
            "inflight_hits": self._stats["inflight_hits"],
            "misses": self._stats["misses"],
            "writes": self._stats["writes"],
            "disk_errors": self._stats["disk_errors"],
        }

    def fallback_decide(
        self,
        ticker: str,
        headline: str,
        bucket: Bucket,
        ctx: ContextCard,
        keyword_hits: list[str],
        *,
        analysis_headline: Optional[str] = None,
        dorg: str = "",
        run_id: str = "",
        schema_version: str = "0.1.2",
    ) -> DecisionRecord:
        """Rule-based fallback when LLM is unavailable."""
        parsed = _rule_based_decide(
            bucket,
            analysis_headline or headline,
            keyword_hits,
            ctx,
            raw_headline=headline,
            dorg=dorg,
        )

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
        raw_headline: Optional[str] = None,
        dorg: str = "",
        contract_amount_eok: float | None = None,
        run_id: str = "",
        schema_version: str = "0.1.2",
    ) -> DecisionRecord | None:
        parsed = _contract_preflight_skip(
            headline,
            keyword_hits,
            ctx,
            raw_headline=raw_headline,
            dorg=dorg,
            contract_amount_eok=contract_amount_eok,
        )
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
        analysis_headline: Optional[str] = None,
        dorg: str = "",
        run_id: str = "",
        schema_version: str = "0.1.2",
        market_ctx: Optional[MarketContext] = None,
        risk_budget: Optional[DailyLossBudgetSnapshot] = None,
        consecutive_stop_losses: int = 0,
        news_signal: Optional[NewsSignalContext] = None,
        strategy_override: str | None = None,
    ) -> DecisionRecord:
        """Call LLM for BUY/SKIP decision.

        Raises:
            LlmTimeoutError: upstream timeout.
            LlmCallError: upstream call failure.
            LlmParseError: response parse/shape failure.
        """
        analysis_text = analysis_headline or headline
        hold_minutes, _hold_match = resolve_hold_profile(analysis_text, list(keyword_hits or []), self._config)

        preflight = self._preflight_decide(
            analysis_text,
            ctx,
            list(keyword_hits or []),
            raw_headline=headline,
            dorg=dorg,
            contract_amount_eok=news_signal.contract_amount_eok if news_signal is not None else None,
            run_id=run_id,
            schema_version=schema_version,
        )
        if preflight is not None:
            return preflight

        self._sweep_cache()
        key = self._cache_key(
            ticker,
            analysis_text,
            bucket,
            ctx,
            corp_name=corp_name,
            detected_at_str=detected_at_str,
            dorg=dorg,
            market_ctx=market_ctx,
            risk_budget=risk_budget,
            consecutive_stop_losses=consecutive_stop_losses,
            strategy_override=strategy_override,
        )

        if key in self._cache and self._cache[key].expires_at > time.monotonic():
            self._stats["memory_hits"] += 1
            return self._as_cache_result(self._cache[key].result, run_id, cache_layer="memory")

        disk_hit = self._load_disk_cache(key, run_id)
        if disk_hit is not None:
            return disk_hit

        inflight = self._inflight.get(key)
        if inflight is not None:
            try:
                shared = await inflight
            except (LlmTimeoutError, LlmCallError, LlmParseError) as e:
                raise type(e)(str(e)) from e
            self._stats["inflight_hits"] += 1
            return self._as_cache_result(shared, run_id, cache_layer="inflight")

        self._stats["misses"] += 1

        async def _invoke_uncached() -> DecisionRecord:
            prompt = _build_prompt(
                bucket,
                analysis_text,
                ticker,
                corp_name,
                detected_at_str,
                ctx,
                market_ctx,
                raw_headline=headline,
                dorg=dorg,
                keyword_hits=keyword_hits,
                hold_minutes=hold_minutes,
                risk_budget=risk_budget,
                consecutive_stop_losses=consecutive_stop_losses,
                news_signal=news_signal,
                strategy_override=strategy_override,
            )

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
                cache_layer="miss",
            )

            self._cache[key] = _CacheEntry(
                result=record,
                expires_at=time.monotonic() + self._config.llm_cache_ttl_s,
            )
            self._write_disk_cache(key, record)
            return record

        task = asyncio.create_task(_invoke_uncached())
        self._inflight[key] = task
        try:
            return await task
        finally:
            if self._inflight.get(key) is task:
                del self._inflight[key]
