# Red Team Audit — 2026-03-09

## Round 1: 자체 분석

### CRITICAL
1. **가드레일 미구현 (fail-open)** — guardrails.py 4~8번 항목 전부 미구현. live 전환 시 LLM BUY=무제한 매수.
2. **spread_bps 항상 None** — kis_client.py:105-106 ask/bid 하드코딩 0. 호가 API 미호출.
3. **gap 필드 항상 None** — context_card.py:128 선언만, 계산 없음.
4. **Live=Paper 동일** — main.py:324-334 실제 주문 실행 코드 없음.

### HIGH
5. **LLM timeout 역전** — SDK 3s > wait_for 2s. 사실상 2초 제한.
6. **캐시키 MD5 8자 (32bit)** — decision.py:130 해시 충돌 위험.
7. **_t0_prices 무한 성장** — price.py:70 정리 없음.
8. **pykrx 캐시 race condition** — context_card.py:20 글로벌 OrderedDict, to_thread와 동시 접근.
9. **EventRegistry 히스토리 무한 성장** — 하루 동안 prune 없음. 정정 매칭 O(N).

### MEDIUM
10. **정정 이벤트 중복 매수** — 정정도 POS_STRONG이면 새 BUY 결정.
11. **빈 ticker 처리 없음** — feed.py:41-44 매칭 실패 시 "" 반환, 파이프라인 진행.
12. **_is_market_hours 주말/공휴일 무시** — feed.py:75-79 시간만 체크.
13. **버킷 키워드 false positive** — 단순 find(), 형태소/경계 체크 없음.
14. **ADV 0과 None 미구분** — main.py:158 `or 0`.

### LOW
15. **LLM 프롬프트/응답 원문 미기록** — reason 100자만 저장.
16. **run_id 충돌 가능** — uuid[:6] = 24비트.
17. **JSONL 멀티프로세스 비안전** — asyncio.Lock만.
18. **Shutdown 시 pending snapshot 손실** — persist/재스케줄 없음.

---

## Round 1.5: KIS API 인사이트 반영 (2026-03-09)
- 호가 API (inquire-asking-price-exp-ccn, tr_id: FHKST01010200) → 실제 spread_bps 계산
- 시가 (stck_oprc) → gap 계산
- Rate limiting: paper 0.5s, real 0.05s

## Round 2: 추가 검토 (외부 제보)

### P0
1. **시장 방어막 fail-open** — market.py 시장지표 수집 실패/초기화 지연 시 is_halted=False로 거래 경로 열림. (market.py:47, :79, main.py:232, :499)
2. **replay decision event_id 조인 깨짐** — DecisionEngine 기본 event_id="" → replay에서 미설정 상태로 기록. (decision.py:231, replay.py:146, :161)

### P1
3. **실매매 필수 guardrail 미구현** — 일손실, 재매수, 섹터 집중, 포지션 상한, 관리종목. (guardrails.py:5, :37)
4. **재시작 시 중복 체결 리스크** — event dedup 메모리 전용, 비영속. (event_registry.py:61, main.py:461)
5. **LLM 결정 재현성 낮음** — temperature 미고정, replay가 현재 설정으로 재평가. (decision.py:202, replay.py:105, :149)
6. **시간대 표기 오류** — detected_at=UTC인데 프롬프트에 KST 표기. (feed.py:128, main.py:246, decision.py:56)

### P2
7. **로깅 실패 시 fail-stop 없음** — I/O 오류에도 계속 진행, 감사추적 누락 위험. (logger.py:31, main.py:375)

## Round 3: 최종 검토 (2026-03-09)
- **가드레일 4~8 구현완료** — daily_loss_limit, same_stock_rebuy, max_positions, restricted_stock
- **dedup 상태 영속화** — state_dir에 JSONL로 seen_ids 저장, 재시작 시 로드
- **replay 로그 파일 분리** — file_prefix="replay" → replay_YYYYMMDD.jsonl
- **스냅샷 스케줄러 fail-stop** — LogWriteError 시 scheduler.stop()
- **KIS 비활성 시 docstring/로그 정합** — fail-close 동작 명시
- **replay detected_at KST 변환** — astimezone(KST) 적용

## Round 4: 가드레일 상태 관리 (2026-03-09)
- **record_pnl 배선** — close 스냅샷 시 P&L 콜백으로 guardrail_state.record_pnl() 호출
- **일일 리셋** — market_loop에서 KST 날짜 변경 감지 시 자동 reset_daily()
- **GuardrailState 영속화** — state_dir/guardrail_state.json에 저장, 재시작 시 당일분만 로드
- **섹터 집중 체크 구현** — max_sector_positions(기본2) 초과 시 SECTOR_CONCENTRATION 차단
- **record_sell 추가** — 포지션 정리 시 position_count/sector_positions 차감
- **스냅샷 fail-stop → 런타임 전파** — stop_event 공유로 LogWriteError 시 전체 종료
- **order_size/max_sector_positions** config 추가
