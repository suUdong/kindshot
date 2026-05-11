"""LiveAutoRevertGuard — auto-paper-revert on intraday loss threshold.

Mirrors crypto-trader d6c2fb8 pattern. When cumulative intraday loss reaches
config.live_auto_revert_loss_pct (default 2%), the guard:
  1. writes config.live_auto_revert_flag_path as JSON for supervisor inspection
  2. returns True so the caller can flip the runtime back to paper / halt
The threshold is intentionally tighter than HARD_MAX_DAILY_LOSS_PCT (5%) so
live mode unwinds BEFORE the hard cap fires.

No-op when paper_trading=True or when live_auto_revert_loss_pct <= 0.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from kindshot.config import Config

logger = logging.getLogger(__name__)


class LiveAutoRevertGuard:
    """Single-shot revert guard. Re-triggers are idempotent."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._triggered_at: Optional[datetime] = None
        self._triggered_payload: Optional[dict[str, object]] = None

    @property
    def triggered(self) -> bool:
        return self._triggered_at is not None

    @property
    def triggered_at(self) -> Optional[datetime]:
        return self._triggered_at

    def check(self, daily_loss_pct: float, equity: float) -> bool:
        """Return True iff the revert should fire (now or already fired).

        `daily_loss_pct` is signed — negative means loss. Example: -0.025
        means a 2.5% intraday drawdown.
        """
        if self._config.paper_trading:
            return False
        threshold = self._config.live_auto_revert_loss_pct
        if threshold <= 0:
            return False
        if equity <= 0:
            return False
        if self._triggered_at is not None:
            return True  # idempotent — flag file already written
        if daily_loss_pct > -threshold:
            return False  # still within tolerance

        now = datetime.now(timezone.utc)
        self._triggered_at = now
        reason = (
            f"live_auto_paper_revert: daily_loss={daily_loss_pct:.4f} "
            f"<= -threshold={-threshold:.4f} equity={equity:.0f}"
        )
        payload = {
            "reason": reason,
            "triggered_at": now.isoformat(),
            "daily_loss_pct": daily_loss_pct,
            "threshold_pct": threshold,
            "equity": equity,
        }
        self._triggered_payload = payload
        self._write_flag(payload)
        logger.error("LIVE AUTO-REVERT TRIGGERED: %s", reason)
        return True

    def _write_flag(self, payload: dict[str, object]) -> None:
        path = Path(self._config.live_auto_revert_flag_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2))
        except OSError:
            logger.exception(
                "LiveAutoRevertGuard could not write flag file at %s", path,
            )

    def reset(self) -> None:
        """Operator hook — clear in-process state after manual review.

        Does NOT delete the flag file (that is the operator's job — the flag
        is supposed to be visible until the supervisor acknowledges it).
        """
        self._triggered_at = None
        self._triggered_payload = None
