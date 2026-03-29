"""NewsStrategy: 기존 뉴스/공시 파이프라인을 Strategy 프로토콜로 래핑.

Phase 1 — 기존 pipeline_loop를 그대로 위임. 동작 변경 없이 Strategy 인터페이스 제공.
향후 Phase 2+에서 다른 전략들이 추가되면, 시그널 생성부만 분리하여
공유 실행 파이프라인(guardrails → order → tracking)으로 통합.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Optional

from kindshot.config import Config
from kindshot.decision import DecisionEngine
from kindshot.event_registry import EventRegistry
from kindshot.guardrails import GuardrailState
from kindshot.kis_client import KisClient
from kindshot.logger import JsonlLogger
from kindshot.market import MarketMonitor
from kindshot.pipeline import RuntimeCounters, pipeline_loop
from kindshot.price import SnapshotScheduler
from kindshot.strategy import SignalSource, Strategy, TradeSignal

logger = logging.getLogger(__name__)


class NewsStrategy:
    """뉴스/공시 이벤트 기반 트레이딩 전략.

    기존 pipeline_loop를 Strategy 인터페이스로 래핑한다.
    Phase 1에서는 stream_signals()를 사용하지 않고, run_pipeline()으로
    기존 파이프라인을 직접 실행한다 (후방 호환).

    Phase 2+에서는 stream_signals()로 TradeSignal을 yield하고,
    공유 실행 파이프라인이 guardrails/order를 처리하게 된다.
    """

    def __init__(
        self,
        config: Config,
        feed,
        registry: EventRegistry,
        decision_engine: DecisionEngine,
        market: MarketMonitor,
        scheduler: SnapshotScheduler,
        log: JsonlLogger,
        run_id: str,
        kis: Optional[KisClient],
        counters: Optional[RuntimeCounters],
        mode: str,
        *,
        stop_event: Optional[asyncio.Event] = None,
        guardrail_state: Optional[GuardrailState] = None,
        feed_source: str = "KIND",
        unknown_review_queue: Optional[asyncio.Queue] = None,
        health_state: Optional[object] = None,
        order_executor: Optional[object] = None,
        recent_pattern_profile: Optional[object] = None,
    ) -> None:
        self._config = config
        self._feed = feed
        self._registry = registry
        self._decision_engine = decision_engine
        self._market = market
        self._scheduler = scheduler
        self._log = log
        self._run_id = run_id
        self._kis = kis
        self._counters = counters
        self._mode = mode
        self._stop_event = stop_event
        self._guardrail_state = guardrail_state
        self._feed_source = feed_source
        self._unknown_review_queue = unknown_review_queue
        self._health_state = health_state
        self._order_executor = order_executor
        self._recent_pattern_profile = recent_pattern_profile
        self._enabled = True

    # ── Strategy Protocol ──────────────────────────────

    @property
    def name(self) -> str:
        return "news"

    @property
    def source(self) -> SignalSource:
        return SignalSource.NEWS

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def stream_signals(self) -> AsyncIterator[TradeSignal]:
        """Phase 2+ 전용. 현재는 미구현 — run_pipeline() 사용."""
        # Phase 2에서 구현: bucket+LLM 결과를 TradeSignal로 변환하여 yield
        return
        yield  # pragma: no cover — make this a generator

    async def start(self) -> None:
        logger.info("NewsStrategy starting (feed_source=%s)", self._feed_source)

    async def stop(self) -> None:
        logger.info("NewsStrategy stopping")

    # ── Phase 1: 기존 파이프라인 직접 실행 ─────────────

    async def run_pipeline(self) -> None:
        """기존 pipeline_loop를 그대로 실행.

        Phase 1에서는 이 메서드가 main.py의 pipeline 태스크를 대체한다.
        stream_signals() 대신 이 메서드로 기존 동작을 유지.
        """
        await pipeline_loop(
            self._feed,
            self._registry,
            self._decision_engine,
            self._market,
            self._scheduler,
            self._log,
            self._config,
            self._run_id,
            self._kis,
            self._counters,
            self._mode,
            stop_event=self._stop_event,
            guardrail_state=self._guardrail_state,
            feed_source=self._feed_source,
            unknown_review_queue=self._unknown_review_queue,
            health_state=self._health_state,
            order_executor=self._order_executor,
            recent_pattern_profile=self._recent_pattern_profile,
        )
