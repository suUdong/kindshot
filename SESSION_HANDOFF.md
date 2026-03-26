# Session Handoff — 2026-03-26 (14차, 파이어모드)

## 이번 세션 완료 작업 (v27~v38, 12개 개선)

| # | 커밋 | 내용 | 영향 |
|---|------|------|------|
| 1 | `217ea32` | **v27: graceful shutdown 수정** | 재시작 90s→5s |
| 2 | `5993b5c` | v28: 키워드 갭 해소 (유통계약, 국책과제 등) | false negative 감소 |
| 3 | `b9964c2` | v29: false negative 보강 (자사주 추가 매입, 임상3상 진입) | false negative 감소 |
| 4 | `15bfc10` | **v30: 외부 LLM 에러 핸들러 rule_fallback 재시도** ★★ | 하루 57~69건 복구 |
| 5 | `0713e21` | **v31: KIS dual-server 가격조회** ★★★ | 실시간 시세 정상화 |
| 6 | `4faf919` | v32: dual-server 지수+헬퍼 리팩토링 | KOSPI/KOSDAQ 실시간 |
| 7 | `a323856` | v33: 뉴스 피드도 dual-server | 공시 감지 지연 방지 |
| 8 | `d7df989` | v34: CEO/거버넌스 노이즈 IGNORE 강화 | false positive 감소 |
| 9 | `a6f2cbe` | **v35: POS_STRONG ADV 3억 완화** | 소형주 촉매 복구 |
| 10 | `da23242` | **v36: confidence 감점 상한 버그 수정** ★ | -20→-10 정상화 |
| 11 | `7f54575` | **v37: 거래량 확인 confidence 조정** | volume spike gate |
| 12 | `e600ac7` | v38: NEG/IGNORE 키워드 갭 해소 | 분류 정확도 향상 |
| - | `dbe2c8c` | fix: t+10m 스냅샷 누락 수정 | 성과 분석 데이터 갭 |

### 카테고리별 요약

| 카테고리 | 버전 | 핵심 효과 |
|---------|------|----------|
| 인프라 | v27 | graceful shutdown + TimeoutStopSec |
| **실시간 시세** | **v31-33** | **VTS 종가 → 실전 서버 (가격+지수+뉴스)** |
| **LLM fallback** | **v30** | **하루 60건+ POS 이벤트 복구** |
| **conf 파이프라인** | **v36-37** | **감점 상한 정상화 + 거래량 확인** |
| 키워드 | v28-29,34,38 | POS/NEG/IGNORE 갭 해소 25+ 패턴 |
| ADV 필터 | v35 | POS_STRONG 소형주 3억+ 허용 |

### 핵심 발견 3가지

**1. VTS 서버 종가 반환 (v31-33) ★★★**
- 전체 가격 스냅샷(4689건) 수익률 = 0% (전일 종가)
- Fix: dual-server (실전키 필요)

**2. LLM 에러 대량 SKIP (v30) ★★**
- 하루 57~69건 POS 이벤트 → fallback 없이 SKIP
- Fix: POS 버킷 재호출로 rule_fallback 활용

**3. Confidence 감점 -20 버그 (v36) ★**
- article(-10) + pipeline(-10) = 총 -20 가능
- Fix: llm_original_conf 캡처 위치 수정

## 현재 상태
- **브랜치:** main, 16커밋 pushed
- **테스트:** 653 passed
- **서버:** active (running), v38 최종 배포
- **systemd:** TimeoutStopSec=15

## 다음 세션 우선순위

### P0 — 즉시
1. **서버 .env에 KIS 실전 API 키 추가** → dual-server 활성화
   ```
   KIS_REAL_APP_KEY=실전앱키
   KIS_REAL_APP_SECRET=실전앱시크릿
   ```
2. **3/27 장중 모니터링** — v30 fallback + v37 volume + 시세 확인

### P1 — 긴급
3. **2주 룰 freeze** — 실시간 시세 기반 100건+ 거래 데이터 수집
4. **KIND 공시 vs 뉴스 구분** — `dorg`/`news_ofer_entp_code` 기반 필터

### P2 — 기능
5. **Paper → 소액 Live 전환**
6. **확률 기반 진입** — 뉴스 후 2~5분 관찰
7. **Anthropic 크레딧** — NVIDIA primary, 차선

### P3 — 제품
8. **텔레그램 채널 지인 초대**
9. **AI 공시 분석 서비스 pivot**
