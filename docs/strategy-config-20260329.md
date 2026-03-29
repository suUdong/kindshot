# Kindshot 전략 구성 현황 (v84, 2026-03-29)

## 1. 활성 전략 목록

| 전략 | 상태 | 설명 |
|------|------|------|
| **뉴스 (POS_STRONG)** | **활성** | KIS/DART 공시 기반 긍정 키워드 매칭 → LLM 판단 → 진입 |
| **뉴스 (POS_WEAK)** | **비활성** | `news_weak_enabled=False` — 약한 긍정 뉴스 진입 차단 |
| **뉴스 (NEG_STRONG)** | **활성** | 부정 공시 감지 시 보유 포지션 강제 청산 (news_exit) |
| **Y2I (유튜브 인사이트)** | **비활성** | `y2i_feed_enabled=False` — 유튜브 시그널 피드 꺼짐 |
| **TA (기술적 분석)** | **비활성** | `technical_strategy_enabled=False` — 순수 TA 진입 꺼짐 |
| **MTF (Multi-Timeframe)** | **활성** | `mtf_enabled=True` — 뉴스 판단 시 멀티 타임프레임 보조 지표로 활용 |
| **Alpha Scanner** | **활성** | 섹터 스냅샷 조회, 파이프라인 우선순위 결정에 활용 |
| **UNKNOWN 섀도 리뷰** | **활성** | 미분류 공시 LLM 재평가 → 승격 가능 (`unknown_shadow_review_enabled=True`) |

> **요약**: 현재 핵심 전략은 **뉴스(POS_STRONG) + MTF 보조 + Alpha Scanner 우선순위**. POS_WEAK, Y2I, TA는 모두 비활성.

---

## 2. 전략별 파라미터 요약

### 2.1 뉴스 전략 (핵심)

| 파라미터 | 값 | 설명 |
|----------|-----|------|
| `feed_source` | KIS | 주 피드 소스 |
| `feed_interval_market_s` | 3.0s | 장중 폴링 간격 |
| `feed_interval_off_s` | 15.0s | 장외 폴링 간격 |
| `llm_provider` | nvidia (→ Llama 3.3 70B) | 1차 LLM |
| `llm_fallback_enabled` | True → claude-haiku-4-5 | NVIDIA 실패 시 폴백 |
| `min_buy_confidence` | 78 | 기본 BUY 최소 confidence |
| `chase_buy_pct` | 5.0% | 당일 5% 이상 상승 시 추격매수 차단 |
| `news_weak_enabled` | False | POS_WEAK 버킷 진입 차단 |
| `news_exit_enabled` | True | NEG_STRONG 시 보유 포지션 강제 청산 |

### 2.2 버킷 분류

| 버킷 | 우선순위 | 동작 |
|------|----------|------|
| IGNORE_OVERRIDE | 1 | 무조건 무시 |
| NEG_STRONG | 2 | 보유 포지션 강제 청산 |
| POS_STRONG | 3 | **LLM 판단 → 진입** |
| NEG_WEAK | 4 | 스킵 |
| POS_WEAK | 5 | 비활성 (news_weak_enabled=False) |
| IGNORE | 6 | 스킵 |
| UNKNOWN | 7 | 섀도 리뷰 → 승격 가능 |

### 2.3 포지션 사이징

| 파라미터 | 값 | 설명 |
|----------|-----|------|
| `order_size` (M) | 500만원 | 기본 주문 크기 |
| `order_size_l` (L) | 700만원 | 고확신 주문 |
| `order_size_s` (S) | 300만원 | 저확신/넓은 스프레드 |
| `max_positions` | 4 | 동시 보유 최대 포지션 |
| `max_sector_positions` | 2 | 동일 섹터 최대 포지션 |
| `account_risk_pct` | 2.0% | 계좌 대비 최대 리스크 |
| `minute_volume_cap_pct` | 5.0% | 1분 거래대금의 5% 이내 |
| `ask_depth_cap_pct` | 10.0% | 매도 5호가 잔량의 10% 이내 |
| ATR 스케일링 | base 2.0%, max 1.3x | 저변동성 시 최대 30% 확대 |

