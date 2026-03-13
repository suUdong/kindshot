# Daily Review: 2026-03-13

## 요약
- 총 이벤트: 572건 (어제 1,141건 대비 절반 — 배포 10:43 이후부터만)
- 버킷: POS_STRONG=87 | POS_WEAK=42 | NEG_STRONG=38 | NEG_WEAK=3 | IGNORE=156 | UNKNOWN=246
- LLM 판단: **0건** (BUY=0, SKIP=0)
- 버킷 보강 첫날: IGNORE 156건 정상 작동, UNKNOWN 873→246 (72% 감소)

## 버킷 보강 효과 ✅
| 지표 | 3/12 (보강 전) | 3/13 (보강 후) |
|------|---------------|---------------|
| UNKNOWN | 873 (76%) | 246 (43%) |
| IGNORE | 0 | 156 |
| 유효 버킷(POS+NEG) | 268 | 170 |

## 심각한 문제

### P0: LLM 판단 0건 — 33건 도달했는데 decision 없음
- POS_STRONG 87건 중 33건이 quant 통과 (skip_stage=None, skip_reason=None)
- 그런데 decision 레코드가 **0건** — LLM 호출 자체가 안 됐거나 로깅 누락
- 어제(3/12)는 6건 정상 작동 → **오늘 배포 코드에서 뭔가 깨졌을 가능성**
- LLM_PARSE 에러 2건만 기록됨
- **확인 필요**: `sudo journalctl -u kindshot --since "2026-03-13 09:00" | grep -i "error\|traceback\|exception" | tail -20`

### P0: spread 데이터 전부 None
- quant fail 28건 중 대부분 spread=None
- SPREAD_DATA_MISSING: 12건, SPREAD_TOO_WIDE: 10건
- KIS 호가 API 응답 자체가 빈 값 반환 중?
- **확인 필요**: polling_trace에서 호가 조회 에러 확인

### P1: "규제" 키워드 false positive (13건/38건 = 34%)
- 셀트리온 "바이오시밀러 규제 완화 수혜" × 11건 → 전부 NEG_STRONG (실제 호재)
- KB금융 "금융 규제공백 위협" × 1건
- FDA 규제 완화 뉴스가 계속 반복 수신
- **조치**: "규제" 단독 키워드 제거 → "규제 위반", "규제 제재", "규제 리스크"로 정밀화

### P1: "소송" 키워드 false positive (5건)
- 삼성전자 "퇴직금 소송" × 4건 → 삼성전자에겐 노이즈급
- 세아제강 "소송등의판결" → 공시 제목 형식
- **조치**: "소송 제기", "소송 패소", "소송 피소"만 NEG_STRONG, "소송 승소"는 POS_WEAK

### P2: 동일 이벤트 중복
- 크래프톤-한화에어로 합작: **8건** (KIS/KIND 양쪽 + 언론 다수)
- 셀트리온 규제 완화: **11건**
- 더코디 전환사채: 2건
- 현재 dedup = event_id(UID) 기반 → 같은 사건 다른 기사는 못 걸러냄

### P2: close 스냅샷 N/A
- NEG_STRONG 38건 중 close 데이터 있는 건 10건뿐
- 장 후반(14~16시) 이벤트는 close 전에 스냅샷 수집 안 됨?

## quant check 통과율
| Skip Reason | 건수 |
|------------|------|
| (통과) | 33 |
| ADV_TOO_LOW | 28 |
| SPREAD_DATA_MISSING | 12 |
| SPREAD_TOO_WIDE | 10 |
| LLM_PARSE | 2 |
| ORDERBOOK_TOP_LEVEL_LIQUIDITY | 1 |
| INTRADAY_VALUE_TOO_THIN | 1 |

## LLM 도달했지만 decision 없는 주요 종목
- 삼성바이오로직스(207940): 수주 2,796억 (매출대비 6.15%)
- 크래프톤(259960): 한화에어로와 피지컬 AI 합작법인
- 한화에어로스페이스(012450): 크래프톤과 합작
- 테스(095610): 485억 반도체 장비 공급계약
- 세미파이브(490470): 180억 AI NPU 수주
- 젬백스(082270): 영업이익 흑자 전환

## 액션 아이템
- [ ] **P0**: LLM 호출 실패 원인 파악 (journalctl 확인)
- [ ] **P0**: spread=None 원인 파악 (KIS 호가 API)
- [ ] **P1**: "규제" 키워드 정밀화 (코덱스)
- [ ] **P1**: "소송" 키워드 정밀화 (코덱스)
- [ ] **P2**: ticker+시간 윈도우 기반 dedup 검토
- [ ] **P2**: close 스냅샷 수집 타이밍 확인
