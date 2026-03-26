# Session Handoff — 2026-03-26 (11차, 파이어모드)

## 이번 세션 완료 작업 (v7~v12)

| # | 커밋 | 분류 | 내용 |
|---|------|------|------|
| 1 | `74987cc` | feat | v7: hold profile 연동 TP/SL + stale position exit |
| 2 | `18e72e8` | feat | v8: detection delay 감점 + BUY 알림 TP/SL 표시 |
| 3 | `441f290` | feat | v9: 시장 반응 확인 confidence 보정 (ret_today) |
| 4 | `1098423` | feat | v10: confidence 조정 파이프라인 통합 + 감점 상한 -10 |
| 5 | `cbaec36` | feat | v11: 고확신 키워드 확대 + hold profile 보강 |
| 6 | `4c72553` | fix | v12: 버킷 키워드 갭 해소 — HIGH_CONVICTION↔POS_STRONG 동기화 |

### 변경 상세

**v7: Hold Profile 연동 TP/SL + Stale Exit**
- EOD hold(자사주소각): TP×1.5, SL×1.3 → 장기 트렌드 수익 극대화
- 수주/공급계약(hold≤15): TP×0.7 → 빠른 반전 전 익절
- Stale position: 5분+ 경과 ±0.2% 미만 → 모멘텀 소멸 탈출

**v8: Detection Delay 감점**
- 30~60s: -1, 60~120s: -2, 120s+: -3
- 텔레그램 BUY 알림에 TP/SL% 표시

**v9: 시장 반응 확인**
- ret_today 0.3~1.5%: 시장 반응 확인 → +2
- ret_today < -0.5%: 시장 불신 → -2

**v10: 감점 상한**
- 4개 조정(ADV, price reaction, delay, market) 단일 블록
- 총 감점 상한 -10: 과다 감점 방지

**v11: 키워드 확대**
- 최초수주/첫매출/양산개시, 역대최대수주, 정부조달 추가
- hold_profile: 정부조달 15분, 첫수주/양산 20분

**v12: 버킷 키워드 갭 해소 (버그 수정)**
- FDA승인/허가, 임상2상/3상 성공 등 21개 키워드가 POS_STRONG 누락
- UNKNOWN 분류 → rule_fallback 미도달 → 시그널 유실 해결

## 현재 상태
- **브랜치:** main
- **테스트:** 645 passed, 0 failed
- **서버:** active (running), 최종 배포 완료

## 잔여 기술 부채

### P1 — 긴급
1. **Anthropic 크레딧 충전 또는 제거** — fallback 불가 상태
2. **3/27 장중 모니터링** — v7~v12 효과 검증
3. **2주 룰 freeze + 데이터 수집** — 100건+ 거래 필요

### P2 — 기능/전략
4. **Paper → 소액 Live 전환 준비** — KIS live API 키 설정
5. **확률 기반 진입** — 뉴스 후 2~5분 관찰 후 진입
6. **Volume 확인 게이트** — 진입 시 거래량 급증 확인

### P3 — 제품 방향
7. **외부 사용자 확보** — 텔레그램 채널 초대
8. **정보 서비스 pivot 검토** — AI 공시 분석 알림 서비스
