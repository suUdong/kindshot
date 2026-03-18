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
    llm_sdk_timeout_s: float = 15.0  # SDK backup timeout (> wait_for)
    llm_wait_for_s: float = 12.0    # asyncio.wait_for fires first
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
    adv_threshold: float = field(default_factory=lambda: _env_float("ADV_THRESHOLD", 500_000_000))
    spread_bps_limit: float = 50.0
    extreme_move_pct: float = 20.0
    spread_check_enabled: bool = field(default_factory=lambda: _env_bool("SPREAD_CHECK_ENABLED", True))
    spread_missing_policy: str = field(default_factory=lambda: _env("SPREAD_MISSING_POLICY", "pass"))  # "pass" = fail-open, "fail" = fail-close
    min_intraday_value_vs_adv20d: float = field(default_factory=lambda: _env_float("MIN_INTRADAY_VALUE_VS_ADV20D", 0.01))
    chase_buy_pct: float = field(default_factory=lambda: _env_float("CHASE_BUY_PCT", 5.0))  # 당일 5%+ 상승 시 BUY 차단
    min_buy_confidence: int = field(default_factory=lambda: _env_int("MIN_BUY_CONFIDENCE", 70))  # BUY 최소 confidence
    no_buy_after_kst_hour: int = field(default_factory=lambda: _env_int("NO_BUY_AFTER_KST_HOUR", 15))  # 15시 이후 BUY 차단
    no_buy_after_kst_minute: int = field(default_factory=lambda: _env_int("NO_BUY_AFTER_KST_MINUTE", 0))  # 15:00 이후 차단
    # 가상 익절/손절 (paper mode 추적용)
    paper_take_profit_pct: float = field(default_factory=lambda: _env_float("PAPER_TAKE_PROFIT_PCT", 0.0))  # 0=비활성 (뉴스 트레이딩은 상승 제한 안 함)
    paper_stop_loss_pct: float = field(default_factory=lambda: _env_float("PAPER_STOP_LOSS_PCT", -1.5))  # -1.5% 손절 (뉴스 변동성 감안)
    quant_fail_sample_rate: float = 0.10
    daily_loss_limit: float = field(default_factory=lambda: _env_float("DAILY_LOSS_LIMIT", 3_000_000))  # won
    max_positions: int = field(default_factory=lambda: _env_int("MAX_POSITIONS", 5))
    max_sector_positions: int = field(default_factory=lambda: _env_int("MAX_SECTOR_POSITIONS", 2))
    order_size: float = field(default_factory=lambda: _env_float("ORDER_SIZE", 5_000_000))  # won per trade (기본, M size)
    order_size_l: float = field(default_factory=lambda: _env_float("ORDER_SIZE_L", 7_000_000))  # L size (high confidence)
    order_size_s: float = field(default_factory=lambda: _env_float("ORDER_SIZE_S", 3_000_000))  # S size (low confidence/wide spread)

    # --- Market ---
    kospi_halt_pct: float = field(default_factory=lambda: _env_float("KOSPI_HALT_PCT", -8.0))
    min_market_breadth_ratio: float = field(default_factory=lambda: _env_float("MIN_MARKET_BREADTH_RATIO", 0.3))

    # --- Price snapshots ---
    snapshot_horizons: tuple[str, ...] = ("t0", "t+1m", "t+5m", "t+30m", "close")
    close_snapshot_delay_s: float = 300.0  # 15:31~15:35

    # --- Logging ---
    log_dir: Path = field(default_factory=lambda: Path(_env("LOG_DIR", "logs")))
    schema_version: str = "0.1.2"

    # --- Collector ---
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "data")))
    collector_news_dir: Path = field(default_factory=lambda: Path(_env("COLLECTOR_NEWS_DIR", "data/collector/news")))
    collector_classifications_dir: Path = field(default_factory=lambda: Path(_env("COLLECTOR_CLASSIFICATIONS_DIR", "data/collector/classifications")))
    collector_daily_prices_dir: Path = field(default_factory=lambda: Path(_env("COLLECTOR_DAILY_PRICES_DIR", "data/collector/daily_prices")))
    collector_index_dir: Path = field(default_factory=lambda: Path(_env("COLLECTOR_INDEX_DIR", "data/collector/index")))
    collector_manifests_dir: Path = field(default_factory=lambda: Path(_env("COLLECTOR_MANIFESTS_DIR", "data/collector/manifests")))
    collector_log_path: Path = field(default_factory=lambda: Path(_env("COLLECTOR_LOG_PATH", "data/collector/collection_log.jsonl")))
    collector_state_path: Path = field(default_factory=lambda: Path(_env("COLLECTOR_STATE_PATH", "data/collector_state.json")))
    runtime_price_snapshots_dir: Path = field(default_factory=lambda: Path(_env("RUNTIME_PRICE_SNAPSHOTS_DIR", "data/runtime/price_snapshots")))
    runtime_market_context_dir: Path = field(default_factory=lambda: Path(_env("RUNTIME_MARKET_CONTEXT_DIR", "data/runtime/market_context")))
    runtime_context_cards_dir: Path = field(default_factory=lambda: Path(_env("RUNTIME_CONTEXT_CARDS_DIR", "data/runtime/context_cards")))
    runtime_index_path: Path = field(default_factory=lambda: Path(_env("RUNTIME_INDEX_PATH", "data/runtime/index.json")))
    replay_day_reports_dir: Path = field(default_factory=lambda: Path(_env("REPLAY_DAY_REPORTS_DIR", "data/replay/day_reports")))
    replay_day_status_dir: Path = field(default_factory=lambda: Path(_env("REPLAY_DAY_STATUS_DIR", "data/replay/day_status")))
    replay_ops_summary_path: Path = field(default_factory=lambda: Path(_env("REPLAY_OPS_SUMMARY_PATH", "data/replay/ops/latest.json")))
    replay_ops_queue_ready_path: Path = field(default_factory=lambda: Path(_env("REPLAY_OPS_QUEUE_READY_PATH", "data/replay/ops/queue_ready_latest.json")))
    replay_ops_run_ready_path: Path = field(default_factory=lambda: Path(_env("REPLAY_OPS_RUN_READY_PATH", "data/replay/ops/run_ready_latest.json")))
    replay_ops_cycle_ready_path: Path = field(default_factory=lambda: Path(_env("REPLAY_OPS_CYCLE_READY_PATH", "data/replay/ops/cycle_ready_latest.json")))
    unknown_shadow_review_enabled: bool = field(default_factory=lambda: _env_bool("UNKNOWN_SHADOW_REVIEW_ENABLED", False))
    unknown_paper_promotion_enabled: bool = field(default_factory=lambda: _env_bool("UNKNOWN_PAPER_PROMOTION_ENABLED", False))
    unknown_promotion_min_confidence: int = field(default_factory=lambda: _env_int("UNKNOWN_PROMOTION_MIN_CONFIDENCE", 85))
    unknown_review_queue_maxsize: int = field(default_factory=lambda: _env_int("UNKNOWN_REVIEW_QUEUE_MAXSIZE", 256))
    unknown_review_article_enrichment_enabled: bool = field(default_factory=lambda: _env_bool("UNKNOWN_REVIEW_ARTICLE_ENRICHMENT_ENABLED", False))
    unknown_review_article_timeout_s: float = field(default_factory=lambda: _env_float("UNKNOWN_REVIEW_ARTICLE_TIMEOUT_S", 5.0))
    unknown_review_article_max_chars: int = field(default_factory=lambda: _env_int("UNKNOWN_REVIEW_ARTICLE_MAX_CHARS", 4000))
    unknown_inbox_dir: Path = field(default_factory=lambda: Path(_env("UNKNOWN_INBOX_DIR", "logs/unknown_inbox")))
    unknown_review_dir: Path = field(default_factory=lambda: Path(_env("UNKNOWN_REVIEW_DIR", "logs/unknown_review")))
    unknown_promotion_dir: Path = field(default_factory=lambda: Path(_env("UNKNOWN_PROMOTION_DIR", "logs/unknown_promotion")))
    unknown_review_ops_summary_path: Path = field(default_factory=lambda: Path(_env("UNKNOWN_REVIEW_OPS_SUMMARY_PATH", "data/unknown_review/ops/latest.json")))
    unknown_review_rule_report_path: Path = field(default_factory=lambda: Path(_env("UNKNOWN_REVIEW_RULE_REPORT_PATH", "data/unknown_review/rule_report/latest.json")))
    unknown_review_rule_queue_path: Path = field(default_factory=lambda: Path(_env("UNKNOWN_REVIEW_RULE_QUEUE_PATH", "data/unknown_review/rule_queue/latest.json")))
    unknown_review_rule_patch_path: Path = field(default_factory=lambda: Path(_env("UNKNOWN_REVIEW_RULE_PATCH_PATH", "data/unknown_review/rule_patch/latest.json")))
    unknown_rule_queue_min_reviews: int = field(default_factory=lambda: _env_int("UNKNOWN_RULE_QUEUE_MIN_REVIEWS", 2))
    unknown_rule_queue_min_promoted: int = field(default_factory=lambda: _env_int("UNKNOWN_RULE_QUEUE_MIN_PROMOTED", 1))
    finalize_cutoff_hour_kst: int = field(default_factory=lambda: _env_int("FINALIZE_CUTOFF_HOUR_KST", 2))
    finalize_cutoff_minute_kst: int = field(default_factory=lambda: _env_int("FINALIZE_CUTOFF_MINUTE_KST", 30))
    collector_news_max_attempts: int = field(default_factory=lambda: _env_int("COLLECTOR_NEWS_MAX_ATTEMPTS", 3))
    collector_retry_delay_s: float = field(default_factory=lambda: _env_float("COLLECTOR_RETRY_DELAY_S", 1.0))

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

    def order_size_for_hint(self, size_hint: str) -> float:
        """size_hint(L/M/S)에 따라 주문 크기 반환."""
        if size_hint == "L":
            return self.order_size_l
        if size_hint == "S":
            return self.order_size_s
        return self.order_size


def load_config(**overrides: object) -> Config:
    return Config(**overrides)  # type: ignore[arg-type]
