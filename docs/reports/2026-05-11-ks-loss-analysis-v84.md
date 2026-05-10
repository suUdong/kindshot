# KS 전략 손실 분석 + v84 개선 (2026-05-11)

## 베이스라인
- 트레이드 수: 14
- AVG exit_ret_pct: **-0.646%**
- Win rate: 3/14 = 21.4%
- Median exit: -0.20%
- Range: [-2.78%, +0.44%]

## Exit Type 분포

| exit_type | count | avg_exit | avg_peak |
|-----------|------:|---------:|---------:|
| max_hold | 6 | -0.021% | +0.604% |
| stop_loss | 6 | -1.458% | +0.197% |
| t5m_loss_exit | 1 | -0.614% | 0% |
| timeout | 1 | +0.439% | +0.439% |

## 손실 원인 (Peak vs Exit gap 분석)

핵심 패턴 3가지:

### 1. Trailing stop 미작동 (peak +0.5%↑ 도달 후 -1%↓ 청산)
- 358570 POS_WEAK: peak +0.57% → exit -2.78% (gap 3.35%)
- 298040 POS_STRONG: peak +0.51% → exit -1.10% (gap 1.61%)
- 016360: peak +0.94% → exit +0.05% (gap 0.89%)

원인: `trailing_stop_activation_pct=0.5%`가 너무 높음. peak 0.5% 미만에서는 trailing 미작동.

### 2. Stop loss 과도 노출 (-1.5%까지 허용)
- 358570: -2.78% (POS_WEAK)
- 259960: -1.37%
- 070300: -1.30%
- 068270: -1.23%

원인: `paper_stop_loss_pct=-1.5%`가 너무 깊음. 평균 stop_loss -1.46%.

### 3. t5m_loss_exit 임계 너무 느슨
- 010140 t5m=-0.26%, threshold=-0.3%(통과) → t30m -0.96%로 확대

원인: `t5m_loss_exit_threshold_pct=-0.3%`. v83 -0.15→-0.3 완화가 과도.

### 4. 시그널 품질 (peak=0 trade 5건 = 36%)
- 010140, 068270(20260320), 070300, 001680, 068270(20260327)
- 진입 후 5~20m 정체 → t30m에 큰 손실 노출

원인: 데이터 해상도(t5m~t29m 미수집)일 가능성. live 환경에서는 다를 수 있음.

## v84 수정안 (4개 core config)

| 파라미터 | v83 | v84 | 근거 |
|---------|----:|----:|------|
| paper_stop_loss_pct | -1.5 | -1.0 | 358570 등 큰 손실 컷 |
| trailing_stop_activation_pct | 0.5 | 0.3 | peak 0.5% 미만 trailing 가동 |
| trailing_stop_early_pct | 0.5 | 0.4 | activation 0.3과 정합 |
| trailing_stop_mid_pct | 0.8 | 0.6 | 298040 같은 mid 손실 전환 방지 |
| trailing_stop_late_pct | 1.0 | 0.7 | late 구간 타이트닝 |
| t5m_loss_exit_threshold_pct | -0.3 | -0.2 | 010140 t5m=-0.26% 컷 |

## 검증 결과 (`classify_buy_exit` 실제 코드 시뮬)

- AVG: -0.646% → **-0.457%** (+0.189% 개선)
- Win rate: 3/14 → **4/14 (28.6%)**

주요 변화:
- 010140: stop_loss -0.96% → t5m_loss_exit -0.26% (+0.7%)
- 002990: max_hold 0% → take_profit +2.20% (+2.20%) — TP가 t+30m 시점 ret_t30m=+2.20%에 트리거

stagnation_exit는 002990(peak +2.2% at t+30m)을 죽일 위험이 있어 보류.

## 회귀 노트
- `t5m_loss_exit_threshold_pct -0.2`로 변경 시 068270 (t5m=-0.61%, t30m -0.12% 회복) 케이스는 임계 밖이라 영향 없음
- POS_WEAK은 `news_weak_enabled=False`로 이미 차단되어 신규 trade 영향 없음

## 다음 단계 (target: AVG ≥ 0%)
- 시그널 품질 필터 (peak=0 trade 36% 감소)
- Position sizing 차등화
- Take-profit ladder
