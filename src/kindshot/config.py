"""Configuration constants and environment loading."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
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


def _env_csv_tuple(key: str, default: str = "") -> tuple[str, ...]:
    raw = _env(key, default)
    if not raw:
        return ()
    items: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        value = part.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        items.append(value)
    return tuple(items)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_RISK_LIMITS_PATH = _REPO_ROOT / "config" / "risk_limits.toml"
_DEFAULT_MAX_POSITIONS = 4


@lru_cache(maxsize=1)
def _load_repo_config() -> dict[str, object]:
    if not _RISK_LIMITS_PATH.exists():
        return {}
    try:
        with _RISK_LIMITS_PATH.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _repo_int(section: str, key: str, default: int) -> int:
    section_data = _load_repo_config().get(section)
    if not isinstance(section_data, dict):
        return default
    value = section_data.get(key)
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _risk_cap_int(
    env_key: str,
    *,
    section: str,
    key: str,
    default: int,
    min_value: int,
    max_value: int,
) -> int:
    repo_value = _repo_int(section, key, default)
    raw = _env(env_key, "")
    if not raw:
        return repo_value
    try:
        value = int(raw)
    except ValueError:
        return repo_value
    if min_value <= value <= max_value:
        return value
    return repo_value


@dataclass(frozen=True)
class Config:
    # --- LLM provider (nvidia | anthropic) ---
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "nvidia"))
    # --- NVIDIA NIM (OpenAI-compatible) ---
    nvidia_api_key: str = field(default_factory=lambda: _env("NVIDIA_API_KEY"))
    nvidia_base_url: str = field(default_factory=lambda: _env("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"))
    nvidia_model: str = field(default_factory=lambda: _env("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct"))
    # --- Anthropic (fallback) ---
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    llm_fallback_enabled: bool = field(default_factory=lambda: _env_bool("LLM_FALLBACK_ENABLED", True))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "claude-haiku-4-5-20251001"))
    llm_sdk_timeout_s: float = 15.0  # SDK backup timeout (> wait_for)
    llm_wait_for_s: float = 12.0    # asyncio.wait_for fires first
    llm_cache_ttl_s: float = 60.0
    llm_cache_sweep_s: float = 300.0
    llm_max_concurrency: int = field(default_factory=lambda: _env_int("LLM_MAX_CONCURRENCY", 4))
    llm_cache_max_entries: int = field(default_factory=lambda: _env_int("LLM_CACHE_MAX_ENTRIES", 1024))

    # --- KIS ---
    kis_app_key: str = field(default_factory=lambda: _env("KIS_APP_KEY"))
    kis_app_secret: str = field(default_factory=lambda: _env("KIS_APP_SECRET"))
    kis_account_no: str = field(default_factory=lambda: _env("KIS_ACCOUNT_NO"))
    kis_is_paper: bool = field(default_factory=lambda: _env_bool("KIS_IS_PAPER", True))
    # 실전 서버 API 키 (모의투자 모드에서도 실시간 시세 조회용)
    kis_real_app_key: str = field(default_factory=lambda: _env("KIS_REAL_APP_KEY"))
    kis_real_app_secret: str = field(default_factory=lambda: _env("KIS_REAL_APP_SECRET"))

    # --- Feed ---
    feed_source: str = field(default_factory=lambda: _env("FEED_SOURCE", "KIS"))  # KIS, KIND, DART, or comma-separated (e.g. "KIS,DART")
    analyst_feed_enabled: bool = field(default_factory=lambda: _env_bool("ANALYST_FEED_ENABLED", True))
    analyst_feed_interval_s: float = field(default_factory=lambda: _env_float("ANALYST_FEED_INTERVAL_S", 30.0))
    # --- Y2I (유튜브 인사이트 시그널) ---
    y2i_feed_enabled: bool = field(default_factory=lambda: _env_bool("Y2I_FEED_ENABLED", False))
    y2i_signal_path: str = field(default_factory=lambda: _env("Y2I_SIGNAL_PATH", str(Path.home() / "workspace/y2i/.omx/state/kindshot_feed.json")))
    y2i_min_score: float = field(default_factory=lambda: _env_float("Y2I_MIN_SCORE", 55.0))
    y2i_min_verdict: str = field(default_factory=lambda: _env("Y2I_MIN_VERDICT", "WATCH"))  # WATCH, BUY, STRONG_BUY
    y2i_poll_interval_s: float = field(default_factory=lambda: _env_float("Y2I_POLL_INTERVAL_S", 60.0))
    y2i_lookback_days: int = field(default_factory=lambda: _env_int("Y2I_LOOKBACK_DAYS", 3))
    kind_rss_url: str = "https://kind.krx.co.kr/disclosure/todaydisclosure.do?method=searchTodayDisclosureRSS"
    # --- DART OpenAPI ---
    dart_api_key: str = field(default_factory=lambda: _env("DART_API_KEY"))
    dart_base_url: str = "https://opendart.fss.or.kr/api"
    dart_poll_page_count: int = field(default_factory=lambda: _env_int("DART_POLL_PAGE_COUNT", 20))  # 한 번에 가져올 공시 수
    # --- DART Buyback Strategy ---
    dart_buyback_enabled: bool = field(default_factory=lambda: _env_bool("DART_BUYBACK_ENABLED", True))
    dart_buyback_base_confidence: int = field(default_factory=lambda: _env_int("DART_BUYBACK_BASE_CONFIDENCE", 65))
    dart_buyback_direct_bonus: int = 15      # 직접매입 보너스
    dart_buyback_trust_bonus: int = 8        # 신탁매입 보너스
    dart_buyback_min_amount: int = field(default_factory=lambda: _env_int("DART_BUYBACK_MIN_AMOUNT", 1_000_000_000))  # 최소 10억
    # --- DART Earnings (PEAD) Strategy ---
    dart_earnings_enabled: bool = field(default_factory=lambda: _env_bool("DART_EARNINGS_ENABLED", True))
    dart_earnings_base_confidence: int = field(default_factory=lambda: _env_int("DART_EARNINGS_BASE_CONFIDENCE", 60))
    dart_earnings_yoy_bonus_30: int = 10     # YoY 30%+ 보너스
    dart_earnings_yoy_bonus_50: int = 15     # YoY 50%+ 보너스
    dart_earnings_yoy_bonus_100: int = 20    # YoY 100%+ 보너스
    dart_earnings_turnaround_bonus: int = 15  # 흑자전환 보너스
    dart_earnings_negative_skip: bool = field(default_factory=lambda: _env_bool("DART_EARNINGS_NEGATIVE_SKIP", True))  # 부정 서프라이즈 SKIP
    # --- Short Overheating (공매도 과열 해제) Strategy ---
    short_overheating_enabled: bool = field(default_factory=lambda: _env_bool("SHORT_OVERHEATING_ENABLED", False))
    short_overheating_base_confidence: int = field(default_factory=lambda: _env_int("SHORT_OVERHEATING_BASE_CONFIDENCE", 60))
    short_overheating_poll_interval_s: float = field(default_factory=lambda: _env_float("SHORT_OVERHEATING_POLL_INTERVAL_S", 3600.0))  # 1시간
    short_overheating_lookback_days: int = field(default_factory=lambda: _env_int("SHORT_OVERHEATING_LOOKBACK_DAYS", 7))
    short_overheating_d_offset: int = field(default_factory=lambda: _env_int("SHORT_OVERHEATING_D_OFFSET", 2))  # D+2
    short_overheating_min_overheating_days: int = field(default_factory=lambda: _env_int("SHORT_OVERHEATING_MIN_DAYS", 1))
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
    pos_strong_adv_threshold: float = field(default_factory=lambda: _env_float("POS_STRONG_ADV_THRESHOLD", 300_000_000))
    spread_bps_limit: float = 50.0
    extreme_move_pct: float = 20.0
    spread_check_enabled: bool = field(default_factory=lambda: _env_bool("SPREAD_CHECK_ENABLED", True))
    spread_missing_policy: str = field(default_factory=lambda: _env("SPREAD_MISSING_POLICY", "pass"))  # "pass" = fail-open, "fail" = fail-close
    min_intraday_value_vs_adv20d: float = field(default_factory=lambda: _env_float("MIN_INTRADAY_VALUE_VS_ADV20D", 0.05))  # 0.15→0.05: 뉴스 직후 거래대금 자연히 낮음, 과도 차단 완화
    max_entry_delay_ms: int = field(default_factory=lambda: _env_int("MAX_ENTRY_DELAY_MS", 60_000))
    orderbook_bid_ask_ratio_min: float = field(default_factory=lambda: _env_float("ORDERBOOK_BID_ASK_RATIO_MIN", 0.8))
    min_prior_volume_rate: float = field(default_factory=lambda: _env_float("MIN_PRIOR_VOLUME_RATE", 70.0))
    # 20일 평균거래량 대비 당일 누적거래량 비율 (0.1 = 10%)
    min_volume_ratio_vs_avg20d: float = field(default_factory=lambda: _env_float("MIN_VOLUME_RATIO_VS_AVG20D", 0.05))  # 5% 미만 → 유동성 스킵
    volume_ratio_surge_threshold: float = field(default_factory=lambda: _env_float("VOLUME_RATIO_SURGE_THRESHOLD", 2.0))  # 200% → 급증 부스트
    prior_volume_gate_start_kst_hour: int = field(default_factory=lambda: _env_int("PRIOR_VOLUME_GATE_START_KST_HOUR", 10))
    prior_volume_gate_start_kst_minute: int = field(default_factory=lambda: _env_int("PRIOR_VOLUME_GATE_START_KST_MINUTE", 0))
    chase_buy_pct: float = field(default_factory=lambda: _env_float("CHASE_BUY_PCT", 5.0))  # 3.0→5.0: 뉴스 기반 3-5% 상승은 정상 반응, 추격매수 기준 완화
    min_buy_confidence: int = field(default_factory=lambda: _env_int("MIN_BUY_CONFIDENCE", 78))  # v81: 73→78 (conf 75-77 borderline 전부 손실: 크래프톤 -1.37%, 지아이이노 -2.78%)
    no_buy_after_kst_hour: int = field(default_factory=lambda: _env_int("NO_BUY_AFTER_KST_HOUR", 15))  # 15시 이후 BUY 차단
    no_buy_after_kst_minute: int = field(default_factory=lambda: _env_int("NO_BUY_AFTER_KST_MINUTE", 15))  # 15:00→15:15: KRX 15:30 마감, 15분 여유 확보
    # 가상 익절/손절 (paper mode 추적용)
    paper_take_profit_pct: float = field(default_factory=lambda: _env_float("PAPER_TAKE_PROFIT_PCT", 2.0))  # 2.0% 기본 익절 (v65: 1.0→2.0, R:R 비율 개선)
    paper_stop_loss_pct: float = field(default_factory=lambda: _env_float("PAPER_STOP_LOSS_PCT", -1.5))  # -1.5% 손절 (V자 반등 대응, 기존 -0.7%에서 완화)
    # Trailing stop + 30분 룰
    trailing_stop_enabled: bool = field(default_factory=lambda: _env_bool("TRAILING_STOP_ENABLED", True))
    trailing_stop_pct: float = field(default_factory=lambda: _env_float("TRAILING_STOP_PCT", 1.0))  # v70: 0.8→1.0% (3/27: 조기 trailing→종가 수익 유실 방지)
    trailing_stop_activation_pct: float = field(default_factory=lambda: _env_float("TRAILING_STOP_ACTIVATION_PCT", 0.5))  # v83: 0.2→0.5% (14건 분석: 0.2%=노이즈, 너무 조기 활성→peak 못 잡음)
    # 시간대별 trailing stop 폭 (진입 후 경과 시간 기준) — v65: 전체 완화
    trailing_stop_early_pct: float = field(default_factory=lambda: _env_float("TRAILING_STOP_EARLY_PCT", 0.5))  # v83: 0.3→0.5 (activation 0.5%와 조합, 노이즈 pullback에 조기 청산 방지)
    trailing_stop_mid_pct: float = field(default_factory=lambda: _env_float("TRAILING_STOP_MID_PCT", 0.8))  # 5~30분: v65 0.5→0.8 (추세 유지)
    trailing_stop_late_pct: float = field(default_factory=lambda: _env_float("TRAILING_STOP_LATE_PCT", 1.0))  # 30분+: v65 0.7→1.0 (장기 홀드 여유)
    max_hold_minutes: int = field(default_factory=lambda: _env_int("MAX_HOLD_MINUTES", 30))  # v83: 20→30분 (002990: peak +2.2% at t+30m, 20분 max_hold로 0% 청산 — 모멘텀 소화 시간 확보)
    # t+5m 체크포인트 청산: 5분 경과 시 손실이면 즉시 청산, 수익이면 타이트 trailing
    t5m_loss_exit_enabled: bool = field(default_factory=lambda: _env_bool("T5M_LOSS_EXIT_ENABLED", True))
    t5m_loss_exit_threshold_pct: float = field(default_factory=lambda: _env_float("T5M_LOSS_EXIT_THRESHOLD_PCT", -0.3))  # v83: -0.15→-0.3% (068270: t5m -0.61%→t30m -0.12% 회복 가능, -0.15%는 과도한 조기 컷)
    t5m_profit_trailing_pct: float = field(default_factory=lambda: _env_float("T5M_PROFIT_TRAILING_PCT", 0.5))  # v65: 0.2→0.5% t+5m 이후 수익 포지션 trailing (기존 너무 타이트)
    partial_take_profit_enabled: bool = field(default_factory=lambda: _env_bool("PARTIAL_TAKE_PROFIT_ENABLED", True))
    partial_take_profit_target_ratio: float = field(default_factory=lambda: _env_float("PARTIAL_TAKE_PROFIT_TARGET_RATIO", 1.0))
    partial_take_profit_size_pct: float = field(default_factory=lambda: _env_float("PARTIAL_TAKE_PROFIT_SIZE_PCT", 50.0))
    trailing_stop_post_partial_early_pct: float = field(default_factory=lambda: _env_float("TRAILING_STOP_POST_PARTIAL_EARLY_PCT", 0.4))
    trailing_stop_post_partial_mid_pct: float = field(default_factory=lambda: _env_float("TRAILING_STOP_POST_PARTIAL_MID_PCT", 0.6))
    trailing_stop_post_partial_late_pct: float = field(default_factory=lambda: _env_float("TRAILING_STOP_POST_PARTIAL_LATE_PCT", 0.8))
    news_exit_enabled: bool = field(default_factory=lambda: _env_bool("NEWS_EXIT_ENABLED", True))
    support_exit_enabled: bool = field(default_factory=lambda: _env_bool("SUPPORT_EXIT_ENABLED", True))
    support_exit_buffer_pct: float = field(default_factory=lambda: _env_float("SUPPORT_EXIT_BUFFER_PCT", 0.2))
    # 시간대별 청산 차등
    session_early_sl_multiplier: float = field(default_factory=lambda: _env_float("SESSION_EARLY_SL_MULT", 1.0))  # v83: 0.7→1.0 (세션 초반 SL 타이트닝 제거 — 259960: SL -1.37% vs close -0.53%, whipsaw 손실 유발)
    session_late_max_hold_divisor: float = field(default_factory=lambda: _env_float("SESSION_LATE_MAX_HOLD_DIV", 2.0))  # 14:00+ max_hold 축소 (÷2)
    quant_fail_sample_rate: float = 0.10
    daily_loss_limit: float = field(default_factory=lambda: _env_float("DAILY_LOSS_LIMIT", 3_000_000))  # won
    daily_loss_limit_pct: float = field(default_factory=lambda: _env_float("DAILY_LOSS_LIMIT_PCT", -1.0))  # 계좌 대비 -1% 도달 시 당일 BUY 중단
    dynamic_daily_loss_enabled: bool = field(default_factory=lambda: _env_bool("DYNAMIC_DAILY_LOSS_ENABLED", True))
    dynamic_daily_loss_size_down_multiplier: float = field(default_factory=lambda: _env_float("DYNAMIC_DAILY_LOSS_SIZE_DOWN_MULT", 0.75))
    dynamic_daily_loss_halt_multiplier: float = field(default_factory=lambda: _env_float("DYNAMIC_DAILY_LOSS_HALT_MULT", 0.5))
    dynamic_daily_loss_profit_lock_ratio: float = field(default_factory=lambda: _env_float("DYNAMIC_DAILY_LOSS_PROFIT_LOCK_RATIO", 0.5))
    dynamic_daily_loss_recent_trade_window: int = field(default_factory=lambda: _env_int("DYNAMIC_DAILY_LOSS_RECENT_TRADE_WINDOW", 4))
    dynamic_daily_loss_recent_trade_min_samples: int = field(default_factory=lambda: _env_int("DYNAMIC_DAILY_LOSS_RECENT_TRADE_MIN_SAMPLES", 3))
    dynamic_daily_loss_low_win_rate_threshold: float = field(default_factory=lambda: _env_float("DYNAMIC_DAILY_LOSS_LOW_WIN_RATE_THRESHOLD", 0.5))
    dynamic_daily_loss_low_win_rate_multiplier: float = field(default_factory=lambda: _env_float("DYNAMIC_DAILY_LOSS_LOW_WIN_RATE_MULT", 0.75))
    dynamic_daily_loss_zero_win_rate_multiplier: float = field(default_factory=lambda: _env_float("DYNAMIC_DAILY_LOSS_ZERO_WIN_RATE_MULT", 0.5))
    # 킬 스위치: 연패 기반 size 축소 & 당일 중단
    consecutive_loss_size_down: int = field(default_factory=lambda: _env_int("CONSECUTIVE_LOSS_SIZE_DOWN", 2))  # N연패 시 size 한단계 다운
    consecutive_loss_halt: int = field(default_factory=lambda: _env_int("CONSECUTIVE_LOSS_HALT", 3))  # N연패 시 당일 BUY 중단
    # Repo-owned paper-trading guardrail. Legacy unlimited env values are ignored.
    max_positions: int = field(
        default_factory=lambda: _risk_cap_int(
            "MAX_POSITIONS",
            section="portfolio_risk",
            key="max_positions",
            default=_DEFAULT_MAX_POSITIONS,
            min_value=1,
            max_value=5,
        )
    )
    max_sector_positions: int = field(default_factory=lambda: _env_int("MAX_SECTOR_POSITIONS", 2))
    order_size: float = field(default_factory=lambda: _env_float("ORDER_SIZE", 5_000_000))  # won per trade (기본, M size)
    order_size_l: float = field(default_factory=lambda: _env_float("ORDER_SIZE_L", 7_000_000))  # L size (high confidence)
    order_size_s: float = field(default_factory=lambda: _env_float("ORDER_SIZE_S", 3_000_000))  # S size (low confidence/wide spread)
    # 포지션 사이징 제약
    account_risk_pct: float = field(default_factory=lambda: _env_float("ACCOUNT_RISK_PCT", 2.0))  # 계좌 대비 최대 리스크 %
    minute_volume_cap_pct: float = field(default_factory=lambda: _env_float("MINUTE_VOLUME_CAP_PCT", 5.0))  # 1분 거래대금의 5%
    ask_depth_cap_pct: float = field(default_factory=lambda: _env_float("ASK_DEPTH_CAP_PCT", 10.0))  # 매도 5호가 잔량의 10%
    # v76: ATR 기반 변동성 정규화 포지션 사이징
    position_sizing_base_atr_pct: float = field(default_factory=lambda: _env_float("POSITION_SIZING_BASE_ATR_PCT", 2.0))  # 기준 ATR (이 값 대비 스케일링)
    position_sizing_atr_max_scale: float = field(default_factory=lambda: _env_float("POSITION_SIZING_ATR_MAX_SCALE", 1.3))  # 저변동성 시 최대 확대 배율
    # 마이크로 라이브: 1건당 주문 금액 상한 (안전장치)
    micro_live_max_order_won: float = field(default_factory=lambda: _env_float("MICRO_LIVE_MAX_ORDER_WON", 1_000_000))
    # 시간대별 confidence 문턱
    early_session_block_end_minute: int = field(default_factory=lambda: _env_int("EARLY_SESSION_BLOCK_END_MINUTE", 30))  # v84: 09:MM 이전 BUY 전면 차단 (08-09시 8건 전패 -7.99%)
    opening_min_confidence: int = field(default_factory=lambda: _env_int("OPENING_MIN_CONFIDENCE", 88))  # v73: 85→88 (09시대 87% 손실률 — 최고 확신만 진입)
    midmorning_min_confidence: int = field(default_factory=lambda: _env_int("MIDMORNING_MIN_CONFIDENCE", 75))  # v84: 10:00~11:30 최적 구간 — confidence 완화 (승률 60%)
    afternoon_min_confidence: int = field(default_factory=lambda: _env_int("AFTERNOON_MIN_CONFIDENCE", 80))  # 13:00-14:30 BUY 최소 confidence (오후 승률 저조)
    closing_min_confidence: int = field(default_factory=lambda: _env_int("CLOSING_MIN_CONFIDENCE", 85))  # 14:30-15:00 BUY 최소 confidence
    fast_profile_hold_minutes: int = field(default_factory=lambda: _env_int("FAST_PROFILE_HOLD_MINUTES", 30))  # v83: 20→30 (hold_profile 수주/공급계약 30분과 일치)
    fast_profile_no_buy_after_kst_hour: int = field(default_factory=lambda: _env_int("FAST_PROFILE_NO_BUY_AFTER_KST_HOUR", 14))  # fast profile BUY 차단
    fast_profile_no_buy_after_kst_minute: int = field(default_factory=lambda: _env_int("FAST_PROFILE_NO_BUY_AFTER_KST_MINUTE", 30))  # 14:00→14:30: 20분 보유 가능 시간 확보
    dynamic_guardrails_enabled: bool = field(default_factory=lambda: _env_bool("DYNAMIC_GUARDRAILS_ENABLED", True))
    dynamic_guardrail_supportive_index_change_pct: float = field(default_factory=lambda: _env_float("DYNAMIC_GUARDRAIL_SUPPORTIVE_INDEX_CHANGE_PCT", 0.3))
    dynamic_guardrail_supportive_breadth_ratio: float = field(default_factory=lambda: _env_float("DYNAMIC_GUARDRAIL_SUPPORTIVE_BREADTH_RATIO", 0.55))
    dynamic_guardrail_confidence_relaxation: int = field(default_factory=lambda: _env_int("DYNAMIC_GUARDRAIL_CONFIDENCE_RELAXATION", 2))
    dynamic_fast_profile_extension_minutes: int = field(default_factory=lambda: _env_int("DYNAMIC_FAST_PROFILE_EXTENSION_MINUTES", 60))
    news_weak_enabled: bool = field(default_factory=lambda: _env_bool("NEWS_WEAK_ENABLED", False))

    # --- MTF (Multi-Timeframe) ---
    mtf_enabled: bool = field(default_factory=lambda: _env_bool("MTF_ENABLED", True))
    mtf_cache_ttl_s: int = field(default_factory=lambda: _env_int("MTF_CACHE_TTL_S", 120))
    technical_strategy_enabled: bool = field(default_factory=lambda: _env_bool("TECHNICAL_STRATEGY_ENABLED", False))
    technical_strategy_tickers: tuple[str, ...] = field(default_factory=lambda: _env_csv_tuple("TECHNICAL_STRATEGY_TICKERS"))
    technical_strategy_poll_interval_s: float = field(default_factory=lambda: _env_float("TECHNICAL_STRATEGY_POLL_INTERVAL_S", 120.0))
    technical_strategy_signal_cooldown_s: float = field(default_factory=lambda: _env_float("TECHNICAL_STRATEGY_SIGNAL_COOLDOWN_S", 1800.0))
    technical_strategy_min_alignment_score: int = field(default_factory=lambda: _env_int("TECHNICAL_STRATEGY_MIN_ALIGNMENT_SCORE", 75))
    technical_strategy_min_rsi: float = field(default_factory=lambda: _env_float("TECHNICAL_STRATEGY_MIN_RSI", 52.0))
    technical_strategy_max_rsi: float = field(default_factory=lambda: _env_float("TECHNICAL_STRATEGY_MAX_RSI", 72.0))
    technical_strategy_min_macd_hist: float = field(default_factory=lambda: _env_float("TECHNICAL_STRATEGY_MIN_MACD_HIST", 0.0))
    technical_strategy_max_bb_position: float = field(default_factory=lambda: _env_float("TECHNICAL_STRATEGY_MAX_BB_POSITION", 85.0))
    technical_strategy_min_volume_ratio_vs_avg20d: float = field(default_factory=lambda: _env_float("TECHNICAL_STRATEGY_MIN_VOLUME_RATIO_VS_AVG20D", 0.15))
    technical_strategy_min_ret_today: float = field(default_factory=lambda: _env_float("TECHNICAL_STRATEGY_MIN_RET_TODAY", 0.0))

    # --- Market ---
    kospi_halt_pct: float = field(default_factory=lambda: _env_float("KOSPI_HALT_PCT", -8.0))
    min_market_breadth_ratio: float = field(default_factory=lambda: _env_float("MIN_MARKET_BREADTH_RATIO", 0.25))  # v73: 0.3→0.25 (3/26 15건 과도한 RISK_OFF 차단 완화)

    # --- Price snapshots ---
    snapshot_horizons: tuple[str, ...] = ("t0", "t+30s", "t+1m", "t+2m", "t+5m", "t+10m", "t+15m", "t+20m", "t+30m", "close")
    close_snapshot_delay_s: float = 300.0  # 15:31~15:35

    # --- Health ---
    health_host: str = field(default_factory=lambda: _env("HEALTH_HOST", "127.0.0.1"))
    health_port: int = field(default_factory=lambda: _env_int("HEALTH_PORT", 8080))
    health_latency_window_size: int = field(default_factory=lambda: _env_int("HEALTH_LATENCY_WINDOW_SIZE", 200))
    macro_api_base_url: str = field(default_factory=lambda: _env("MACRO_API_BASE_URL", ""))
    macro_api_timeout_s: float = field(default_factory=lambda: _env_float("MACRO_API_TIMEOUT_S", 5.0))
    macro_filter_enabled: bool = field(default_factory=lambda: _env_bool("MACRO_FILTER_ENABLED", True))
    alpha_scanner_api_base_url: str = field(default_factory=lambda: _env("ALPHA_SCANNER_API_BASE_URL", ""))
    alpha_scanner_api_timeout_s: float = field(default_factory=lambda: _env_float("ALPHA_SCANNER_API_TIMEOUT_S", 5.0))

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
    collector_backfill_report_path: Path = field(default_factory=lambda: Path(_env("COLLECTOR_BACKFILL_REPORT_PATH", "data/collector/backfill/latest.json")))
    collector_backfill_auto_report_path: Path = field(default_factory=lambda: Path(_env("COLLECTOR_BACKFILL_AUTO_REPORT_PATH", "data/collector/backfill/auto_latest.json")))
    runtime_price_snapshots_dir: Path = field(default_factory=lambda: Path(_env("RUNTIME_PRICE_SNAPSHOTS_DIR", "data/runtime/price_snapshots")))
    runtime_market_context_dir: Path = field(default_factory=lambda: Path(_env("RUNTIME_MARKET_CONTEXT_DIR", "data/runtime/market_context")))
    runtime_context_cards_dir: Path = field(default_factory=lambda: Path(_env("RUNTIME_CONTEXT_CARDS_DIR", "data/runtime/context_cards")))
    runtime_index_path: Path = field(default_factory=lambda: Path(_env("RUNTIME_INDEX_PATH", "data/runtime/index.json")))
    llm_cache_dir: Path = field(default_factory=lambda: Path(_env("LLM_CACHE_DIR", "data/runtime/llm_cache")))
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

    # --- Ticker Learning ---
    ticker_learning_enabled: bool = field(default_factory=lambda: _env_bool("TICKER_LEARNING_ENABLED", True))
    ticker_learning_min_trades: int = field(default_factory=lambda: _env_int("TICKER_LEARNING_MIN_TRADES", 3))

    # --- Recent pattern profile ---
    recent_pattern_enabled: bool = field(default_factory=lambda: _env_bool("RECENT_PATTERN_ENABLED", True))
    recent_pattern_profile_path: Path = field(default_factory=lambda: Path(_env("RECENT_PATTERN_PROFILE_PATH", "data/runtime/recent_pattern_profile.json")))
    recent_pattern_lookback_days: int = field(default_factory=lambda: _env_int("RECENT_PATTERN_LOOKBACK_DAYS", 7))
    recent_pattern_min_trades: int = field(default_factory=lambda: _env_int("RECENT_PATTERN_MIN_TRADES", 2))
    recent_pattern_profit_boost: int = field(default_factory=lambda: _env_int("RECENT_PATTERN_PROFIT_BOOST", 5))  # v73: 3→5 (midday 수익 패턴 부스트 강화)
    recent_pattern_profit_min_win_rate: float = field(default_factory=lambda: _env_float("RECENT_PATTERN_PROFIT_MIN_WIN_RATE", 0.5))
    recent_pattern_profit_min_total_pnl_pct: float = field(default_factory=lambda: _env_float("RECENT_PATTERN_PROFIT_MIN_TOTAL_PNL_PCT", 0.15))
    recent_pattern_loss_max_win_rate: float = field(default_factory=lambda: _env_float("RECENT_PATTERN_LOSS_MAX_WIN_RATE", 0.30))  # v73: 0.25→0.30 (더 많은 손실 패턴 캡처)
    recent_pattern_loss_max_total_pnl_pct: float = field(default_factory=lambda: _env_float("RECENT_PATTERN_LOSS_MAX_TOTAL_PNL_PCT", -0.3))  # v73: -0.5→-0.3 (약한 손실도 조기 차단)
    recent_pattern_max_profit_patterns: int = field(default_factory=lambda: _env_int("RECENT_PATTERN_MAX_PROFIT_PATTERNS", 5))  # v72: 2→5 (수익 패턴 더 많이 캡처)
    recent_pattern_max_loss_patterns: int = field(default_factory=lambda: _env_int("RECENT_PATTERN_MAX_LOSS_PATTERNS", 5))  # v72: 2→5 (손실 패턴 더 많이 캡처)

    # --- Intraday performance monitor (v72) ---
    intraday_monitor_enabled: bool = field(default_factory=lambda: _env_bool("INTRADAY_MONITOR_ENABLED", True))
    intraday_monitor_interval_s: int = field(default_factory=lambda: _env_int("INTRADAY_MONITOR_INTERVAL_S", 1800))  # 30분마다 장중 성과 리포트
    intraday_monitor_min_trades: int = field(default_factory=lambda: _env_int("INTRADAY_MONITOR_MIN_TRADES", 1))  # 최소 N건 이상일 때만 발송

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

    def adv_threshold_for_bucket(self, bucket: str) -> float:
        """Return the effective ADV threshold for a bucket."""
        if bucket == "POS_STRONG":
            return min(self.adv_threshold, self.pos_strong_adv_threshold)
        return self.adv_threshold

    def validate(self) -> list[str]:
        """Return config warnings. Raises ValueError on fatal issues."""
        import logging
        _log = logging.getLogger(__name__)
        warnings: list[str] = []

        # LLM provider 유효성 검사
        if self.llm_provider not in ("nvidia", "anthropic"):
            raise ValueError(f"LLM_PROVIDER must be 'nvidia' or 'anthropic', got '{self.llm_provider}'")

        if self.llm_provider == "nvidia" and not self.nvidia_api_key:
            warnings.append("NVIDIA_API_KEY not set (primary LLM provider)")
            _log.warning("Config: NVIDIA_API_KEY not set — will fallback to Anthropic")

        if not self.anthropic_api_key:
            warnings.append("ANTHROPIC_API_KEY not set")
            if self.llm_provider == "anthropic":
                _log.warning("Config: ANTHROPIC_API_KEY not set — LLM calls will fail")
            else:
                _log.info("Config: ANTHROPIC_API_KEY not set — fallback unavailable")

        if self.paper_take_profit_pct <= 0:
            raise ValueError(f"paper_take_profit_pct must be positive, got {self.paper_take_profit_pct}")
        if self.paper_stop_loss_pct >= 0:
            raise ValueError(f"paper_stop_loss_pct must be negative, got {self.paper_stop_loss_pct}")
        if not (0 < self.partial_take_profit_target_ratio <= 1):
            raise ValueError(
                "partial_take_profit_target_ratio must be between 0 and 1 inclusive, "
                f"got {self.partial_take_profit_target_ratio}"
            )
        if not (0 < self.partial_take_profit_size_pct < 100):
            raise ValueError(
                f"partial_take_profit_size_pct must be between 0 and 100, got {self.partial_take_profit_size_pct}"
            )
        if self.trailing_stop_post_partial_early_pct <= 0:
            raise ValueError(
                "trailing_stop_post_partial_early_pct must be positive, "
                f"got {self.trailing_stop_post_partial_early_pct}"
            )
        if self.trailing_stop_post_partial_mid_pct <= 0:
            raise ValueError(
                "trailing_stop_post_partial_mid_pct must be positive, "
                f"got {self.trailing_stop_post_partial_mid_pct}"
            )
        if self.trailing_stop_post_partial_late_pct <= 0:
            raise ValueError(
                "trailing_stop_post_partial_late_pct must be positive, "
                f"got {self.trailing_stop_post_partial_late_pct}"
            )
        if not (0 < self.dynamic_daily_loss_size_down_multiplier <= 1):
            raise ValueError(
                "dynamic_daily_loss_size_down_multiplier must be within (0, 1]"
            )
        if not (0 < self.dynamic_daily_loss_halt_multiplier <= 1):
            raise ValueError(
                "dynamic_daily_loss_halt_multiplier must be within (0, 1]"
            )
        if not (0 <= self.dynamic_daily_loss_profit_lock_ratio <= 1):
            raise ValueError(
                f"dynamic_daily_loss_profit_lock_ratio must be between 0 and 1, got {self.dynamic_daily_loss_profit_lock_ratio}"
            )
        if self.dynamic_daily_loss_recent_trade_window <= 0:
            raise ValueError("dynamic_daily_loss_recent_trade_window must be positive")
        if self.dynamic_daily_loss_recent_trade_min_samples <= 0:
            raise ValueError("dynamic_daily_loss_recent_trade_min_samples must be positive")
        if self.dynamic_daily_loss_recent_trade_min_samples > self.dynamic_daily_loss_recent_trade_window:
            raise ValueError("dynamic_daily_loss_recent_trade_min_samples must be <= dynamic_daily_loss_recent_trade_window")
        if not (0 <= self.dynamic_daily_loss_low_win_rate_threshold <= 1):
            raise ValueError("dynamic_daily_loss_low_win_rate_threshold must be within 0..1")
        if not (0 < self.dynamic_daily_loss_low_win_rate_multiplier <= 1):
            raise ValueError("dynamic_daily_loss_low_win_rate_multiplier must be within (0, 1]")
        if not (0 < self.dynamic_daily_loss_zero_win_rate_multiplier <= 1):
            raise ValueError("dynamic_daily_loss_zero_win_rate_multiplier must be within (0, 1]")
        if self.chase_buy_pct <= 0:
            raise ValueError(f"chase_buy_pct must be positive, got {self.chase_buy_pct}")
        if not (0 <= self.min_buy_confidence <= 100):
            raise ValueError(f"min_buy_confidence must be 0-100, got {self.min_buy_confidence}")
        if self.adv_threshold < 0:
            raise ValueError(f"adv_threshold must be non-negative, got {self.adv_threshold}")
        if self.pos_strong_adv_threshold < 0:
            raise ValueError(
                f"pos_strong_adv_threshold must be non-negative, got {self.pos_strong_adv_threshold}"
            )
        if not (1 <= self.max_positions <= 5):
            raise ValueError("max_positions must be within 1..5")
        if self.recent_pattern_lookback_days <= 0:
            raise ValueError("recent_pattern_lookback_days must be positive")
        if self.recent_pattern_min_trades <= 0:
            raise ValueError("recent_pattern_min_trades must be positive")
        if not (0 <= self.recent_pattern_profit_boost <= 20):
            raise ValueError("recent_pattern_profit_boost must be within 0..20")
        if not (0.0 <= self.recent_pattern_profit_min_win_rate <= 1.0):
            raise ValueError("recent_pattern_profit_min_win_rate must be within 0..1")
        if not (0.0 <= self.recent_pattern_loss_max_win_rate <= 1.0):
            raise ValueError("recent_pattern_loss_max_win_rate must be within 0..1")
        if self.technical_strategy_poll_interval_s <= 0:
            raise ValueError("technical_strategy_poll_interval_s must be positive")
        if self.technical_strategy_signal_cooldown_s < 0:
            raise ValueError("technical_strategy_signal_cooldown_s must be non-negative")
        if not (0 <= self.technical_strategy_min_alignment_score <= 100):
            raise ValueError("technical_strategy_min_alignment_score must be within 0..100")
        if not (0 <= self.technical_strategy_min_rsi <= 100):
            raise ValueError("technical_strategy_min_rsi must be within 0..100")
        if not (0 <= self.technical_strategy_max_rsi <= 100):
            raise ValueError("technical_strategy_max_rsi must be within 0..100")
        if self.technical_strategy_min_rsi > self.technical_strategy_max_rsi:
            raise ValueError("technical_strategy_min_rsi must be <= technical_strategy_max_rsi")
        if not (0 <= self.technical_strategy_max_bb_position <= 100):
            raise ValueError("technical_strategy_max_bb_position must be within 0..100")
        if self.technical_strategy_min_volume_ratio_vs_avg20d < 0:
            raise ValueError("technical_strategy_min_volume_ratio_vs_avg20d must be non-negative")

        if not self.kis_app_key or not self.kis_app_secret:
            warnings.append("KIS API keys not set")

        return warnings


def load_config(**overrides: object) -> Config:
    cfg = Config(**overrides)  # type: ignore[arg-type]
    cfg.validate()
    return cfg
