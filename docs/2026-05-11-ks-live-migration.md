# Kindshot Paper → Live (KIS 실전계좌) Migration Plan (2026-05-11)

Operator runbook for promoting `kindshot` from paper mode (`paper_trading=true`)
to live KIS 실전계좌 (`paper_trading=false`) on the Lightsail box
(`kindshot-server`).

This document is the canonical checklist. Anything not on it is **out of
scope** for a live cutover. Pause and ask if reality diverges from these
steps — do **not** improvise.

The v86 paper daemon (PID 503343 as of cutover prep) is paper-only and must
**stay running through the migration** — the broker scaffolding lives in a
new `src/kindshot/broker/` package and does not modify daemon code paths.

---

## 0. Hard preconditions (none are optional)

| Gate | Where enforced | How to verify |
|---|---|---|
| Paper performance proves edge (AVG exit_ret_pct ≥ +0.5%) | manual paper wrap | `cat reports/v86-paper-wrap-latest.md` |
| 2-week+ paper run, win rate ≥ 50%, MDD ≤ 5% | manual paper wrap | same |
| KIS API key pair for 실전계좌 exists, IP-whitelisted | Operator | KIS developer portal |
| Telegram alerts reaching operator | manual probe | test message before cutover |
| Hard caps unchanged in `config.py` (`HARD_MAX_DAILY_LOSS_PCT=0.05`, `HARD_MAX_RISK_PER_TRADE_PCT=0.05`, `SAFE_LIVE_MAX_POSITION_PCT=0.10`) | source review | `git diff src/kindshot/config.py` shows no edits |
| Rsync deploy rehearsal passed on `kindshot-server` | session log | last successful rsync + restart |

Stop here if **any** row is unchecked.

---

## 1. Live-mode safety gates (preflight)

`preflight_check()` in `src/kindshot/config.py` enforces these gates whenever
`paper_trading=False`. The `BrokerFactory.build_broker()` call refuses to
return a `LiveBroker` unless every ERROR row is clear.

### 1a. Explicit env opt-in

```bash
export LIVE_TRADING_ENABLED=true       # also accepts: 1, yes, on
# alias also accepted:
# export KS_LIVE_TRADING_ENABLED=true
```

### 1b. Operator confirmation marker

Refresh once per ≤ 24 h (or whenever the daemon is restarted):

```bash
mkdir -p artifacts
python3 - <<'PY'
import json, datetime as dt, pathlib
pathlib.Path("artifacts/live-confirmed.json").write_text(
    json.dumps({"confirmed_at": dt.datetime.now(dt.UTC).isoformat()})
)
PY
```

Missing file, malformed JSON, future-dated, or stale (> 24 h) timestamp all
hard-fail preflight. Adjust the window via `LIVE_CONFIRMATION_MAX_AGE_HOURS`.

### 1c. Auto-revert daily-loss cap

Set in env (or accept default 0.02 = 2 %):

```bash
export LIVE_AUTO_REVERT_LOSS_PCT=0.02
```

`LiveAutoRevertGuard.check(daily_loss_pct, equity)` writes
`artifacts/live-auto-revert.flag` (JSON: reason / triggered_at /
daily_loss_pct / threshold_pct / equity) when the threshold trips. The
runtime is then expected to halt live trading and revert to paper before the
hard cap (5 %) fires. The flag file is **not** auto-deleted — operator must
inspect + clear before re-enabling live mode.

### 1d. Optional dry-run rehearsal (recommended)

Before the first capital-at-risk start, run the full live code path with
echoed fills:

```bash
export LIVE_DRY_RUN=true
```

`LiveBroker(dry_run=True)` returns `dry-<seq>` fills without calling KIS.
Verify daemon logs show `LIVE DRY-RUN BUY/SELL` lines, that the auto-revert
guard receives equity ticks, and that telegram alerts arrive. Flip
`LIVE_DRY_RUN=false` before going live for real.

### 1e. KIS real-server credentials must be present **in env**

Never commit keys. Export before launching:

```bash
export KIS_REAL_APP_KEY='xxxxxxxx'
export KIS_REAL_APP_SECRET='xxxxxxxx'
export KIS_ACCOUNT_NO='12345678-01'
```

`KIS_APP_KEY` (paper/VTS) is **NOT** acceptable for live cutover — the
preflight check rejects it explicitly.

### 1f. Micro-live order cap

```bash
export MICRO_LIVE_MAX_ORDER_WON=1000000   # 1m won default
```

Preflight blocks live mode when this is ≤ 0. Start small.

---

## 2. Cutover sequence

> Operator executes locally first via SSH to `kindshot-server`, then on the
> server. Tag each step in `SESSION_HANDOFF.md` as you complete it.

1. **Snapshot.** `rsync` pull `data/`, `logs/`, `artifacts/` from the server
   to a timestamped local backup folder. Verify file count + total size.
