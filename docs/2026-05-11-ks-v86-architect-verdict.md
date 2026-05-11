# KS v86 활성화 — Architect 독립 검증 (2026-05-11)

## Verdict: **CONCERN**
활성화 자체는 합리적이나 forward-looking 권고 6건 필요.

| # | Severity | 항목 | 노력 | 즉시 적용 |
|---|----------|------|------|----------|
| 1 | High | 091990 universe 제거 + backtest 재실행 | S | ❌ (backtest 코드 노터치 제약) |
| 2 | Med  | 실거래시 SIGTERM grace 3s → 30s | S | ✅ ops note |
| 3 | Med  | diagnose 에 cooldown 분기 추가 + test | S | ⏸️ (시그널 0건 동안 영향 없음) |
| 4 | XS   | .env vs backtest report 권고 차이 docs 명시 | XS | ✅ |
| 5 | Med  | cooldown_s (86400) vs hold_days (5d) 정합 | M | ⏸️ (별도 설계 fire) |
| 6 | S    | 09:50 첫 EOD-grade scan 결과 별도 진단 | S | ✅ ops note |

## 항목별 근거 (architect)

### 1. 활성화 안전성 — CONCERN
- 08:50 KST 장 개장 전 진행 → **timing 안전**
- `kill → sleep 3 → kill -9` → **SIGKILL 까지 단 3초 grace** 위험
  - paper 모드라 실손실 0이지만, **실거래 동일 절차 절대 금지**
  - 권고: SIGTERM 30s grace 후 SIGKILL fallback

### 2. ratio=2.0 정합성 — PASS
- backtest 권고 = ratio=3.0 (avg_net=+2.16%, winrate=56.7%)
- 운영 채택 = ratio=2.0 (avg_net=+1.30%, winrate=47%)
- 의도: 1주일 paper 검증 후 3.0 승격 (보수적 hedge, 합리)
- **gap**: README/CLAUDE.md 어디에도 차이 명시 없음 → 운영자 혼동 가능

### 3. ratio lowering 무의미 결론 — PASS (timing 한계)
- 진단 데이터 부합 (ratio 1.0 까지 0/40)
- **단 09:01 진단은 장 시작 1분 후 partial bar**
  - vol_ratio_low 3건 (005930, 000660, 032830) 은 vol 미반영으로 평가 자체가 왜곡
  - 09:50 또는 EOD 재진단 시 결론 재확인 권고

### 4. 회귀 안전성 — PASS (partial)
- 8 case drift test 핵심 reject bucket 모두 cover
- **gap**: cooldown 분기 (`_cooldown_active`) 는 diagnose 미반영
  - 시그널 0건 동안 영향 없음 (`_last_emitted_at` 비어있음)
  - 첫 시그널 발생 후부터는 false-positive 위험 (24h cooldown)

### 5. 빠뜨린 critical 위험 (3건)
- **091990 universe 잔존** (High): backtest sim 결과 자체에 영향 가능
- **vol_bonus 음수 클립**: `_qualifies` 가 사전 차단 + `max(0, ...)` 방어 → 코드 안전, 의도 주석 부재
- **cooldown=86400s vs hold=5d 비대칭**: backtest 가정과 다름. 동일 종목 2일 연속 돌파 시 중복 진입 가능 (MAX_POSITIONS=4 로 완화)

## 즉시 적용 (이 fire)

### #2: 실거래 운영 절차 — SIGTERM grace 30s
```bash
# OK for paper:
kill "$PID"; sleep 3; kill -9 "$PID"
# REQUIRED for live (다음 fire 적용):
kill -TERM "$PID"; sleep 30; kill -KILL "$PID" 2>/dev/null
```
실거래 진입 전 `deploy/restart_paper.sh` 또는 systemd unit 의 `TimeoutStopSec=30s` 설정 필수.

### #4: ratio 차이 docs 명시
- 백테스트 권고 = `ratio=3.0` (avg_net=+2.16%/trade, trades=30/180일)
- 본 활성화 채택 = `ratio=2.0` (avg_net=+1.30%/trade, trades=68/180일)
- **이유**: 첫 paper 운영 보수적 출발, 1주일 검증 후 3.0 승격 검토
- 기록 위치: `.env` 인라인 주석 + `docs/2026-05-11-ks-v86-paper-activation.md`

### #6: 09:50 EOD-grade 재진단 (수동 트리거 권고)
```bash
# 09:50 KST 부근 daemon scan 완료 후 실행
.venv/bin/python scripts/v86_diagnose.py 2>&1 | tail -50
# 09:01 진단 (partial bar) vs 09:50 (1h 누적) 비교
diff <(awk -F, 'NR>1{print $1","$9}' data/runtime/v86_diagnose_20260511_090158.csv) \
     <(awk -F, 'NR>1{print $1","$9}' data/runtime/v86_diagnose_<NEW_TS>.csv)
```

## 보류 (별도 fire)

### #1: 091990 universe 교체 + backtest 재실행
- 차단 사유: 본 fire 제약 "v86 backtest 코드 노터치"
- 별도 fire 권고: `fire-w12-ks-v86-universe-cleanup`
- 영향: backtest avg_net=+1.30% 자체가 091990 처리 방식에 의존 가능

### #3: diagnose cooldown 분기
- 차단 사유: 현재 시그널 0건, false-positive 미발생
- 첫 시그널 발생 후 follow-up fire 권고

### #5: cooldown_s vs hold_days 정합
- 차단 사유: 설계 결정 (capital allocation, 중복 진입 정책)
- 별도 fire 권고: `fire-w12-ks-v86-cooldown-design`

## References
- `src/kindshot/feeds/volume_breakout_feed.py:176-208` — `_qualifies + _confidence`
- `src/kindshot/config.py:331-339` — v86 config keys (ratio=2.0 default)
- `scripts/v86_diagnose.py:26-54` — evaluate (cooldown 미반영)
- `scripts/v86_paper_watch.py:32-37` — v86 signal SQL
- `tests/test_v86_diagnose_consistency.py:58-84` — drift 회귀
- `data/runtime/v86_diagnose_20260511_090158.csv:34` — 091990 no_data
- `docs/2026-05-11-ks-v86-backtest-report.md:53-63` — ratio=3.0 권고
- `docs/2026-05-11-ks-v86-paper-activation.md:46-54` — kill+restart 시퀀스 (3s grace)
