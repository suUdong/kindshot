# KS Side Fixes Status (2026-05-11)

KS 외부 의존 컴포넌트 진단 결과. 외부 process 코드는 미수정 (진단 only).

## (a) alpha-scanner serve-kindshot-api (port 8765)

- 상태: **DOWN** — `curl http://127.0.0.1:8765/health` 연결 거부 (port closed)
- 가동 여부: AlphaFeed (config.alpha_feed_enabled) 활성화 시 base_url 미응답 → AlphaFeed 신호 0건
- 영향: KS AlphaFeed lane 차단. 단, KS 운영에는 critical 하지 않음 (다른 전략 lane 동작).
- 조치 권고: cx-w11-alpha-quality (다른 Claude) 측에서 serve-kindshot-api 데몬 가동 책임. KS 측은
  `ALPHA_FEED_ENABLED=false` 또는 가동 후 활성화 대기.

## (b) y2i kindshot_feed.json stale 검증

- 파일 경로: `~/workspace/y2i/.omx/state/kindshot_feed.json`
- mtime: **Apr 24 22:20** — 오늘(2026-05-11) 기준 17일 stale
- 내용 (현재): `{"generated_at": ..., "signals": [...]}` 구조, **entries 길이 0**
- 즉 마지막 write 시점도 17일 전이고, 그때도 signals 가 비어 있었음.

### y2i scheduler 상태
- PID **141851** alive (uptime 38min, command: `omx-brainstorm.cli run-scheduler --config config.toml`)
- 로그 파일: `~/workspace/y2i/.omx/logs/omx-scheduler-daemon.log` (5.5MB, mtime May 11 08:03)
- heartbeat: `~/workspace/y2i/.omx/logs/scheduler_heartbeat.log` (May 11 08:40)
- 즉 scheduler 자체는 살아 heartbeat 발생 중, 그러나 `kindshot_feed.json` 만 4/24 이후 무업데이트.

### 진단 — root cause 확인됨
y2i scheduler daemon 로그 (`~/workspace/y2i/.omx/logs/omx-scheduler-daemon.log`) 분석:

```
2026-04-19T07:20:03 ~ 2026-04-19T13:40:05 (수십 회 반복)
CRITICAL  Consecutive IP blocks reached the safety limit — shutting down scheduler
          to protect this IP. Clear .omx/state/ip_block_state.json and restart when ready.
2026-05-10T23:03:04
INFO      Running scheduled comparison job          ← 약 21일 만에 첫 정상 동작
```

**원인 사슬:**
1. 4/19 ~ 4/24 사이 y2i scheduler 가 외부 사이트(YouTube) 로부터 IP block 을 반복적으로 받음.
2. `ip_block_state.json` safety circuit 발동 → 매 10분 시도마다 즉시 종료.
3. 마지막 정상 `comparison job` 결과가 4/24 → kindshot_feed.json 그 시점의 signals 로 frozen.
4. PID 141851 은 5/10 23:03 이후 재기동된 새 프로세스. heartbeat 활성. 다만 새 comparison job 이
   아직 충분한 데이터를 모으지 못해 kindshot_feed.json 갱신은 미반영 (또는 ip_block 재발 가능성).

**Bug 여부:** 진정한 의미의 bug 는 아님. 의도된 safety circuit 발동.
- 단, ip_block_state.json 미초기화 시 scheduler 가 무한 재시도 → 무용 동작 (실제로 5/10 까지 재기동 후
  활동 0).
- 책임 영역: **fire-w11-y2i-twitter** (다른 Claude) 가 ip_block_state.json clear / proxy rotation /
  rate-limit 완화 정책을 처리해야 함.

**KS 측 조치:**
- Y2iFeed 가 stale 파일을 읽어도 동일 signal_date 의 entries 는 dedupe 처리 → 안전.
- `Y2I_FEED_ENABLED=false` 로 두는 것도 옵션. 다만 KS 의 dedupe 가 충분하면 stale 허용도 가능.
- KS 코드 수정 불요 (y2i 측 외부 process 정상화 대기).

## 결정 사항
- AlphaFeed/Y2iFeed 모두 현재 무신호 → **v86 VolumeBreakoutFeed** 가 KS lane 보강을 담당.
- AlphaFeed 8765 부재는 cx-w11-alpha-quality, y2i stale 은 fire-w11-y2i-twitter 측 책임 영역.
- KS 측 운영 권고: 환경변수에서 `ALPHA_FEED_ENABLED=false`, `Y2I_FEED_ENABLED=false` 로 두고
  v85.1 (news rule_fallback) + v86 (volume breakout) + Rebalance/DART/Technical 조합으로 가동.