2. **Stop the paper daemon** (kindshot.service paper mode still uses the
   legacy `OrderExecutor` path):
   ```bash
   ks "sudo systemctl stop kindshot"
   ```
   The v86 watcher (PID 503343 on the operator box) is unaffected — leave
   it running for the cutover wrap-up.
3. **Verify preflight (dry-run):**
   ```bash
   PYTHONPATH=src python3 scripts/preflight_live_check.py
   ```
   Expected: `READY for live cutover.` + exit code 0. Any `[ERROR]` row
   aborts the cutover. Add `--json` to pipe into ops dashboards.
4. **Export env in the systemd drop-in** (NOT in TOML or git):
   ```bash
   ks "sudo systemctl edit kindshot"
   ```
   ```ini
   [Service]
   Environment="LIVE_TRADING_ENABLED=true"
   Environment="PAPER_TRADING=false"
   Environment="LIVE_DRY_RUN=true"
   Environment="LIVE_AUTO_REVERT_LOSS_PCT=0.02"
   Environment="MICRO_LIVE_MAX_ORDER_WON=1000000"
   Environment="KIS_REAL_APP_KEY=..."
   Environment="KIS_REAL_APP_SECRET=..."
   Environment="KIS_ACCOUNT_NO=12345678-01"
   ```
5. **Refresh the live-confirmation marker on the server** (step 1b) — write
   `artifacts/live-confirmed.json` next to the working directory.
6. **Start the daemon in DRY-RUN first and tail logs:**
   ```bash
   ks "sudo systemctl daemon-reload && sudo systemctl start kindshot"
   ks "sudo journalctl -u kindshot -f"
   ```
   Look for `LiveBroker DRY_RUN enabled`. Confirm at least one cycle of
   signal → DRY-RUN BUY → DRY-RUN SELL plus telegram delivery.
7. **Promote to live for real:** flip `LIVE_DRY_RUN=false` in the drop-in,
   refresh the marker, restart:
   ```bash
   ks "sudo systemctl edit kindshot"        # set LIVE_DRY_RUN=false
   ks "sudo systemctl restart kindshot"
   ks "sudo journalctl -u kindshot -f"
   ```
   First live fill confirmation expected within ~1 trading hour.

---

## 3. Rollback

Anything breaks → revert to paper immediately. There is no "fix in place"
move once capital is on the table.

1. ```bash
   ks "sudo systemctl stop kindshot"
   ```
2. Remove live env from the drop-in:
   ```bash
   ks "sudo systemctl edit kindshot"          # comment out LIVE_TRADING_ENABLED + PAPER_TRADING + dry-run
   ks "sudo systemctl daemon-reload"
   ```
3. If `artifacts/live-auto-revert.flag` exists, **read it first**, then move
   it aside for the incident review:
   ```bash
   ks "mv /opt/kindshot/artifacts/live-auto-revert.flag /opt/kindshot/artifacts/live-auto-revert.$(date +%Y%m%d_%H%M%S).flag"
   ```
4. Restart in paper mode:
   ```bash
   ks "sudo systemctl start kindshot"
   ks "sudo systemctl status kindshot --no-pager"
   ```
5. Pull KIS account snapshot, reconcile any open positions manually before
   the next session.

---

## 4. Verification

| Check | How |
|---|---|
| `BrokerFactory.select_broker_kind(config)` returns `live` (or `live-dry`) | `PYTHONPATH=src python3 -c 'from kindshot.broker.factory import select_broker_kind; from kindshot.config import Config; print(select_broker_kind(Config()))'` |
| `preflight_live_check.py` exits 0 with the prod env loaded | `... --env-file /etc/kindshot/live.env` |
| Daemon journal shows `LiveBroker LIVE mode` (or DRY_RUN) | `ks "journalctl -u kindshot -n 100 --no-pager"` |
| Auto-revert flag not present at start | `ks "ls -la /opt/kindshot/artifacts/live-auto-revert.flag"` (expect `No such file`) |
| Tests pass after deploy | `ks "cd /opt/kindshot && source .venv/bin/activate && python -m pytest tests/test_broker_*.py tests/test_preflight_live*.py tests/test_live_auto_revert.py -q"` |

---

## 5. Out-of-scope

- Do **not** edit `src/kindshot/main.py` daemon wiring as part of this
  cutover — broker scaffolding is parallel infrastructure. A separate
  follow-up commit will integrate `BrokerFactory` into the daemon path
  after at least 1 full week of dry-run telemetry.
- Do **not** touch v85.1 / v86 strategy code (`feeds/`, `pipeline.py`,
  `guardrails.py`, `news_strategy.py`). The broker layer routes orders only.
- Do **not** kill the v86 paper watcher (`PID 503343` per the cutover prep
  note). The watcher writes `reports/v86-paper-wrap-latest.md` which feeds
  Section 0 row 1.
- Do **not** commit `.env`, KIS keys, or systemd drop-ins to the repo. Use
  `systemctl edit` which lands under `/etc/systemd/system/kindshot.service.d/`.
