"""Configuration constants and environment loading."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_bool(key: str, default: bool = False) -> bool:
    return _env(key, str(default)).lower() in ("true", "1", "yes")


def _env_int(key: str, default: int = 0) -> int:
    v = _env(key, "")
    return int(v) if v else default


def _env_float(key: str, default: float = 0.0) -> float:
    v = _env(key, "")
    return float(v) if v else default


@dataclass(frozen=True)
class Config:
    # --- Anthropic ---
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "claude-haiku-4-5-20251001"))
    llm_sdk_timeout_s: float = 10.0
    llm_wait_for_s: float = 12.0
    llm_cache_ttl_s: float = 60.0
    llm_cache_sweep_s: float = 300.0
    llm_max_concurrency: int = field(default_factory=lambda: _env_int("LLM_MAX_CONCURRENCY", 2))

    # --- KIS ---
    kis_app_key: str = field(default_factory=lambda: _env("KIS_APP_KEY"))
    kis_app_secret: str = field(default_factory=lambda: _env("KIS_APP_SECRET"))
    kis_account_no: str = field(default_factory=lambda: _env("KIS_ACCOUNT_NO"))
    kis_is_paper: bool = field(default_factory=lambda: _env_bool("KIS_IS_PAPER", True))

    # --- Feed ---
    feed_source: str = field(default_factory=lambda: _env("FEED_SOURCE", "KIS"))  # KIS or KIND
    kind_rss_url: str = "https://kind.krx.co.kr/disclosure/todaydisclosure.do?method=searchTodayDisclosureRSS"
    feed_interval_market_s: float = field(default_factory=lambda: _env_float("FEED_INTERVAL_MARKET", 3.0))
    feed_interval_off_s: float = field(default_factory=lambda: _env_float("FEED_INTERVAL_OFF", 15.0))
    feed_overlap_s: int = field(default_factory=lambda: _env_int("FEED_OVERLAP_S", 90))
    feed_jitter_pct: float = 0.20
    feed_backoff_threshold: int = 3
    feed_backoff_max_s: float = 60.0

    # --- Watchdog ---
    watchdog_interval_s: float = 30.0
    watchdog_stale_threshold_s: float = 120.0

    # --- Quant thresholds ---
    adv_threshold: float = field(default_factory=lambda: _env_float("ADV_THRESHOLD", 5_000_000_000))
    spread_bps_limit: float = 25.0
    extreme_move_pct: float = 20.0
    spread_check_enabled: bool = field(default_factory=lambda: _env_bool("SPREAD_CHECK_ENABLED", True))
    min_intraday_value_vs_adv20d: float = field(default_factory=lambda: _env_float("MIN_INTRADAY_VALUE_VS_ADV20D", 0.01))
    quant_fail_sample_rate: float = 0.10
    daily_loss_limit: float = field(default_factory=lambda: _env_float("DAILY_LOSS_LIMIT", 3_000_000))  # won
    max_positions: int = field(default_factory=lambda: _env_int("MAX_POSITIONS", 5))
    max_sector_positions: int = field(default_factory=lambda: _env_int("MAX_SECTOR_POSITIONS", 2))
    order_size: float = field(default_factory=lambda: _env_float("ORDER_SIZE", 5_000_000))  # won per trade

    # --- Market ---
    kospi_halt_pct: float = field(default_factory=lambda: _env_float("KOSPI_HALT_PCT", -8.0))
    min_market_breadth_ratio: float = field(default_factory=lambda: _env_float("MIN_MARKET_BREADTH_RATIO", 0.8))

    # --- Price snapshots ---
    snapshot_horizons: tuple[str, ...] = ("t0", "t+1m", "t+5m", "t+30m", "close")
    close_snapshot_delay_s: float = 300.0  # 15:31~15:35

    # --- Logging ---
    log_dir: Path = field(default_factory=lambda: Path(_env("LOG_DIR", "logs")))
    schema_version: str = "0.1.2"

    # --- Pipeline ---
    pipeline_workers: int = field(default_factory=lambda: _env_int("PIPELINE_WORKERS", 4))
    pipeline_queue_maxsize: int = field(default_factory=lambda: _env_int("PIPELINE_QUEUE_MAXSIZE", 512))

    # --- Runtime ---
    dry_run: bool = False
    paper: bool = False

    # --- Context Card ---
    pykrx_cache_ttl_s: int = field(default_factory=lambda: _env_int("PYKRX_CACHE_TTL_S", 300))
    pykrx_cache_max_size: int = field(default_factory=lambda: _env_int("PYKRX_CACHE_MAX_SIZE", 512))

    @property
    def kis_enabled(self) -> bool:
        return bool(self.kis_app_key and self.kis_app_secret)


def load_config(**overrides: object) -> Config:
    return Config(**overrides)  # type: ignore[arg-type]
