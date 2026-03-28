# Kindshot 통합 검증 리포트

> 검증일: 2026-03-29
> 대상: v78 가드레일 완화 + v80 _sell_triggered 수정 + v79 Y2iFeed 연동
> 데이터: 2026-03-18 ~ 2026-03-27 (8거래일, 로그 기반)

---

## 1. 가드레일 시뮬레이션 결과

### 1.1 요약

| 지표 | v77 (이전) | v78 (현재) | 변화 |
|------|-----------|-----------|------|
| 가드레일 도달 이벤트 | 229건 | 229건 | - |
| 통과 | 132건 (57.6%) | 136건 (59.4%) | **+4건 (+1.7%p)** |
| 차단 | 97건 | 93건 | -4건 |
| 전체 POS 이벤트 | 1,339건 | 1,339건 | - |

v78 가드레일 완화로 **4건의 추가 시그널이 통과**되었으며, 통과율이 57.6% → 59.4%로 개선.

### 1.2 차단 사유별 비교

| 차단 사유 | v77 (이전) | v78 (현재) | 변화 |
|-----------|-----------|-----------|------|
| ADV_TOO_LOW | 31 | 31 | 0 |
| PRE_OPENING_LOW_CONFIDENCE | 21 | 24 | +3 |
| LOW_CONFIDENCE | **20** | **7** | **-13** |
| MARKET_CLOSE_CUTOFF | 12 | 18 | +6 |
| OPENING_LOW_CONFIDENCE | 4 | 4 | 0 |
| ORDERBOOK_TOP_LEVEL_LIQUIDITY | 3 | 4 | +1 |
| CHASE_BUY_BLOCKED | 2 | 1 | -1 |
| INTRADAY_VALUE_TOO_THIN | **2** | **0** | **-2** |
| CLOSING_LOW_CONFIDENCE | 1 | 2 | +1 |
| AFTERNOON_LOW_CONFIDENCE | 0 | 1 | +1 |
| PRIOR_VOLUME_TOO_THIN | 1 | 1 | 0 |

**핵심 변화:**
- `LOW_CONFIDENCE`: 20→7건 (-13건) — `min_buy_confidence` 78→73 효과가 가장 큼
- `INTRADAY_VALUE_TOO_THIN`: 2→0건 — `min_intraday_value_vs_adv20d` 15%→5% 효과
- `MARKET_CLOSE_CUTOFF`: 12→18건 (+6건) — 15:00→15:15 완화에도 불구하고 시간대별 confidence 문턱 변화로 일부 이벤트가 confidence 체크를 먼저 통과한 후 다른 사유로 차단
- `PRE_OPENING_LOW_CONFIDENCE`: 21→24건 (+3건) — 같은 이유 (confidence 완화로 기존 LOW_CONFIDENCE 대신 시간대별 체크에서 차단)

### 1.3 새로 통과된 이벤트 (4건)

| 날짜 | 종목 | 헤드라인 | Conf | 이전 차단 사유 |
|------|------|---------|------|--------------|
| 03-20 | 358570 지아이이노베이션 | J&J 자회사 얀센과 전립선암 병용 임상 착수 | 75 | LOW_CONFIDENCE |
| 03-20 | 259960 크래프톤 | 장병규 의장, 100억원대 자사주 매입 | 76 | INTRADAY_VALUE_TOO_THIN |
| 03-20 | 373220 LG엔솔 | ESS 기회…경쟁사와 특허 갈등 없을 것 | 78 | INTRADAY_VALUE_TOO_THIN |
| 03-27 | 272210 한화시스템 | 단일판매·공급계약체결 | 74 | LOW_CONFIDENCE |

### 1.4 날짜별 통과율

| 날짜 | v77 통과 | v78 통과 | 차이 |
|------|---------|---------|------|
| 03-18 | 0/4 (0%) | 0/4 (0%) | 0 |
| 03-19 | 24/34 (70.6%) | 24/34 (70.6%) | 0 |
| 03-20 | 17/41 (41.5%) | 20/41 (48.8%) | **+3** |
| 03-23 | 21/22 (95.5%) | 21/22 (95.5%) | 0 |
| 03-24 | 6/6 (100%) | 6/6 (100%) | 0 |
| 03-26 | 41/66 (62.1%) | 41/66 (62.1%) | 0 |
| 03-27 | 23/56 (41.1%) | 24/56 (42.9%) | **+1** |

