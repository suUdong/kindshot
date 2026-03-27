"""Market environment check: KOSPI/KOSDAQ halt + macro snapshot."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import aiohttp

from kindshot.config import Config
from kindshot.kis_client import IndexInfo, KisClient
from kindshot.models import MarketContext
from kindshot.runtime_artifacts import update_runtime_artifact_index

from kindshot.tz import KST as _KST

logger = logging.getLogger(__name__)


async def _fetch_vkospi() -> Optional[float]:
    """VKOSPI fetch stub. pykrx removed (KRX blocks AWS IPs).

    VKOSPI is optional context data; returning None is safe.
    """
    return None


async def _fetch_macro_regime(base_url: str, timeout_s: float) -> dict[str, Any] | None:
    """Fetch the latest macro regime from macro-intelligence over HTTP."""
    if not base_url:
        return None

    url = f"{base_url.rstrip('/')}/regime/current"
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            response.raise_for_status()
            payload = await response.json()

    if payload.get("status") != "ok":
        return None
    return payload


_MACRO_REGIME_MULTIPLIERS: dict[str, float] = {
    "expansionary": 1.2,
    "neutral": 1.0,
    "contractionary": 0.6,
}


class MarketMonitor:
    """Monitors KOSPI/KOSDAQ for halt condition and captures macro snapshot.

    When KIS is unavailable, market monitor cannot initialize and
    is_halted remains True (fail-close). Trading is blocked until
    KIS credentials are provided and the first update succeeds.
    """

    _MAX_INIT_FAILURES = 5  # After this many consecutive failures, force-initialize

    def __init__(self, config: Config, kis: Optional[KisClient] = None) -> None:
        self._config = config
        self._kis = kis
        self._halted = True  # fail-close: block trading until first successful update
        self._initialized = False
        self._init_failures = 0
        self._kospi_change: Optional[float] = None
        self._kosdaq_change: Optional[float] = None
        self._kospi_breadth_ratio: Optional[float] = None
        self._kosdaq_breadth_ratio: Optional[float] = None
        self._vkospi: Optional[float] = None
        self._macro_overall_regime: Optional[str] = None
        self._macro_overall_confidence: Optional[float] = None
        self._macro_kr_regime: Optional[str] = None
        self._macro_crypto_regime: Optional[str] = None
        self._macro_position_multiplier: Optional[float] = None
        self._last_ts: Optional[datetime] = None

    def _runtime_market_context_path(self, ts: datetime) -> Path:
        dt = ts.astimezone(_KST).strftime("%Y%m%d")
        return self._config.runtime_market_context_dir / f"{dt}.jsonl"

    @property
    def is_halted(self) -> bool:
        if not self._initialized:
            return True
        return self._halted

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def enabled(self) -> bool:
        return self._kis is not None

    @property
    def snapshot(self) -> MarketContext:
        """Return latest macro snapshot for logging."""
        return MarketContext(
            kospi_change_pct=self._kospi_change,
            kosdaq_change_pct=self._kosdaq_change,
            kospi_breadth_ratio=self._kospi_breadth_ratio,
            kosdaq_breadth_ratio=self._kosdaq_breadth_ratio,
            vkospi=self._vkospi,
            macro_overall_regime=self._macro_overall_regime,
            macro_overall_confidence=self._macro_overall_confidence,
            macro_kr_regime=self._macro_kr_regime,
            macro_crypto_regime=self._macro_crypto_regime,
            macro_position_multiplier=self._macro_position_multiplier,
        )

    async def append_runtime_snapshot(self) -> None:
        ts = self._last_ts or datetime.now(timezone.utc)
        record = {
            "type": "market_context",
            "ts": ts.isoformat(),
            **self.snapshot.model_dump(mode="json"),
        }
        path = self._runtime_market_context_path(ts)
        line = json.dumps(record, ensure_ascii=False)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

        _write()
        await update_runtime_artifact_index(
            self._config,
            date=ts.astimezone(_KST).strftime("%Y%m%d"),
            artifact="market_context",
            path=path,
            recorded_at=ts,
        )

    @staticmethod
    def _breadth_ratio(info: Optional[IndexInfo]) -> Optional[float]:
        if info is None or info.up_issue_count is None or info.down_issue_count is None:
            return None
        if info.down_issue_count <= 0:
            return float(info.up_issue_count) if info.up_issue_count > 0 else 1.0
        return round(info.up_issue_count / info.down_issue_count, 3)

    def _compute_position_multiplier(self) -> float:
        """Compute regime-based position multiplier from current macro state."""
        base = _MACRO_REGIME_MULTIPLIERS.get(self._macro_overall_regime or "", 1.0)

        # Korea-specific caution: if kr_regime is contractionary but overall isn't, reduce by 0.1
        if self._macro_kr_regime == "contractionary" and self._macro_overall_regime != "contractionary":
            base -= 0.1

        # Dampen toward 1.0 when confidence is low
        confidence = self._macro_overall_confidence
        if confidence is not None and confidence < 0.3:
            # Linear blend: at confidence=0 → multiplier=1.0, at confidence=0.3 → full effect
            blend = confidence / 0.3
            base = 1.0 + (base - 1.0) * blend

        # Clamp to [0.5, 1.5]
        return round(max(0.5, min(1.5, base)), 3)

    async def update(self) -> None:
        """Check KOSPI/KOSDAQ and update halt status + macro snapshot."""
        # KOSPI + KOSDAQ via KIS (parallel)
        if self._kis:
            kospi_task = self._kis.get_index_info("0001")
            kosdaq_task = self._kis.get_index_info("2001")
            kospi_info, kosdaq_info = await asyncio.gather(kospi_task, kosdaq_task)
            kospi = kospi_info.change_pct if kospi_info is not None else None
            kosdaq = kosdaq_info.change_pct if kosdaq_info is not None else None
            self._kospi_breadth_ratio = self._breadth_ratio(kospi_info)
            self._kosdaq_breadth_ratio = self._breadth_ratio(kosdaq_info)

            if kospi is not None:
                self._kospi_change = kospi
                self._init_failures = 0
                was_halted = self._halted
                self._halted = kospi <= self._config.kospi_halt_pct
                if not self._initialized:
                    self._initialized = True
                    logger.info("Market monitor initialized: KOSPI %.2f%%", kospi)
                if self._halted and not was_halted:
                    logger.warning("MARKET HALT: KOSPI %.2f%% <= %.1f%%", kospi, self._config.kospi_halt_pct)
                elif not self._halted and was_halted:
                    logger.info("Market halt lifted: KOSPI %.2f%%", kospi)
            elif not self._initialized:
                self._init_failures += 1
                logger.warning(
                    "Market monitor init failed (%d/%d): KOSPI index unavailable",
                    self._init_failures, self._MAX_INIT_FAILURES,
                )
                if self._init_failures >= self._MAX_INIT_FAILURES:
                    self._initialized = True
                    self._halted = False
                    logger.warning(
                        "Market monitor force-initialized after %d failures — trading allowed without KOSPI data",
                        self._init_failures,
                    )

            if kosdaq is not None:
                self._kosdaq_change = kosdaq

        # VKOSPI via pykrx (independent)
        try:
            vkospi = await _fetch_vkospi()
            if vkospi is not None:
                self._vkospi = vkospi
        except Exception:
            logger.exception("VKOSPI update failed")

        if self._config.macro_api_base_url:
            try:
                macro = await _fetch_macro_regime(
                    self._config.macro_api_base_url,
                    self._config.macro_api_timeout_s,
                )
                if macro is not None:
                    self._macro_overall_regime = macro.get("overall_regime")
                    self._macro_overall_confidence = macro.get("overall_confidence")
                    layers = macro.get("layers", {})
                    self._macro_kr_regime = layers.get("kr", {}).get("regime")
                    self._macro_crypto_regime = layers.get("crypto", {}).get("regime")
                    self._macro_position_multiplier = self._compute_position_multiplier()
            except Exception:
                logger.warning("Macro regime update failed", exc_info=True)

        self._last_ts = datetime.now(timezone.utc)
