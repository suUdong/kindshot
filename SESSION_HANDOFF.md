# Session Handoff — 2026-03-26 (8차, 파이어모드)

## 이번 세션 완료 작업

### 수익성 개선 v4 — 3건

| # | 커밋 | 분류 | 내용 |
|---|------|------|------|
| 1 | `6d6a44c` | feat | SL 완화 -0.7→-1.5% + 소형주 ADV 보너스 +3 |
| 2 | `99fefa0` | feat | 장전 이벤트 재평가 메커니즘 — iv_ratio=0 DUPLICATE 소실 복구 |

### 변경 상세

**1. SL 완화 (V자 반등 대응)**
- `paper_stop_loss_pct` 기본값: -0.7% → **-1.5%**
- 동적 SL (`get_dynamic_stop_loss_pct`):
  - conf >= 85: -1.05% → **-2.55%** (고확신 촉매 큰 폭 허용)
  - conf 80-84: -0.7% → **-1.5%** (기본)
  - conf 75-79: -0.5% → **-1.0%** (저확신 상대적 타이트)
- 근거: t+5m -1~-3% → close +2~+10% V자 반등 패턴 다수

**2. 소형주 confidence 보너스**
- `apply_adv_confidence_adjustment`: ADV 500~2000억 구간 → **confidence +3**
- 초소형주(<500억): 기존대로 조정 없음
- 대형주(2000~5000억): 기존 -5, cap 72 유지

**3. 장전 이벤트 재평가**
- 문제: 장전 iv_ratio=0 → INTRADAY_VALUE_TOO_THIN → DUPLICATE 영구 소실 (3/20 12건 miss)
- 해결:
  - `EventRegistry.unmark()`: seen_ids 제거 + `_reeval_ids`로 related_title_dup 우회
  - `process_registered_event`: 장전(09시 이전) INTRADAY_VALUE_TOO_THIN → pending 큐 추가
  - `pipeline_loop`: 09:01 이후 pending 이벤트 재주입
- 테스트 5건 추가 (registry 3 + pipeline 2)

## 현재 상태
- **브랜치:** main
- **테스트:** 633 passed, 0 failed
- **마지막 커밋:** `99fefa0` feat: 장전 이벤트 재평가 메커니즘
- **서버:** active (running), 16:10 재시작 완료

## 잔여 기술 부채

### P1 — 긴급
1. **Anthropic 크레딧 충전 또는 제거** — fallback 불가 상태 방치 위험

### P1 — 전략 검증
2. ~~SL -0.7% 재검토~~ → **완료** (v4에서 -1.5%로 완화)
3. ~~장전 이벤트 재평가~~ → **완료** (pending 큐 + 09:01 재주입)
4. **2주 룰 freeze + 데이터 수집** — 새 전략(SL -1.5%, ADV 보너스)으로 100건+ 거래 필요

### P2 — 기능/전략
5. **Paper → 소액 Live 전환 준비** — KIS live API 키 설정, 주문 실행 모듈
6. **텔레그램 알림 품질 개선** — BUY 시그널에 "왜 BUY인지" 이유 추가
7. ~~소형주 집중~~ → **완료** (ADV 500~2000억 +3 보너스)
8. **확률 기반 진입** — 뉴스 후 2~5분 관찰 후 진입 (구현 복잡)

### P3 — 제품 방향
9. **외부 사용자 확보** — 텔레그램 채널에 지인 1~3명 초대
10. **정보 서비스 pivot 검토** — AI 공시 분석 알림 서비스