### 2.4 익절/손절 (Exit Management)

| 파라미터 | 값 | 설명 |
|----------|-----|------|
| `paper_take_profit_pct` | +2.0% | 익절 목표 |
| `paper_stop_loss_pct` | -1.5% | 손절 라인 |
| `trailing_stop_enabled` | True | 트레일링 스탑 활성 |
| `trailing_stop_activation_pct` | +0.5% | 활성화 기준 (v83: 0.2→0.5) |
| `trailing_stop_pct` | 1.0% | 기본 trailing 폭 |
| `trailing_stop_early_pct` | 0.5% | 0~5분: 조기 pullback 방지 |
| `trailing_stop_mid_pct` | 0.8% | 5~30분: 추세 유지 |
| `trailing_stop_late_pct` | 1.0% | 30분+: 장기 홀드 여유 |
| `max_hold_minutes` | 30분 | 최대 보유 시간 (v83: 20→30) |
| `t5m_loss_exit_threshold_pct` | -0.3% | t+5분 체크포인트 손절 (v83: -0.15→-0.3) |
| `partial_take_profit_enabled` | True | 반분할 익절 (TP의 100% 도달 시 50% 청산) |

### 2.5 보유시간 프로파일 (Hold Profile)

| 키워드 유형 | 보유시간 | 예시 |
|------------|----------|------|
| 공급계약/수주 | 30분 | 공급계약, 수주, 납품계약 |
| 특허/임상3상/FDA | 30분 | FDA, 품목허가, 임상3상, 특허 |
| 임상2상/기술수출 | 20분 | 임상2상, 기술수출, CDMO |
| 주주환원(소각/취득) | EOD | 자사주 소각, 배당, 주주환원 |
| M&A | 30분 | 합병, 인수 |
| 공개매수/경영권 | EOD | 공개매수, 경영권 분쟁 |
| 실적 서프라이즈 | 30분 | 어닝 서프라이즈, 흑자전환 |
| 기본값 | 30분 | `max_hold_minutes` |

### 2.6 Quant 필터

| 파라미터 | 값 | 설명 |
|----------|-----|------|
| `adv_threshold` | 5억원 | 20일 평균거래대금 최소 |
| `pos_strong_adv_threshold` | 3억원 | POS_STRONG 완화 기준 |
| `spread_bps_limit` | 50bp | 스프레드 상한 |
| `extreme_move_pct` | 20% | 극단적 등락 차단 |
| `min_volume_ratio_vs_avg20d` | 5% | 20일 평균 대비 최소 거래량 |
| `volume_ratio_surge_threshold` | 200% | 급증 판정 기준 |
| `max_entry_delay_ms` | 60초 | 공시 후 60초 이내만 진입 |

---

## 3. 시간대별 필터 설정

### v84 시간대별 Confidence 문턱

```
08:00 ─────────── 09:00 ── 09:30 ── 10:00 ──────── 11:30 ── 13:00 ── 14:30 ── 15:00 ── 15:15
  │                  │        │        │               │        │        │        │        │
  ├── BLOCKED ───────┤        │        │               │        │        │        │        │
  │ (EARLY_SESSION)  │        │        │               │        │        │        │        │
  │ 08-09시 전면차단  ├────────┤        │               │        │        │        │        │
  │ 8건 전패 -7.99%  │ BLOCK  │        │               │        │        │        │        │
  │                  │ ~09:30 │        │               │        │        │        │        │
  │                  │        ├────────┤               │        │        │        │        │
  │                  │        │ conf≥88│               │        │        │        │        │
  │                  │        │ OPENING│               │        │        │        │        │
  │                  │        │        ├───────────────┤        │        │        │        │
  │                  │        │        │   conf≥75     │        │        │        │        │
  │                  │        │        │  MIDMORNING   │        │        │        │        │
  │                  │        │        │  (최적 구간)   │        │        │        │        │
  │                  │        │        │               ├────────┤        │        │        │
  │                  │        │        │               │ conf≥78│        │        │        │
  │                  │        │        │               │ 기본    │        │        │        │
  │                  │        │        │               │        ├────────┤        │        │
  │                  │        │        │               │        │ conf≥80│        │        │
  │                  │        │        │               │        │ AFTRN  │        │        │
  │                  │        │        │               │        │        ├────────┤        │
  │                  │        │        │               │        │        │ conf≥85│        │
  │                  │        │        │               │        │        │ CLOSING│        │
  │                  │        │        │               │        │        │        ├────────┤
  │                  │        │        │               │        │        │        │CUTOFF  │
  │                  │        │        │               │        │        │        │15:15   │
```

