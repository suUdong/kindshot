# Session Handoff — 2026-03-26 (12차, 파이어모드)

## 이번 세션 완료 작업 (v7~v16, 10개 개선)

| # | 커밋 | 분류 | 내용 |
|---|------|------|------|
| 1 | `74987cc` | feat | v7: hold profile 연동 TP/SL + stale position exit |
| 2 | `18e72e8` | feat | v8: detection delay 감점 + BUY 알림 TP/SL 표시 |
| 3 | `441f290` | feat | v9: 시장 반응 확인 confidence 보정 (ret_today) |
| 4 | `1098423` | feat | v10: confidence 조정 파이프라인 통합 + 감점 상한 -10 |
| 5 | `cbaec36` | feat | v11: 고확신 키워드 확대 + hold profile 보강 |
| 6 | `4c72553` | fix | v12: 버킷 키워드 갭 해소 — HIGH_CONVICTION↔POS_STRONG 동기화 |
| 7 | `7a1ec3e` | feat | v13: UNKNOWN 헤드라인 분석 기반 키워드 추가 |
| 8 | `5640a43` | feat | v14: trailing stop hold profile 차등화 |
| 9 | `2155ad4` | feat | v15: LLM 프롬프트 최적화 (ret_3d<-5% 완화, hold profile 안내) |
| 10 | `55abbf5` | feat | v16: rule_fallback 기사/미확정 필터 강화 |

### 핵심 개선 요약

**출구 전략 (v7, v14)**
- TP/SL/trailing stop 모두 hold profile(촉매 유형) 반영
- EOD hold(자사주소각): TP×1.5, SL×1.3, trailing×1.5
- 수주/공급계약(hold≤15): TP×0.7, trailing×0.7
- Stale exit: 5분+ 무반응(±0.2%) 자동 탈출

**Confidence 파이프라인 (v8-v10)**
- 4단계 조정: ADV → price reaction → delay → market
- 총 감점 상한 -10 (과다 감점 방지)
- Price reaction: ret_today 0.3~1.5% → +2 (시장 확인)
- Delay: 30s+ 감지 지연 → -1~-3

**키워드/버킷 (v11-v13)**
- 27개 POS_STRONG 키워드 추가
- HIGH_CONVICTION↔POS_STRONG 갭 해소 (FDA승인 등 시그널 유실 수정)
- 서버 UNKNOWN 분석 기반 추가 (품목허가, 사용승인 등)

**프롬프트/필터 (v15-v16)**
- ret_3d<-5% 강한 촉매 BUY 허용 (코드 v6와 일치)
- 미확정 표현(추진/검토/계획) rule_fallback 필터

## 현재 상태
- **브랜치:** main
- **테스트:** 645 passed, 0 failed (634 → 645, +11 신규)
- **서버:** active (running), v16 배포 완료

## 다음 세션 우선순위

### P1 — 검증
1. **3/27 장중 모니터링** — v7~v16 효과 검증 (BUY 시그널 발생 여부)
2. **Anthropic 크레딧 충전 또는 제거** — fallback 불가 상태
3. **2주 룰 freeze + 데이터 수집** — 100건+ 거래 필요

### P2 — 기능
4. **Paper → 소액 Live 전환 준비** — KIS live API 키 설정
5. **확률 기반 진입** — 뉴스 후 2~5분 관찰 후 진입
6. **Volume spike 확인 게이트** — 진입 시 거래량 급증 확인

### P3 — 제품
7. **외부 사용자 확보** — 텔레그램 채널 초대
8. **정보 서비스 pivot 검토** — AI 공시 분석 알림 서비스
