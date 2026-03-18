# Strategy Performance Analysis
Generated: 2026-03-18 15:30

## Iteration 1-5 개선 결과 (2026-03-18)

### UNKNOWN 비율 개선
- 이전: 1410/2002 = 70.4%
- 이후: 584/2002 = 29.2%
- **-41.2%p 개선**

### 키워드 변경 요약
- IGNORE +20개: 의무공시, IR, 건조한 실적 숫자, 배당 행정, 인사/조직
- POS_STRONG +20개: 역대 최대, 사상 최대, 주주환원, 글로벌 확장, 임상 2상, 주식소각
- POS_WEAK +22개: 저평가, 실적 성장, 바이오시밀러, 목표주가, 주식분할, 해외매출
- NEG_STRONG +16개: false positive 방지 (특허 만료, 합작 해지, 투자유치 실패, 인수 무산, 흑자전환 실패 등)
- NEG_WEAK +4개: 자사주 처분, 매출 미달, 영업익 감소

### False Positive 방지
- 6건의 위험한 false positive 제거 (특허 만료→BUY 등)
- 추가 4건 edge case 방지 (인수 무산, 흑자전환 실패, 규모 축소, 특허 침해)
- 정상 POS 분류 영향 없음 검증 완료

### 인프라 수정
- LLM timeout 역전 수정: SDK 10s→15s
- LLM 캐시 크기 제한: 1024 엔트리
- LLM 프롬프트 개선: 예시 포함, 형식 명확화
- size_hint 파서 관용화: 누락 시 confidence 기반 기본값
- ADV 임계값: 50억→30억 (기회 확대)
- 테스트 수정: 329 pass

### 포착 증가
- POS_STRONG 추가: 45건
- POS_WEAK 추가: 55건
- IGNORE 필터링: 717건 노이즈 제거

## 잔여 항목
- UNKNOWN 29.2% → 20% 목표 (남은 127건, 대부분 기사형 뉴스)
- Price snapshot 수집 → 서버 운영 문제, 코드는 정상
- 수익률 측정 → price data 축적 후 분석 가능
- BUY confidence 앵커링 (72/M 반복) → 프롬프트 개선으로 해결 예상
