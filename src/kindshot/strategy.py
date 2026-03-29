"""멀티 전략 프레임워크: Strategy 프로토콜 + TradeSignal + StrategyRegistry.

kindshot을 뉴스 전용 트레이더에서 한국 주식 자동매매 플랫폼으로 확장하기 위한
전략 추상화 레이어. 각 전략은 독립적으로 시그널을 생성하고, 통합 파이프라인에서 실행된다.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import AsyncIterator, Optional, Protocol, runtime_checkable

from kindshot.models import Action, SizeHint

logger = logging.getLogger(__name__)


# ── Signal Models ──────────────────────────────────────


class SignalSource(str, Enum):
    """시그널 발생 소스."""
    NEWS = "NEWS"              # 뉴스/공시 기반
    TECHNICAL = "TECHNICAL"    # 기술적 분석 (모멘텀, 브레이크아웃 등)
    Y2I = "Y2I"                # 유튜브 인사이트
    ALPHA = "ALPHA"            # Alpha-scanner conviction
    MACRO = "MACRO"            # 매크로 레짐 기반
    COMPOSITE = "COMPOSITE"    # 복합 전략


@dataclass
class TradeSignal:
    """전략이 생성하는 통합 매매 시그널.

    모든 전략은 이 모델로 시그널을 출력한다.
    파이프라인은 TradeSignal을 받아 guardrails → 주문 → 추적을 실행.
    """
    strategy_name: str
    source: SignalSource
    ticker: str
    corp_name: str
    action: Action
    confidence: int  # 0-100
    size_hint: SizeHint = SizeHint.S
    reason: str = ""
    headline: str = ""  # 뉴스 전략의 경우 공시 제목

    # 컨텍스트 (전략별 추가 정보)
    event_id: str = ""
    detected_at: Optional[datetime] = None
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.confidence < 0 or self.confidence > 100:
            raise ValueError(f"confidence must be 0-100, got {self.confidence}")


# ── Strategy Protocol ──────────────────────────────────


@runtime_checkable
class Strategy(Protocol):
    """전략 프로토콜: 모든 전략이 구현해야 하는 인터페이스.

    두 가지 패턴을 지원:
    - Event-driven (뉴스): stream_signals()로 시그널을 비동기 스트림
    - Polling (기술적 분석): stream_signals() 내부에서 주기적으로 폴링

    Examples:
        class MyStrategy:
            @property
            def name(self) -> str: return "my_strategy"

            @property
            def source(self) -> SignalSource: return SignalSource.TECHNICAL

            @property
            def enabled(self) -> bool: return True

            async def stream_signals(self) -> AsyncIterator[TradeSignal]:
                while True:
                    signal = await self._evaluate()
                    if signal:
                        yield signal
                    await asyncio.sleep(60)

            async def start(self) -> None: ...
            async def stop(self) -> None: ...
    """

    @property
    def name(self) -> str:
        """전략 이름 (고유 식별자)."""
        ...

    @property
    def source(self) -> SignalSource:
        """시그널 소스 타입."""
        ...

    @property
    def enabled(self) -> bool:
        """전략 활성화 여부."""
        ...

    async def stream_signals(self) -> AsyncIterator[TradeSignal]:
        """시그널 비동기 스트림. 전략이 종료될 때까지 yield."""
        ...

    async def start(self) -> None:
        """전략 초기화 (리소스 할당 등)."""
        ...

    async def stop(self) -> None:
        """전략 종료 (리소스 해제)."""
        ...


# ── Strategy Registry ──────────────────────────────────


class StrategyRegistry:
    """전략 레지스트리: 등록된 전략들을 관리하고 시그널을 통합 스트림으로 병합.

    Usage:
        registry = StrategyRegistry()
        registry.register(NewsStrategy(config, ...))
        registry.register(TechnicalStrategy(config, ...))

        async for signal in registry.stream_all():
            await execute_signal(signal)
    """

    def __init__(self) -> None:
        self._strategies: dict[str, Strategy] = {}

    def register(self, strategy: Strategy) -> None:
        """전략 등록. 이름 중복 시 에러."""
        if strategy.name in self._strategies:
            raise ValueError(f"Strategy '{strategy.name}' already registered")
        self._strategies[strategy.name] = strategy
        logger.info("Strategy registered: %s (source=%s, enabled=%s)",
                     strategy.name, strategy.source.value, strategy.enabled)

    def get(self, name: str) -> Optional[Strategy]:
        return self._strategies.get(name)

    @property
    def strategies(self) -> list[Strategy]:
        return list(self._strategies.values())

    @property
    def active_strategies(self) -> list[Strategy]:
        return [s for s in self._strategies.values() if s.enabled]

    async def start_all(self) -> None:
        """활성 전략들을 모두 시작."""
        for s in self.active_strategies:
            try:
                await s.start()
                logger.info("Strategy started: %s", s.name)
            except Exception:
                logger.exception("Failed to start strategy: %s", s.name)

    async def stop_all(self) -> None:
        """모든 전략을 종료."""
        for s in self._strategies.values():
            try:
                await s.stop()
                logger.info("Strategy stopped: %s", s.name)
            except Exception:
                logger.warning("Failed to stop strategy: %s", s.name, exc_info=True)

    async def stream_all(self) -> AsyncIterator[TradeSignal]:
        """모든 활성 전략의 시그널을 하나의 스트림으로 병합.

        각 전략을 별도 태스크로 실행하고, asyncio.Queue로 시그널을 수집.
        한 전략이 실패해도 나머지는 계속 동작.
        """
        queue: asyncio.Queue[Optional[TradeSignal]] = asyncio.Queue()
        active = self.active_strategies
        if not active:
            logger.warning("No active strategies to stream")
            return

        done_count = 0
        total = len(active)

        async def _run_strategy(strategy: Strategy) -> None:
            nonlocal done_count
            try:
                async for signal in strategy.stream_signals():
                    await queue.put(signal)
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Strategy '%s' failed", strategy.name)
            finally:
                done_count += 1
                if done_count >= total:
                    await queue.put(None)  # sentinel

        tasks = [
            asyncio.create_task(_run_strategy(s), name=f"strategy-{s.name}")
            for s in active
        ]

        try:
            while True:
                signal = await queue.get()
                if signal is None:
                    break
                yield signal
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
