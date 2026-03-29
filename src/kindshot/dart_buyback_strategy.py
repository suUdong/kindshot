"""DART 자사주 매입 공시 기반 매매 전략.

DartFeed에서 자사주 취득 결정 공시를 감지하면
DS005 API로 구조화 데이터를 조회하고, confidence 스코어링 후 TradeSignal을 생성한다.
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
from kindshot.dart_enricher import BuybackInfo, CorpCodeMapper, DartEnricher
from kindshot.feed import DartFeed, RawDisclosure
from kindshot.models import Action, SizeHint
from kindshot.strategy import SignalSource, TradeSignal
from kindshot.tz import KST as _KST

logger = logging.getLogger(__name__)

# 자사주 매입 공시 감지 패턴
_BUYBACK_PATTERNS = [
    "자기주식취득결정",
    "자사주취득",
    "주요사항보고서(자기주식취득결정)",
    "자기주식 취득 결정",
]

_BUYBACK_RE = re.compile("|".join(re.escape(p) for p in _BUYBACK_PATTERNS))


def is_buyback_disclosure(report_nm: str) -> bool:
    """report_nm이 자사주 매입 공시인지 판별."""
    return bool(_BUYBACK_RE.search(report_nm))


def score_buyback(info: BuybackInfo, config: Config) -> int:
    """자사주 매입 confidence 스코어링.

    기본 confidence + 직접매입 보너스 + 규모 보너스.
    """
    score = config.dart_buyback_base_confidence

    # 직접매입 vs 신탁
    if info.is_direct:
        score += config.dart_buyback_direct_bonus
    else:
        score += config.dart_buyback_trust_bonus

    # 규모 보너스 (planned_amount 기준, 시총 대비는 추후 확장)
    # 현재는 절대 금액 기준으로 간이 판단
    amount = info.planned_amount
    if amount >= 50_000_000_000:  # 500억+
        score += 10
    elif amount >= 10_000_000_000:  # 100억+
        score += 5

    return min(score, 100)


def size_hint_from_confidence(confidence: int) -> SizeHint:
    if confidence >= 85:
        return SizeHint.L
    if confidence >= 75:
        return SizeHint.M
    return SizeHint.S


class DartBuybackStrategy:
    """DART 자사주 매입 공시 기반 매매 전략.

    Strategy 프로토콜 구현. DartFeed를 직접 폴링하지 않고,
    buyback_queue를 통해 DartFeed에서 분리된 자사주 공시를 수신한다.
    """

    def __init__(
        self,
        config: Config,
        session: aiohttp.ClientSession,
        buyback_queue: asyncio.Queue[RawDisclosure],
        *,
        stop_event: Optional[asyncio.Event] = None,
    ) -> None:
        self._config = config
        self._session = session
        self._queue = buyback_queue
        self._stop_event = stop_event or asyncio.Event()
        self._mapper = CorpCodeMapper(config, session)
        self._enricher = DartEnricher(config, session, self._mapper)
        self._consumed: set[str] = set()  # 처리된 rcept_no
        self._enabled = config.dart_buyback_enabled

    @property
    def name(self) -> str:
        return "dart_buyback"

    @property
    def source(self) -> SignalSource:
        return SignalSource.NEWS

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def stream_signals(self) -> AsyncIterator[TradeSignal]:
        """buyback_queue에서 자사주 공시를 수신하고 시그널 생성."""
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
        """단일 공시를 처리하여 TradeSignal 생성."""
        ticker = disc.ticker
        rcept_no = disc.rss_guid or ""

        info = await self._enricher.fetch_buyback(ticker, rcept_no)
        if info is None:
            # DS005 조회 실패 시에도 기본 시그널 생성 (report_nm 기반)
            logger.info("DartBuyback: DS005 unavailable for %s, using basic signal", ticker)
            confidence = self._config.dart_buyback_base_confidence
            reason = "자사주 취득 결정 공시 (상세 미조회)"
            return TradeSignal(
                strategy_name="dart_buyback",
                source=SignalSource.NEWS,
                ticker=ticker,
                corp_name=disc.corp_name,
                action=Action.BUY,
                confidence=confidence,
                size_hint=size_hint_from_confidence(confidence),
                reason=reason,
                headline=disc.title,
                event_id=f"buyback_{rcept_no}",
                detected_at=disc.detected_at,
            )

        # 최소 금액 필터
        if info.planned_amount < self._config.dart_buyback_min_amount:
            logger.info(
                "DartBuyback: %s planned_amount=%d < min=%d, skipping",
                ticker, info.planned_amount, self._config.dart_buyback_min_amount,
            )
            return None

        confidence = score_buyback(info, self._config)
        method_label = "직접" if info.is_direct else "신탁"
        amount_eok = info.planned_amount / 1e8
        reason = f"자사주 {method_label}매입 {amount_eok:.0f}억"

        return TradeSignal(
            strategy_name="dart_buyback",
            source=SignalSource.NEWS,
            ticker=ticker,
            corp_name=disc.corp_name,
            action=Action.BUY,
            confidence=confidence,
            size_hint=size_hint_from_confidence(confidence),
            reason=reason,
            headline=disc.title,
            event_id=f"buyback_{rcept_no}",
            detected_at=disc.detected_at,
            metadata={"buyback": asdict(info)},
        )

    async def start(self) -> None:
        # corp_code 매핑 사전 로드
        try:
            await self._mapper.ensure_loaded()
            logger.info("DartBuybackStrategy started (corp_codes=%d)", len(self._mapper._map))
        except Exception:
            logger.warning("DartBuybackStrategy: corp_code preload failed", exc_info=True)

    async def stop(self) -> None:
        logger.info("DartBuybackStrategy stopping (consumed=%d)", len(self._consumed))
