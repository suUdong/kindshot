"""DART 잠정실적 기반 PEAD (Post-Earnings Announcement Drift) 전략.

DartFeed에서 잠정실적/30%변경 공시를 감지하면
DS003 API로 전기 재무 데이터를 조회하고, YoY 서프라이즈 스코어링 후 TradeSignal을 생성한다.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import asdict
from datetime import datetime
from typing import AsyncIterator, Optional

import aiohttp

from kindshot.config import Config
from kindshot.dart_enricher import CorpCodeMapper, DartEnricher, EarningsInfo
from kindshot.feed import DartFeed, RawDisclosure
from kindshot.models import Action, SizeHint
from kindshot.strategy import SignalSource, TradeSignal
from kindshot.tz import KST as _KST

logger = logging.getLogger(__name__)


def _infer_prior_period(report_nm: str) -> tuple[str, str]:
    """공시 제목에서 전기 기준 사업연도와 보고서 코드를 추론.

    잠정실적 공시 제목에서 분기 정보를 파싱하여
    전기 동일 분기의 (bsns_year, reprt_code)를 반환한다.

    Returns:
        (bsns_year, reprt_code) — 전기 동일 분기
        reprt_code: 11013=1분기, 11012=반기, 11014=3분기, 11011=사업보고서
    """
    now = datetime.now(_KST)
    current_year = now.year

    # "2025년" 등 연도 추출
    year_match = re.search(r"(\d{4})년", report_nm)
    report_year = int(year_match.group(1)) if year_match else current_year

    # 분기 추출
    if "1분기" in report_nm or "1/4분기" in report_nm:
        return str(report_year - 1), "11013"
    elif "반기" in report_nm or "2분기" in report_nm or "2/4분기" in report_nm:
        return str(report_year - 1), "11012"
    elif "3분기" in report_nm or "3/4분기" in report_nm:
        return str(report_year - 1), "11014"
    else:
        # 사업보고서(연간) 또는 분기 미명시 → 전기 사업보고서
        return str(report_year - 1), "11011"


def compute_yoy(current: int, prior: int) -> Optional[float]:
    """YoY 증감률 계산 (%).

    Returns:
        YoY 증감률 (%) or None (전기 0인 경우)
    """
    if prior == 0:
        return None
    return ((current - prior) / abs(prior)) * 100.0


def is_turnaround(current: int, prior: int) -> bool:
    """흑자전환 여부 (전기 적자 → 당기 흑자)."""
    return prior < 0 and current > 0


def score_earnings(
    current_op: int,
    prior_earnings: EarningsInfo,
    config: Config,
) -> tuple[int, str]:
    """실적 서프라이즈 confidence 스코어링.

    Returns:
        (confidence, reason)
    """
    prior_op = prior_earnings.operating_profit
    score = config.dart_earnings_base_confidence

    # 흑자전환 보너스
    if is_turnaround(current_op, prior_op):
        score += config.dart_earnings_turnaround_bonus
        reason = "흑자전환 (전기 영업적자→흑자)"
        return min(score, 100), reason

    # YoY 증감률
    yoy = compute_yoy(current_op, prior_op)
    if yoy is None:
        reason = "영업이익 YoY 산출 불가 (전기=0)"
        return min(score, 100), reason

    if yoy >= 100:
        score += config.dart_earnings_yoy_bonus_100
        reason = f"영업이익 YoY +{yoy:.0f}% (100%+ 서프라이즈)"
    elif yoy >= 50:
        score += config.dart_earnings_yoy_bonus_50
        reason = f"영업이익 YoY +{yoy:.0f}% (50%+ 서프라이즈)"
    elif yoy >= 30:
        score += config.dart_earnings_yoy_bonus_30
        reason = f"영업이익 YoY +{yoy:.0f}% (30%+ 서프라이즈)"
    elif yoy > 0:
        reason = f"영업이익 YoY +{yoy:.0f}% (소폭 증가)"
    else:
        reason = f"영업이익 YoY {yoy:.0f}% (감소)"

    return min(score, 100), reason


def size_hint_from_confidence(confidence: int) -> SizeHint:
    if confidence >= 85:
        return SizeHint.L
    if confidence >= 75:
        return SizeHint.M
    return SizeHint.S


# 공시 제목에서 당기 영업이익 파싱 패턴 (잠정실적 제목에 포함되는 경우)
_OP_PROFIT_RE = re.compile(r"영업이익\s*[:\s]*([+-]?\d[\d,]*)\s*(백만원|억원|원)?")


def _parse_op_from_title(title: str) -> Optional[int]:
    """공시 제목에서 영업이익 금액을 파싱 (있는 경우만)."""
    m = _OP_PROFIT_RE.search(title)
    if not m:
        return None
    raw = m.group(1).replace(",", "")
    try:
        value = int(raw)
    except ValueError:
        return None
    unit = m.group(2) or "원"
    if unit == "억원":
        value *= 100_000_000
    elif unit == "백만원":
        value *= 1_000_000
    return value


class DartEarningsStrategy:
    """DART 잠정실적/30%변경 기반 PEAD 전략.

    Strategy 프로토콜 구현. earnings_queue를 통해
    DartFeed에서 분리된 잠정실적 공시를 수신한다.
    """

    def __init__(
        self,
        config: Config,
        session: aiohttp.ClientSession,
        earnings_queue: asyncio.Queue[RawDisclosure],
        *,
        stop_event: Optional[asyncio.Event] = None,
    ) -> None:
        self._config = config
        self._session = session
        self._queue = earnings_queue
        self._stop_event = stop_event or asyncio.Event()
        self._mapper = CorpCodeMapper(config, session)
        self._enricher = DartEnricher(config, session, self._mapper)
        self._consumed: set[str] = set()  # 처리된 rcept_no
        self._enabled = config.dart_earnings_enabled

    @property
    def name(self) -> str:
        return "dart_earnings"

    @property
    def source(self) -> SignalSource:
        return SignalSource.NEWS

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def stream_signals(self) -> AsyncIterator[TradeSignal]:
        """earnings_queue에서 잠정실적 공시를 수신하고 시그널 생성."""
        while not self._stop_event.is_set():
            try:
                disc = await asyncio.wait_for(self._queue.get(), timeout=2.0)
            except asyncio.TimeoutError:
                continue

            if disc is None:
                return

            rcept_no = disc.rss_guid or ""
            if rcept_no in self._consumed:
                continue
            self._consumed.add(rcept_no)

            signal = await self._process_disclosure(disc)
            if signal:
                yield signal

    async def _process_disclosure(self, disc: RawDisclosure) -> Optional[TradeSignal]:
        """단일 잠정실적 공시를 처리하여 TradeSignal 생성."""
        ticker = disc.ticker
        rcept_no = disc.rss_guid or ""
        report_nm = disc.title  # "회사명(코드) report_nm" 형태

        # 전기 동일 분기 재무 데이터 조회
        prior_year, reprt_code = _infer_prior_period(report_nm)
        prior = await self._enricher.fetch_earnings(ticker, prior_year, reprt_code)

        if prior is None:
            # DS003 조회 실패 → 기본 시그널 (서프라이즈 스코어링 없이)
            logger.info("DartEarnings: DS003 unavailable for %s (year=%s), basic signal", ticker, prior_year)
            confidence = self._config.dart_earnings_base_confidence
            return TradeSignal(
                strategy_name="dart_earnings",
                source=SignalSource.NEWS,
                ticker=ticker,
                corp_name=disc.corp_name,
                action=Action.BUY,
                confidence=confidence,
                size_hint=size_hint_from_confidence(confidence),
                reason="잠정실적 공시 (전기 비교 미조회)",
                headline=disc.title,
                event_id=f"earnings_{rcept_no}",
                detected_at=disc.detected_at,
            )

        # 당기 영업이익: 제목에서 파싱 시도 (없으면 전기 대비 판단 불가 → 기본 시그널)
        current_op = _parse_op_from_title(report_nm)
        if current_op is None:
            logger.info("DartEarnings: cannot parse current OP from title for %s", ticker)
            confidence = self._config.dart_earnings_base_confidence
            return TradeSignal(
                strategy_name="dart_earnings",
                source=SignalSource.NEWS,
                ticker=ticker,
                corp_name=disc.corp_name,
                action=Action.BUY,
                confidence=confidence,
                size_hint=size_hint_from_confidence(confidence),
                reason="잠정실적 공시 (당기 영업이익 미파싱)",
                headline=disc.title,
                event_id=f"earnings_{rcept_no}",
                detected_at=disc.detected_at,
                metadata={"prior_earnings": asdict(prior)},
            )

        # 부정 서프라이즈 필터
        yoy = compute_yoy(current_op, prior.operating_profit)
        if config_skip_negative := self._config.dart_earnings_negative_skip:
            if yoy is not None and yoy < 0 and not is_turnaround(current_op, prior.operating_profit):
                logger.info(
                    "DartEarnings: negative surprise %s YoY=%.1f%%, SKIP",
                    ticker, yoy,
                )
                return None

        confidence, reason = score_earnings(current_op, prior, self._config)

        return TradeSignal(
            strategy_name="dart_earnings",
            source=SignalSource.NEWS,
            ticker=ticker,
            corp_name=disc.corp_name,
            action=Action.BUY,
            confidence=confidence,
            size_hint=size_hint_from_confidence(confidence),
            reason=reason,
            headline=disc.title,
            event_id=f"earnings_{rcept_no}",
            detected_at=disc.detected_at,
            metadata={
                "prior_earnings": asdict(prior),
                "current_op": current_op,
                "yoy_pct": yoy,
            },
        )

    async def start(self) -> None:
        # corp_code 매핑 사전 로드
        try:
            await self._mapper.ensure_loaded()
            logger.info("DartEarningsStrategy started (corp_codes=%d)", len(self._mapper._map))
        except Exception:
            logger.warning("DartEarningsStrategy: corp_code preload failed", exc_info=True)

    async def stop(self) -> None:
        logger.info("DartEarningsStrategy stopping (consumed=%d)", len(self._consumed))