### 1.5 v78 임계값 변경 요약

| 가드레일 | v77 (이전) | v78 (현재) | 근거 |
|----------|-----------|-----------|------|
| min_buy_confidence | 78 | **73** | POS_STRONG conf 73-77 시그널 허용 |
| min_intraday_value_vs_adv20d | 15% | **5%** | 공시 직후 거래대금 자연적 낮음 |
| chase_buy_pct | 3% | **5%** | 뉴스 반영 3-5% 상승은 정상 |
| fast_profile 마감 | 14:00 | **14:30** | 20분 보유 윈도우 확보 |
| no_buy_after | 15:00 | **15:15** | 장마감 15:30, 15분 버퍼 |
| 호가 유동성 기준 | 100% | **50%** | 2호가 이하 체결 허용 |

---

## 2. _sell_triggered 수정 검증 (v80)

### 2.1 버그 설명

v80 이전: `_sell_triggered.add(event_id)`가 trade_close 콜백 **호출 전**에 실행됨
→ 콜백이 예외를 던지면 event_id가 영구적으로 `_sell_triggered`에 남아
→ close 스냅샷에서 재시도 불가 → **포지션 미청산 위험**

### 2.2 수정 내용

`_sell_triggered.add()`를 콜백 성공(`callback_ok = True`) **후**에만 호출하도록 변경.

```python
# price.py:461-463 (v80 수정 후)
if position_closed and callback_ok:
    self._remaining_position_pct[snap.event_id] = 0.0
    self._sell_triggered.add(snap.event_id)
```

### 2.3 테스트 결과

| 테스트 케이스 | 결과 |
|-------------|------|
| 콜백 성공 → _sell_triggered에 추가됨 | PASS |
| 콜백 실패(예외) → _sell_triggered에 추가되지 않음 | PASS |
| 콜백 실패 후 재시도 → 정상 청산 | PASS |
| 부분 청산 → _sell_triggered에 추가되지 않음 | PASS |

```
tests/test_sell_triggered_fix.py: 4 passed in 0.25s
```

---

## 3. Y2iFeed / Alpha-Scanner 연동 현황

### 3.1 Y2iFeed (v79)

- **기능**: y2i 유튜브 인사이트 시그널을 kindshot 파이프라인에 주입
- **경로**: `~/workspace/y2i/.omx/state/signal_tracker.json` 폴링
- **필터**: score ≥ 55, verdict ≥ WATCH, lookback 3일, ticker+date 기준 중복 제거
- **활성화**: `Y2I_FEED_ENABLED=true` (기본 비활성)
- **파이프라인 통합**: `MultiFeed`에 `Y2iFeed` 추가, `dorg="y2i"`로 태깅
- **테스트**: `tests/test_y2i_feed.py` 전체 통과

### 3.2 Alpha-Scanner 연동

- **기능**: alpha-scanner 섹터 모멘텀을 confidence 조정에 반영
- **적용**: `confidence_adjustments` 파이프라인의 sector momentum 단계
- **효과**: 상승 섹터 종목에 confidence 가산, 하락 섹터 종목에 감산

---

## 4. 결론 및 권장사항

### 검증 결과 요약

| 항목 | 상태 | 비고 |
|------|------|------|
| v78 가드레일 완화 | **정상** | 통과율 +1.7%p, 4건 추가 통과 |
| v80 _sell_triggered 수정 | **정상** | 4/4 테스트 통과, 재시도 정상 동작 |
| v79 Y2iFeed 연동 | **정상** | 테스트 통과, 기본 비활성 상태 |

### 관찰 사항

1. **LOW_CONFIDENCE 차단 감소가 가장 큰 효과** (20→7건, -65%). `min_buy_confidence` 73이 핵심 변화.
2. **ADV_TOO_LOW가 최대 차단 사유** (31건, 33%). 소형주 필터가 여전히 가장 큰 장벽.
3. **PRE_OPENING_LOW_CONFIDENCE** (24건, 26%)도 주요 차단. 장전/개장 직후 높은 문턱(88) 유지 중.
4. 새로 통과된 4건 중 크래프톤 자사주 매입(conf 76), 한화시스템 공급계약(conf 74) 등 품질 양호한 시그널 포함.

### 시뮬레이션 스크립트

```bash
python3 scripts/guardrail_sim.py --output reports/guardrail_sim.json --days 10
```