### 시간대별 요약 테이블

| 시간대 | 필터 | Confidence 문턱 | 비고 |
|--------|------|-----------------|------|
| 08:00~09:00 | **전면 차단** | - | v84: 8건 전패 -7.99%, 전체 손실 88% |
| 09:00~09:30 | **전면 차단** | - | `early_session_block_end_minute=30` |
| 09:30~10:00 | 개장 초반 | ≥ 88 | 잔여 변동성, 최고 확신만 |
| 10:00~11:30 | **최적 구간** | ≥ 75 | 승률 60% 기대, confidence 완화 |
| 11:30~13:00 | 기본 | ≥ 78 | 점심시간, 스프레드 강화 (×0.7) |
| 13:00~14:30 | 오후 | ≥ 80 | 승률 저조 구간 |
| 14:30~15:00 | 마감 임박 | ≥ 85 | 확실한 촉매만 |
| 15:00~15:15 | Fast profile 차단 | - | fast_profile 종목 BUY 차단 |
| 15:15~ | **전면 차단** | - | `no_buy_after_kst_hour=15, minute=15` |

### 추가 시간대 필터

- **11:00~14:00**: 스프레드 기준 70%로 강화 (midday_spread_limit = 35bp)
- **14:00+**: `max_hold_minutes` ÷ 2 (15분으로 축소)
- **장 초반 SL 배율**: `session_early_sl_multiplier=1.0` (v83: whipsaw 방지로 타이트닝 제거)

---

## 4. 가드레일 설정 현황

### 4.1 포트폴리오 레벨

| 가드레일 | 값 | 설명 |
|----------|-----|------|
| `daily_loss_limit` | 300만원 | 일일 손실 한도 (절대값) |
| `daily_loss_limit_pct` | -1.0% | 계좌 대비 일일 손실 한도 |
| `consecutive_loss_size_down` | 2연패 | 2연패 시 size 한 단계 다운 |
| `consecutive_loss_halt` | 3연패 | 3연패 시 당일 BUY 중단 |
| `dynamic_daily_loss_enabled` | True | 동적 손실 예산 관리 |
| 동적 손실 - size down | 0.75x | 연패 시 손실 예산 75%로 축소 |
| 동적 손실 - halt | 0.50x | 3연패 시 50%로 축소 |
| 동적 손실 - profit lock | 50% | 수익 발생 시 50% 잠금 |
| 승률 기반 조절 | < 50% → 0.75x, 0% → 0.50x | 최근 4건 기준 |
| 동일 종목 재매수 | 차단 | `SAME_STOCK_REBUY` |

### 4.2 종목 레벨

| 가드레일 | 값 | 설명 |
|----------|-----|------|
| 스프레드 | ≤ 50bp (11~14시: 35bp) | 유동성 확인 |
| ADV 20일 | ≥ 5억 (POS_STRONG: 3억) | 거래대금 확인 |
| 극단적 등락 | ±20% 초과 차단 | 서킷브레이커 등 |
| 호가 유동성 | 1호가 ≥ 주문금액 50% | 체결 가능성 |
| 거래정지/관리종목 | 차단 | KRX 마커 기반 |
| 진입 지연 | ≤ 60초 | 공시 후 1분 이내 |
| 추격매수 | 당일 +5% 초과 차단 | 고점 추격 방지 |

### 4.3 시장 레벨

| 가드레일 | 값 | 설명 |
|----------|-----|------|
| `kospi_halt_pct` | -8.0% | KOSPI -8% 시 전면 중단 |
| `min_market_breadth_ratio` | 0.25 | 상승종목 비율 25% 미만 → RISK_OFF |
| Dynamic guardrails | 지수 +0.3% & breadth > 55% 시 confidence -2 완화 | 시장 우호적일 때 |

