# Session Handoff — 2026-03-26 (14차, 파이어모드)

## 이번 세션 완료 작업 (v27~v43, 17개 개선)

| # | 커밋 | 내용 | 영향 |
|---|------|------|------|
| 1 | `217ea32` | **v27: graceful shutdown** | 재시작 90s→5s |
| 2 | `5993b5c` | v28: 키워드 갭 (유통계약, 국책과제) | false negative ↓ |
| 3 | `b9964c2` | v29: 자사주 추가 매입, 임상3상 진입 | false negative ↓ |
| 4 | `15bfc10` | **v30: LLM 에러 rule_fallback** ★★ | 60건+/일 복구 |
| 5 | `0713e21` | **v31: KIS dual-server 가격** ★★★ | 실시간 시세 |
| 6 | `4faf919` | v32: dual-server 지수 + 헬퍼 | KOSPI/KOSDAQ 실시간 |
| 7 | `a323856` | v33: dual-server 뉴스 피드 | 공시 지연 방지 |
| 8 | `d7df989` | v34: CEO/거버넌스 IGNORE | false positive ↓ |
| 9 | `a6f2cbe` | **v35: ADV 3억 완화** | 소형주 촉매 복구 |
| 10 | `da23242` | **v36: confidence -20 버그 수정** ★ | 상한 정상화 |
| 11 | `7f54575` | **v37: volume spike gate** | 거래량 확인 |
| 12 | `e600ac7` | v38: NEG/IGNORE 갭 (소송판결, 생산중단) | 분류 정확도 ↑ |
| 13 | `3e54e44` | v39: 소각 결의/전량 소각 보강 | false negative ↓ |
| 14 | `eeee5df` | v40: 배당 결정/결의 추가 | false negative ↓ |
| 15 | `18d690b` | v41: dorg 필드 이벤트 로그 전파 | 분석 기반 |
| 16 | `7c5b073` | v42: hold_profile 키워드 동기화 | TP/SL 정확도 ↑ |
| 17 | `a628fe8` | **v43: INTRADAY_VALUE 시간 보정** ★ | 장 초반 차단 해소 |
| - | `dbe2c8c` | fix: t+10m 스냅샷 누락 | 데이터 갭 |
| - | `26d9e52` | fix: VTS 경고 로그 | 운영 가시성 |
| - | `fba6e38` | test: volume adjustment 7건 | 테스트 커버리지 |
| - | - | systemd enable | 자동 시작 |

### 핵심 발견 + 해결 (임팩트 순)

**1. VTS 서버 종가 반환 ★★★ (v31-33)**
전체 가격 스냅샷(4689건) = 전일 종가 → 수익률 측정 불가.
Fix: dual-server. **⚠️ .env에 실전 키 추가 필요.**

**2. LLM 에러 대량 SKIP ★★ (v30)**
3/24: 57건, 3/25: 69건 POS 이벤트 → fallback 없이 SKIP.
Fix: POS 버킷 재호출 (rule_fallback 활용).

**3. INTRADAY_VALUE 장 초반 false reject ★ (v43)**
conf=82 삼성중공업/셀트리온이 09:05 감지 → 누적 거래대금 부족 차단.
Fix: 09:00~09:30 임계값 1/5, 09:30~10:00 1/2.

**4. Confidence 감점 -20 버그 ★ (v36)**
article(-10) + pipeline(-10) = 총 -20. 상한 -10 무력화.
Fix: llm_original_conf 캡처 위치 수정.

## 현재 상태
- **브랜치:** main, 25커밋 pushed
- **테스트:** 660 passed (+7 신규)
- **서버:** active (running, enabled), v43 최종 배포
- **systemd:** TimeoutStopSec=15, enabled

## 다음 세션 우선순위

### P0 — 즉시
1. **서버 .env에 KIS 실전 API 키 추가**
   ```
   KIS_REAL_APP_KEY=실전앱키
   KIS_REAL_APP_SECRET=실전앱시크릿
   ```
2. **3/27 장중 모니터링** — 모든 개선 효과 검증

### P1 — 데이터 수집
3. **2주 룰 freeze** — 실시간 시세 기반 100건+ 거래 데이터
4. **dorg 필드 분석** — KIND 공시 vs 뉴스 구분 필터 구현

### P2 — 기능
5. **Paper → 소액 Live 전환**
6. **확률 기반 진입** — 뉴스 후 2~5분 관찰

### P3 — 제품
7. **텔레그램 채널 지인 초대**
8. **AI 공시 분석 서비스 pivot**
