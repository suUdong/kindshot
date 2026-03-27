"""Helpers for consuming alpha-scanner HTTP surfaces from Kindshot."""

from __future__ import annotations

import logging
import time

import aiohttp

logger = logging.getLogger(__name__)

_SECTOR_ENDPOINT_CANDIDATES = (
    "/kindshot/sectors/snapshot",
    "/kindshot/sectors/current",
    "/kindshot/sector-api",
    "/sector-api",
)
_SECTOR_SNAPSHOT_TTL_S = 60.0
_sector_snapshot_cache: dict[str, tuple[dict, float]] = {}
_sector_endpoint_cache: dict[str, str] = {}


def _valid_sector_snapshot(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    prioritized = payload.get("prioritized_stocks")
    return payload.get("status") == "ok" and isinstance(prioritized, list)


async def fetch_alpha_scanner_sector_snapshot(
    base_url: str,
    timeout_s: float,
    *,
    top_n: int = 200,
) -> dict | None:
    """Fetch and cache the latest alpha-scanner sector snapshot.

    The alpha-scanner deployment path is not yet fixed inside Kindshot, so this
    probes a short list of compatible endpoints and caches the first success.
    """
    if not base_url:
        return None

    normalized_base = base_url.rstrip("/")
    now = time.monotonic()
    cached = _sector_snapshot_cache.get(normalized_base)
    if cached and cached[1] > now:
        return cached[0]

    timeout = aiohttp.ClientTimeout(total=timeout_s)
    candidate_paths = []
    cached_path = _sector_endpoint_cache.get(normalized_base)
    if cached_path:
        candidate_paths.append(cached_path)
    candidate_paths.extend(
        path for path in _SECTOR_ENDPOINT_CANDIDATES
        if path != cached_path
    )

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for path in candidate_paths:
            try:
                async with session.get(
                    f"{normalized_base}{path}",
                    params={"top": top_n},
                ) as response:
                    if response.status >= 400:
                        continue
                    payload = await response.json()
            except Exception as exc:
                logger.debug("Alpha-scanner sector snapshot fetch failed [%s%s]: %s", normalized_base, path, exc)
                continue

            if not _valid_sector_snapshot(payload):
                continue

            _sector_endpoint_cache[normalized_base] = path
            _sector_snapshot_cache[normalized_base] = (
                payload,
                time.monotonic() + _SECTOR_SNAPSHOT_TTL_S,
            )
            return payload

    return None


def lookup_sector_snapshot_ticker(snapshot: dict | None, ticker: str) -> dict | None:
    """Return the prioritized-stocks row for a ticker when present."""
    if not snapshot or not ticker:
        return None
    ticker_key = str(ticker).upper()
    for row in snapshot.get("prioritized_stocks", []):
        if str(row.get("ticker", "")).upper() == ticker_key:
            return row
    return None


def classify_sector_priority(row: dict | None) -> tuple[int, float, float]:
    """Return a stable priority key for the runtime queue.

    Lower values are processed first.
    """
    if not row:
        return (1, 0.0, 0.0)

    signal = str(row.get("sector_rotation_signal") or "").upper()
    if signal in {"LEADING", "IMPROVING"}:
        tier = 0
    elif signal in {"WEAKENING", "LAGGING"}:
        tier = 2
    else:
        tier = 1

    priority_score = -float(row.get("priority_score") or 0.0)
    momentum_score = -float(row.get("sector_momentum_score") or 0.0)
    return (tier, priority_score, momentum_score)
