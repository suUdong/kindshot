# Kindshot Ops Backlog

## Purpose

- Track bounded operational cleanup work that is too specific for the phase roadmap but important for the next validation run.
- Use this file when the user wants Codex to keep executing report/log-driven fixes in sequence.
- Keep items short, reversible, and ordered by priority.

## Execution Rules

- Read this file together with `roadmap.md`, `session.md`, and `latest.md`.
- When this file contains active items, prefer the first active `P0`, then `P1`, then `P2`.
- Execute exactly one item per run.
- After each run, update the touched item's status and note the next active item.
- Do not move roadmap-wide strategy work into this file; keep this for operational defects, observability gaps, and report cleanup.

## Modes

### Default Mode

- Use one backlog item per run.
- Prefer this mode when the user says `진행` without extra instructions.

### Batch Mode

- Use batch mode only when the user explicitly asks for it, for example with `배치 모드`, `전체 수행`, or `ops-backlog 한 번에`.
- Apply batch mode only to this operational backlog. Do not use it for roadmap strategy work.
- The `one strategy hypothesis per run` rule still applies to roadmap/return work; batch mode is only for short-cycle defect cleanup.
- In batch mode, execute consecutive `ACTIVE` items from the top of the queue while all of the following stay true:
  - the items are operationally related or share a common validation surface
  - no blocker or ambiguous evidence forces a stop
  - the diff remains reviewable and reversible
- Stop the batch when any of these happens:
  - a touched item needs user input or external access
  - the next item would require a different subsystem or a materially different hypothesis
  - validation fails and needs investigation before more edits
- Prefer a soft cap of 2-4 items per batch.
- Validate once at the end of the batch with compile, targeted tests for touched areas, and the full test suite when feasible.
- After a batch run, update every touched item status and add a short batch summary to `latest.md` and `session.md`.

## Status Legend

- `ACTIVE`: ready for the next bounded run
- `DONE`: completed and validated
- `BLOCKED`: cannot progress without missing access or new evidence
- `DEFERRED`: intentionally postponed behind higher-value work

## Active Queue

| Priority | Status | Slice | Why It Matters | Evidence |
|---|---|---|---|---|
| P0 | BLOCKED | Verify that decision records reappear after the market-halt and LLM-parse fixes on the next live/paper session | No post-fix live/paper log exists yet; this needs the next session's evidence before it can be closed | Post-fix run output not yet available |
| P1 | DONE | Diagnose `SPREAD_DATA_MISSING` / `SPREAD_TOO_WIDE` path using KIS quote evidence | Off-hours cases are now split into `SPREAD_DATA_MISSING_OFF_HOURS` so premarket/postmarket quote gaps no longer look like generic intraday data failures | `docs/operations/2026-03-13-daily-review.md`, `logs/kindshot_20260313.jsonl`, `logs/polling_trace_20260313.jsonl` |
| P2 | DONE | Evaluate ticker+time-window dedup for same-story multi-headline bursts | Runtime now applies a conservative same-ticker, 10-minute, high-overlap title dedup for ORIGINAL bursts to reduce repeated review/LLM load | `docs/operations/2026-03-13-daily-review.md`, `logs/kindshot_20260313.jsonl` |
| P2 | DONE | Confirm close snapshot collection timing for late-day events | Pending `close` snapshots were being dropped on shutdown; runtime now flushes eligible `close` snapshots during shutdown after the configured close cutoff | `logs/daily_report_20260313.txt`, `src/kindshot/price.py`, `src/kindshot/main.py` |

## Recently Completed

| Priority | Status | Slice | Note |
|---|---|---|---|
| P2 | DONE | Confirm close snapshot collection timing for late-day events | Added shutdown-time `close` snapshot flush after the configured cutoff; non-close horizons still keep normal timing semantics |
| P2 | DONE | Evaluate ticker+time-window dedup for same-story multi-headline bursts | Added conservative same-ticker related-title dedup with a 10-minute window and targeted tests; event-group merging remains deferred |
| P1 | DONE | Diagnose `SPREAD_DATA_MISSING` / `SPREAD_TOO_WIDE` path using KIS quote evidence | 2026-03-13 `SPREAD_DATA_MISSING` events clustered at 06:02~08:44 KST; runtime now keeps fail-close behavior but records off-hours spread absence separately |
| P0 | DONE | Reduce over-aggressive market halt gating and log market-halt skips explicitly | Default halt threshold raised to `-8.0`; market-halt skips now write `skip_stage` / `skip_reason` |
| P0 | DONE | Harden LLM response parsing against wrapper text and fenced JSON | Decision parser now extracts the first valid JSON object |
| P1 | DONE | Remove generic `규제` from `NEG_STRONG` | `규제 완화` headlines no longer route to `NEG_STRONG` by default |
| P1 | DONE | Remove generic `소송` from `NEG_STRONG` | Only explicit adverse lawsuit phrases remain negative by default |
| P1 | DONE | Remove residual general-word `해지` false positives such as `불안해지자` | Feed disclosure-keyword hints now use explicit termination phrases instead of generic `해지` |