### 4.4 학습/패턴 기반

| 기능 | 상태 | 설명 |
|------|------|------|
| Ticker Learning | 활성 | 종목별 과거 3건+ 성과 기반 조정 |
| Recent Pattern | 활성 | 최근 7일 패턴 프로파일 (수익 +5, 손실 차단) |
| Intraday Monitor | 활성 | 30분마다 장중 성과 리포트 |

---

## 5. 월요일 (2026-03-31) 예상 시나리오

v84 설정 기준 월요일 장중 예상 시나리오:

### 08:00~09:30 — 진입 0건 (차단)

- `early_session_block_end_minute=30` → 09:30 이전 모든 BUY 차단
- 08시대 공시는 감지/분류만 수행, 진입 불가
- NEG_STRONG 뉴스 감지 시 기존 포지션 강제 청산은 동작
- **근거**: v84 이전 08-09시 8건 전패, -7.99% 손실 (전체 손실의 88%)

### 09:30~10:00 — Confidence 88+ 만 진입

- `opening_min_confidence=88` 적용
- LLM이 88 이상 판정한 최고 확신 공시만 진입
- 예상 진입: 0~1건 (대형 수주/계약/FDA 등 강한 촉매에 한정)
- 스프레드/ADV 등 기본 quant 필터 병행

### 10:00~11:30 — 메인 진입 구간 (승률 60% 기대)

- `midmorning_min_confidence=75` — 가장 완화된 구간
- 과거 데이터 기반 승률 60% 최적 구간
- 예상 진입: 2~4건 (공시 밀도에 따라)
- MTF 보조 지표 + Alpha Scanner 섹터 우선순위 활용
- 트레일링 스탑 0.5% 활성화 → peak 추적

### 11:30~13:00 — 점심시간 일반 운영

- 기본 `min_buy_confidence=78` 적용
- 스프레드 기준 강화 (50bp → 35bp)
- 거래량 감소 구간 — 유동성 낮은 종목 자연 필터링
- 예상 진입: 0~1건

### 13:00~14:30 — 오후 운영 (보수적)

- `afternoon_min_confidence=80`
- 승률 저조 구간이므로 높은 확신 요구
- `max_hold_minutes` ÷ 2 = 15분 (14시 이후)
- 예상 진입: 0~1건

### 14:30~15:15 — 마감 임박 (매우 보수적)

- `closing_min_confidence=85`
- Fast profile 종목 14:30 이후 차단
- 15:15 이후 전면 BUY 차단 (KRX 15:30 마감, 15분 여유)
- 예상 진입: 0건 (예외적 촉매에 한해 1건)

### 일일 예상 요약

| 항목 | 예상치 |
|------|--------|
| 총 진입 | 2~5건 |
| 메인 구간 (10:00~11:30) | 2~4건 |
| 기타 구간 합산 | 0~1건 |
| 기대 승률 | 50~60% (메인 구간 기준) |
| R:R 비율 | TP +2.0% / SL -1.5% = 1.33:1 |
| 일일 최대 손실 | -300만원 또는 계좌 -1% |
| 연패 차단 | 3연패 시 자동 중단 |

### 리스크 시나리오

- **시장 급락 (KOSPI -3% 이상)**: breadth < 25% → RISK_OFF, 진입 대폭 축소
- **KOSPI -8%**: 전면 중단 (서킷브레이커급)
- **연패 발생**: 2연패 → size down (M→S), 3연패 → 당일 BUY 중단
- **시장 우호적 (지수 +0.3%, breadth 55%+)**: confidence 문턱 -2 완화, fast profile 연장

---

## 변경 이력

- **v84 (2026-03-29)**: 장 초반 09:00~09:30 BUY 전면 차단, midmorning confidence 완화 (78→75)
- **v83**: trailing 활성화 0.2→0.5%, t5m 체크포인트 -0.15→-0.3%, max_hold 20→30분
- **v82**: 세션 초반 SL 타이트닝 제거, 수주/공급계약 hold 20→30분
