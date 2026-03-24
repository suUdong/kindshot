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
    chase_buy_pct: float = field(default_factory=lambda: _env_float("CHASE_BUY_PCT", 3.0))  # 당일 3%+ 상승 시 BUY 차단 (추격매수 방지)
    min_buy_confidence: int = field(default_factory=lambda: _env_int("MIN_BUY_CONFIDENCE", 72))  # BUY 최소 confidence (약한 촉매 필터)
    no_buy_after_kst_hour: int = field(default_factory=lambda: _env_int("NO_BUY_AFTER_KST_HOUR", 15))  # 15시 이후 BUY 차단
    no_buy_after_kst_minute: int = field(default_factory=lambda: _env_int("NO_BUY_AFTER_KST_MINUTE", 0))  # 15:00 이후 차단
    # 가상 익절/손절 (paper mode 추적용)
    paper_take_profit_pct: float = field(default_factory=lambda: _env_float("PAPER_TAKE_PROFIT_PCT", 0.8))  # 0.8% 익절 (뉴스 반응 특성상 1.5%는 과도)
    paper_stop_loss_pct: float = field(default_factory=lambda: _env_float("PAPER_STOP_LOSS_PCT", -1.0))  # -1.0% 손절
    # Trailing stop + 30분 룰
    trailing_stop_enabled: bool = field(default_factory=lambda: _env_bool("TRAILING_STOP_ENABLED", True))
    trailing_stop_pct: float = field(default_factory=lambda: _env_float("TRAILING_STOP_PCT", 0.5))  # 기본 trailing (5분 이후)
    trailing_stop_activation_pct: float = field(default_factory=lambda: _env_float("TRAILING_STOP_ACTIVATION_PCT", 0.3))  # 0.3% 이상 수익 시 trailing 활성화
    # 시간대별 trailing stop 폭 (진입 후 경과 시간 기준)
    trailing_stop_early_pct: float = field(default_factory=lambda: _env_float("TRAILING_STOP_EARLY_PCT", 0.3))  # 0~5분: 타이트
    trailing_stop_mid_pct: float = field(default_factory=lambda: _env_float("TRAILING_STOP_MID_PCT", 0.5))  # 5~30분: 보통
    trailing_stop_late_pct: float = field(default_factory=lambda: _env_float("TRAILING_STOP_LATE_PCT", 0.7))  # 30분+: 완화
    max_hold_minutes: int = field(default_factory=lambda: _env_int("MAX_HOLD_MINUTES", 30))  # 최대 보유 30분 (0=비활성)
    quant_fail_sample_rate: float = 0.10
    daily_loss_limit: float = field(default_factory=lambda: _env_float("DAILY_LOSS_LIMIT", 3_000_000))  # won
    # 킬 스위치: 연패 기반 size 축소 & 당일 중단
    consecutive_loss_size_down: int = field(default_factory=lambda: _env_int("CONSECUTIVE_LOSS_SIZE_DOWN", 2))  # N연패 시 size 한단계 다운
    consecutive_loss_halt: int = field(default_factory=lambda: _env_int("CONSECUTIVE_LOSS_HALT", 3))  # N연패 시 당일 BUY 중단
    max_positions: int = field(default_factory=lambda: _env_int("MAX_POSITIONS", 5))
    max_sector_positions: int = field(default_factory=lambda: _env_int("MAX_SECTOR_POSITIONS", 2))
    order_size: float = field(default_factory=lambda: _env_float("ORDER_SIZE", 5_000_000))  # won per trade (기본, M size)
    order_size_l: float = field(default_factory=lambda: _env_float("ORDER_SIZE_L", 7_000_000))  # L size (high confidence)
    order_size_s: float = field(default_factory=lambda: _env_float("ORDER_SIZE_S", 3_000_000))  # S size (low confidence/wide spread)

    # --- Market ---
    kospi_halt_pct: float = field(default_factory=lambda: _env_float("KOSPI_HALT_PCT", -8.0))
    min_market_breadth_ratio: float = field(default_factory=lambda: _env_float("MIN_MARKET_BREADTH_RATIO", 0.3))

    # --- Price snapshots ---
    snapshot_horizons: tuple[str, ...] = ("t0", "t+30s", "t+1m", "t+2m", "t+5m", "t+30m", "close")
    close_snapshot_delay_s: float = 300.0  # 15:31~15:35

    # --- Health ---
    health_host: str = field(default_factory=lambda: _env("HEALTH_HOST", "127.0.0.1"))
    health_port: int = field(default_factory=lambda: _env_int("HEALTH_PORT", 8080))

    # --- Logging ---
    log_dir: Path = field(default_factory=lambda: Path(_env("LOG_DIR", "logs")))
    schema_version: str = "0.1.3"

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
    unknown_shadow_review_enabled: bool = field(default_factory=lambda: _env_bool("UNKNOWN_SHADOW_REVIEW_ENABLED", True))
    unknown_paper_promotion_enabled: bool = field(default_factory=lambda: _env_bool("UNKNOWN_PAPER_PROMOTION_ENABLED", True))
    unknown_promotion_min_confidence: int = field(default_factory=lambda: _env_int("UNKNOWN_PROMOTION_MIN_CONFIDENCE", 85))
    unknown_review_queue_maxsize: int = field(default_factory=lambda: _env_int("UNKNOWN_REVIEW_QUEUE_MAXSIZE", 256))
    unknown_review_article_enrichment_enabled: bool = field(default_factory=lambda: _env_bool("UNKNOWN_REVIEW_ARTICLE_ENRICHMENT_ENABLED", True))
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

    def validate(self) -> list[str]:
        """Return config warnings. Raises ValueError on fatal issues."""
        import logging
        _log = logging.getLogger(__name__)
        warnings: list[str] = []

        if not self.anthropic_api_key:
            warnings.append("ANTHROPIC_API_KEY not set")
            _log.warning("Config: ANTHROPIC_API_KEY not set — LLM calls will fail")

        if self.paper_take_profit_pct <= 0:
            raise ValueError(f"paper_take_profit_pct must be positive, got {self.paper_take_profit_pct}")
        if self.paper_stop_loss_pct >= 0:
            raise ValueError(f"paper_stop_loss_pct must be negative, got {self.paper_stop_loss_pct}")
        if self.chase_buy_pct <= 0:
            raise ValueError(f"chase_buy_pct must be positive, got {self.chase_buy_pct}")
        if not (0 <= self.min_buy_confidence <= 100):
            raise ValueError(f"min_buy_confidence must be 0-100, got {self.min_buy_confidence}")
        if self.adv_threshold < 0:
            raise ValueError(f"adv_threshold must be non-negative, got {self.adv_threshold}")

        if not self.kis_app_key or not self.kis_app_secret:
            warnings.append("KIS API keys not set")

        return warnings


def load_config(**overrides: object) -> Config:
    cfg = Config(**overrides)  # type: ignore[arg-type]
    cfg.validate()
    return cfg
