"""BrokerFactory — env-driven Paper / VTS / Live selection.

Selection rules:
    paper_trading=True
        → PaperBroker (no preflight gate)
    paper_trading=False AND preflight_check ERROR rows present
        → RuntimeError (refuse to enter live)
    paper_trading=False AND live_dry_run=True AND real creds present
        → LiveBroker(dry_run=True)
    paper_trading=False AND KIS_REAL_APP_KEY/SECRET present
        → LiveBroker
    paper_trading=False AND only KIS_APP_KEY/SECRET present
        → VTSBroker (모의투자) — preflight blocks live cutover, but VTS is the
          natural fallback for staging
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import aiohttp

from kindshot.broker.base import BrokerInterface
from kindshot.broker.live import LiveBroker
from kindshot.broker.paper import PaperBroker
from kindshot.broker.vts import VTSBroker
from kindshot.config import Config, preflight_check

logger = logging.getLogger(__name__)


def build_broker(
    config: Config,
    *,
    session: Optional[aiohttp.ClientSession] = None,
    env: Optional[dict[str, str]] = None,
) -> BrokerInterface:
    """Return the broker mandated by config + preflight gates.

    Raises RuntimeError if paper_trading=False AND preflight_check yields any
    ERROR row — the caller is then expected to fall back to PaperBroker or
    abort startup. We do NOT silently downgrade to paper in live mode.
    """
    if config.paper_trading:
        broker = PaperBroker(starting_cash=config.order_size * 10)
        logger.info("BrokerFactory: paper_trading=True → PaperBroker")
        return broker

    issues = preflight_check(config, env=env)
    errors = [(lvl, msg) for lvl, msg in issues if lvl == "ERROR"]
    if errors:
        error_text = "; ".join(msg for _, msg in errors)
        raise RuntimeError(
            f"Live preflight failed — refusing to build live broker. ERROR rows: {error_text}"
        )

    if config.kis_real_app_key and config.kis_real_app_secret:
        dry = config.live_dry_run
        logger.warning(
            "BrokerFactory: paper_trading=False → LiveBroker(dry_run=%s)", dry,
        )
        return LiveBroker(config, session=session, dry_run=dry)

    if config.kis_app_key and config.kis_app_secret:
        logger.warning(
            "BrokerFactory: paper_trading=False but only paper creds present → VTSBroker"
        )
        return VTSBroker(config, session=session)

    raise RuntimeError(
        "Live mode requested but no KIS credentials configured (KIS_REAL_APP_KEY or KIS_APP_KEY)."
    )


def select_broker_kind(config: Config, env: Optional[dict[str, str]] = None) -> str:
    """Inspection helper — what would build_broker pick, without instantiating it.

    Returns one of: 'paper', 'vts', 'live', 'live-dry', 'blocked'.
    """
    if config.paper_trading:
        return "paper"
    if env is None:
        env = dict(os.environ)
    issues = preflight_check(config, env=env)
    if any(lvl == "ERROR" for lvl, _ in issues):
        return "blocked"
    if config.kis_real_app_key and config.kis_real_app_secret:
        return "live-dry" if config.live_dry_run else "live"
    if config.kis_app_key and config.kis_app_secret:
        return "vts"
    return "blocked"
