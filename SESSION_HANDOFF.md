# Session Handoff — 2026-03-26 (14차, 파이어모드)

## 이번 세션 완료 작업 (v27~v36, 10개 개선 + 2 fix)

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
| 9 | `a6f2cbe` | **v35: POS_STRONG ADV 3억 완화** | 소형주 촉매 복구 |
| 10 | `da23242` | **v36: confidence 감점 상한 버그 수정** ★ | -20→-10 상한 정상화 |
| 11 | `dbe2c8c` | fix: t+10m 스냅샷 누락 수정 | 성과 분석 데이터 갭 |

### 핵심 발견 + 해결

**1. VTS 서버 종가 반환 (v31-v33) ★★★ 치명적**
- 모의투자 VTS 서버 = 실시간 시세 미제공 → 전일 종가만 반환
- 전체 가격 스냅샷(4689건) 수익률 측정 완전 불가
- Fix: `KIS_REAL_APP_KEY/SECRET` dual-server (가격+지수+뉴스)
- **⚠️ 서버 .env에 실전 API 키 추가 필요**

**2. LLM 에러로 POS 이벤트 대량 SKIP (v30) ★★**
- 3/24: 57건, 3/25: 69건 → fallback 없이 바로 SKIP
- Fix: POS 버킷은 execute_bucket_path 재호출

**3. Confidence 감점 -20 버그 (v36) ★**
- article(-10) + pipeline(-10) = -20 가능. 상한 -10 무력화
- Fix: `llm_original_conf`를 모든 감점 전에 캡처

**4. POS_STRONG ADV 임계값 무효 (v35)**
- `min(5억, 10억)=5억` → `pos_strong_adv_threshold` 기본값 무의미
- Fix: 기본값 10억→3억. `min(5억, 3억)=3억`으로 소형주 허용

### 서버 상태 점검 결과

| 항목 | 결과 | 비고 |
|------|------|------|
| SIGTERM 처리 | ❌→✅ | 90초 timeout 해소 |
| 가격 스냅샷 | ❌→⚠️ | 코드 준비, 실전 키 필요 |
| LLM fallback | ❌→✅ | 60건+/일 복구 |
| confidence cap | ❌→✅ | -20→-10 정상화 |
| ADV 필터 | 과도→✅ | POS_STRONG 3억 허용 |
| t+10m 스냅샷 | ❌→✅ | 누락 수정 |

## 현재 상태
- **브랜치:** main
- **테스트:** 653 passed, 0 failed
- **서버:** active (running), v36 최종 배포
- **systemd:** TimeoutStopSec=15

## 다음 세션 우선순위

### P0 — 즉시
1. **서버 .env에 KIS 실전 API 키 추가** — dual-server 활성화 필수
   ```
   KIS_REAL_APP_KEY=실전앱키
   KIS_REAL_APP_SECRET=실전앱시크릿
   ```
2. **3/27 장중 모니터링** — 모든 개선 효과 검증

### P1 — 긴급
3. **2주 룰 freeze + 데이터 수집** — 실시간 시세 기반 100건+ 거래
4. **Anthropic 크레딧** — NVIDIA primary, fallback 불가

### P2 — 기능
5. **Volume spike gate** — 거래량 급증 확인
6. **Paper → 소액 Live** — 실전 API 키로 전환
7. **확률 기반 진입** — 뉴스 후 2~5분 관찰

### P3 — 제품
8. **텔레그램 채널 지인 초대**
9. **AI 공시 분석 서비스 pivot**
