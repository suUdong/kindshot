# 2026-03-27 Collect Backfill Reporting

## Goal

`kindshot collect backfill` currently leaves durable manifests and collection logs, but the command itself does not emit a single machine-readable run artifact. This slice adds a backfill report path so operators and automation can consume one JSON report for a run instead of reconstructing intent and outcome from stdout plus multiple files.

## Current Gap

- `collect status --json` already exposes manifest-aware backlog state.
- `collect backfill` only logs a one-line completion summary.
- Wrapper scripts can send a human-readable Telegram message, but there is no CLI-native JSON artifact for the run itself.

## Hypothesis

If `collect backfill` can emit a machine-readable report that bundles the requested range, run outcome, touched-date details, and post-run collector status, operators can review one artifact per run and automation can reuse the result without scraping logs.

## Scope

- Extend the `collect backfill` CLI surface only.
- Keep collector write semantics unchanged.
- Keep output additive:
  - existing backfill behavior remains the same without `--json`
  - `--json` prints a machine-readable report to stdout
  - `--json --output PATH` also writes the same payload to disk
- Reuse the manifest-aware collector status report as the post-run state block.

## Design

### CLI Shape

Add:

```bash
kindshot collect backfill --cursor 20260310 --json
kindshot collect backfill --from 20260301 --to 20260313 --json --output data/collector/backfill/latest.json
```

Rules:

- `--output PATH` requires `--json`
- range semantics for `--cursor`, `--from`, and `--to` stay unchanged

### Report Contract

The report should include:

- `source`
- `generated_at`
- `request`:
  - `cursor`
  - `from_date`
  - `to_date`
- `result`:
  - requested range/finalized date
  - processed/complete/partial/skipped counts
  - date lists
- `rows`:
  - one row per touched date from this run
  - include latest collector record fields plus manifest metadata when available
- `collector_status`:
  - the same payload shape returned by `load_collection_status_report()`
- optional `error` block when the run raises after writing an `error` record

### Shared Helper

- Add a collector helper that builds the backfill report from:
  - request arguments
  - optional `BackfillResult`
  - collector state/log summary
  - manifest-aware detail rows
- Add a printer/writer helper mirroring `collect status --json`.

## Non-Goals

- Do not change `collection_log.jsonl` schema.
- Do not add a new default config path for reports in this slice.
- Do not change Telegram formatting in this slice.
- Do not change collector retry/cutoff policy.

## Validation

- unit test: parse backfill args with `--json` and `--output`
- unit test: backfill report helper includes touched-date rows and collector status
- unit test: backfill JSON printer emits stdout and writes file
- unit test: `collect_main()` dispatches backfill JSON mode
- targeted collector tests
- full repository test suite

## Rollback

- Revert:
  - `src/kindshot/collector.py`
  - updated collector tests
  - this design note
  - run-summary updates
- No data migration is required because the change only adds a new read/report surface.
