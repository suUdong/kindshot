# 2026-03-27 Collector Status Manifest Awareness

## Goal

`kindshot collect status` already reports backlog counts and the latest blocked dates, but an operator still has to open raw manifest files to understand whether a blocked day is partial, what the collector believed was missing, and which manifest is the authoritative replay contract. This slice makes the status path itself manifest-aware so the blocker context is visible directly in stdout/logs and `--json` artifacts.

## Current Gap

- `collect backfill` writes per-day manifests and a manifest index that replay code can already use.
- `collect status` currently reads only `collector_state.json` and `collection_log.jsonl`.
- For blocked days, the status detail payload shows `skip_reason` or `error`, but not the manifest path or manifest-side status metadata.
- That means replay-facing storage contracts exist, but the main operator status surface does not expose them.

## Hypothesis

If `collect status` enriches blocked backlog detail rows with manifest path and manifest metadata, operators can decide whether to retry, inspect, or ignore a blocked day without leaving the status surface, and downstream polling jobs can consume one JSON artifact instead of combining log and manifest files themselves.

## Scope

- Extend `src/kindshot/collector.py` status-read helpers only.
- Keep `collect backfill` write semantics unchanged.
- Keep output changes additive:
  - existing summary fields stay stable
  - existing detail fields stay stable
  - new manifest-related fields are added to detail rows
- Update collector status tests and run-summary documents.

## Design

### Manifest-Aware Status Detail

For each limited backlog detail row (`partial` and `error`):

- consult `data/collector/manifests/index.json` first
- fall back to `data/collector/manifests/YYYYMMDD.json` when needed
- attach:
  - `manifest_path`
  - `manifest_exists`
  - `manifest_status`
  - `manifest_has_partial_data`
  - `manifest_status_reason`
  - `manifest_generated_at`

This keeps the read contract aligned with replay: status should point to the same manifest entry that replay would later consume.

### Human Log Output

`kindshot collect status` log lines for blocked rows should include:

- existing log-record reason/error
- manifest-side status or reason when available
- manifest path

That keeps the human-readable surface useful even when the JSON artifact is not being persisted.

## Non-Goals

- Do not change manifest write format in this slice.
- Do not change backfill retry/cutoff policy.
- Do not add Telegram delivery or scheduler behavior here.
- Do not broaden replay-day health semantics.

## Validation

- unit test: status detail helper reads manifest context
- unit test: `_build_status_report()` includes manifest fields in backlog details
- unit test: `print_collection_status_json()` emits manifest-aware backlog details
- unit test: `log_collection_status()` includes manifest context in human log output
- targeted collector test module
- full repository test suite

## Rollback

- Revert `src/kindshot/collector.py`, `tests/test_collector.py`, this design note, and the memory summary updates.
- No data migration is required because the change only enriches status read paths.
