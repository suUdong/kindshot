# Session Handoff — 2026-03-26 (14차, 파이어모드)

## 이번 세션 완료 작업 (v27~v34, 8개 개선)

| # | 커밋 | 내용 | 영향 |
|---|------|------|------|
| 1 | `217ea32` | **v27: graceful shutdown 수정** | 재시작 90s→5s |
| 2 | `5993b5c` | v28: 키워드 갭 해소 (유통계약, 국책과제 등) | false negative 감소 |
| 3 | `b9964c2` | v29: false negative 보강 (자사주 추가 매입, 임상3상 진입) | false negative 감소 |
| 4 | `15bfc10` | **v30: 외부 LLM 에러 핸들러 rule_fallback 재시도** ★★ | 하루 57~69건 복구 |
| 5 | `0713e21` | **v31: KIS dual-server 가격조회** ★★★ | 실시간 시세 정상화 |
| 6 | `4faf919` | v32: dual-server 지수 데이터 + 헬퍼 리팩토링 | KOSPI/KOSDAQ 실시간 |
| 7 | `a323856` | v33: 뉴스 피드도 dual-server 적용 | 공시 감지 지연 방지 |
| 8 | `d7df989` | v34: CEO/거버넌스 노이즈 IGNORE 강화 | false positive 감소 |

### 핵심 발견 3가지

**1. Graceful shutdown 실패 (v27)**
- `_unknown_review_loop` 무한 블로킹 → SIGTERM 90초 timeout → SIGKILL
- Fix: `asyncio.wait_for(2s)` + sentinel 순서 + drain timeout

**2. LLM 에러로 POS 이벤트 대량 SKIP (v30)**
- 3/24: 57건, 3/25: 69건 → LLM_ERROR로 바로 SKIP (fallback 미시도)
- Fix: POS 버킷은 execute_bucket_path 재호출로 rule_fallback 활용

**3. VTS 서버 종가 반환 (v31-v33) ★ 치명적**
- 모의투자 VTS 서버가 실시간 시세 미제공 → 전일 종가만 반환
- 전체 가격 스냅샷(4689건) 수익률 측정 불가
- Fix: `KIS_REAL_APP_KEY/SECRET` dual-server → 가격/지수/뉴스 모두 실전 서버
- **⚠️ 서버 .env에 실전 API 키 추가 필요:**
  ```
  KIS_REAL_APP_KEY=실전앱키
  KIS_REAL_APP_SECRET=실전앱시크릿
  ```

### 서버 로그 분석 요약

| 날짜 | final_BUY | 주요 이슈 |
|------|-----------|----------|
| 3/20 | 7 | 수익률 ≈0% (VTS 종가 문제) |
| 3/23 | 0 | MARKET_BREADTH_RISK_OFF |
| 3/24 | 0 | LLM_ERROR 57건 (v30으로 해결) |
| 3/25 | 0 | LLM_ERROR 69건 + ADV 70건 |
| 3/26 | 0 | LOW_CONFIDENCE (KOSPI -3.22%) |

## 현재 상태
- **브랜치:** main
- **테스트:** 653 passed, 0 failed
- **서버:** active (running), v34 최종 배포
- **systemd:** TimeoutStopSec=15 추가됨

## 다음 세션 우선순위

### P0 — 즉시
1. **서버 .env에 KIS 실전 API 키 추가** — v31 dual-server 활성화 필수
2. **3/27 장중 모니터링** — v30 LLM fallback + 실시간 시세 확인

### P1 — 긴급
3. **2주 룰 freeze + 데이터 수집** — 실시간 시세 기반 100건+ 거래 필요
4. **Anthropic 크레딧 충전 또는 제거** — fallback 불가 (NVIDIA primary)

### P2 — 기능
5. **Volume spike gate** — 거래량 급증 확인 후 BUY
6. **Paper → 소액 Live 전환** — 실전 API 키로 전환
7. **확률 기반 진입** — 뉴스 후 2~5분 관찰

### P3 — 제품
8. **텔레그램 채널 지인 초대** — 외부 검증
9. **AI 공시 분석 서비스 pivot** — 수익모델 다변화
