# Session Handoff — 2026-03-26 (13차, 파이어모드)

## 이번 세션 완료 작업 (v7~v22, 16개 개선)

| # | 커밋 | 내용 |
|---|------|------|
| 1 | `74987cc` | v7: hold profile 연동 TP/SL + stale position exit |
| 2 | `18e72e8` | v8: detection delay 감점 + BUY 알림 TP/SL 표시 |
| 3 | `441f290` | v9: 시장 반응 확인 confidence 보정 |
| 4 | `1098423` | v10: confidence 파이프라인 통합 + 감점 상한 -10 |
| 5 | `cbaec36` | v11: 고확신 키워드 확대 + hold profile 보강 |
| 6 | `4c72553` | v12: 버킷 키워드 갭 해소 (21개 POS_STRONG 추가) |
| 7 | `7a1ec3e` | v13: UNKNOWN 헤드라인 분석 기반 키워드 추가 |
| 8 | `5640a43` | v14: trailing stop hold profile 차등화 |
| 9 | `2155ad4` | v15: LLM 프롬프트 최적화 |
| 10 | `55abbf5` | v16: rule_fallback 기사/미확정 필터 강화 |
| 11 | `138aea9` | **v17: LLM-fallback 하이브리드 오버라이드** ★ 핵심 |
| 12 | `9f63cdb` | v18: "목표" 키워드 정확 매칭 |
| 13 | `be96b55` | v19: "예정" 미확정 표현 추가 |
| 14 | `dad4e0f` | v20: contract preflight 대형계약 하락장 바이패스 |
| 15 | `11a8cf7` | v21: contract preflight 기사/미확정 필터 확대 |
| 16 | `1e11054` | **v22: post-LLM 기사 패턴 감점 -10** ★ |

### 핵심 발견 + 해결

**서버 로그 분석 결과:**
- 최근 3일 2544 이벤트, BUY 11건, LOW_CONFIDENCE 11건
- NVIDIA LLM이 고확신 촉매를 체계적으로 과소평가:
  - 자사주 소각(rule_fallback=82) → LLM이 71-73
  - FDA 허가(rule_fallback=82) → LLM이 72
  - 106억 수주 매출 9.33%(rule_fallback=77) → LLM이 69

**v17 하이브리드 해결:** LLM conf<75 + rule_fallback BUY(75+) → rule_fallback 오버라이드
→ 11건 false negative 중 최소 6건 구제 예상

### 개선 카테고리 요약

| 카테고리 | 버전 | 핵심 효과 |
|---------|------|----------|
| 출구 전략 | v7,v14 | 촉매별 TP/SL/trailing 차등화 |
| Confidence | v8-v10 | 4단계 파이프라인 + 감점 상한 |
| 키워드 | v11-v13 | 27+ POS_STRONG 추가, 갭 해소 |
| 프롬프트 | v15 | 하락장 완화, hold profile 안내 |
| 필터 | v16,v18-v19,v21 | 기사/미확정 false positive 방지 |
| **하이브리드** | **v17** | **LLM 과소평가 → rule_fallback 오버라이드** |
| **Post-LLM** | **v22** | **기사/미확정 패턴 감점 -10 (false positive 방지)** |
| Preflight | v20-v21 | 대형계약 하락장 통과, 미확정 차단 |

## 현재 상태
- **브랜치:** main
- **테스트:** 651 passed, 0 failed (634 → 651, +17 신규 테스트)
- **서버:** active (running), v21 최종 배포

## 다음 세션 우선순위

### P0 — 내일 검증
1. **3/27 장중 모니터링** — v7~v21 효과 검증 (특히 v17 하이브리드)
2. **하이브리드 오버라이드 로그 확인** — "LLM-fallback hybrid" 로그 발생 여부

### P1 — 긴급
3. **Anthropic 크레딧 충전 또는 제거** — fallback 불가
4. **2주 룰 freeze + 데이터 수집** — 100건+ 거래 필요

### P2 — 기능
5. **Paper → 소액 Live 전환** — KIS live API 키
6. **확률 기반 진입** — 뉴스 후 2~5분 관찰
7. **Volume spike 게이트** — 거래량 급증 확인

### P3 — 제품
8. **텔레그램 채널 지인 초대** — 외부 검증
9. **AI 공시 분석 서비스 pivot** — 수익모델 다변화
