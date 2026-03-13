# Kindshot Codex Session State

## Current Session

- Branch: `codex/roadmap-loop-foundation`
- Phase: `Post-roadmap return refinement`
- Focus: Get the historical collection design correct before starting collector implementation.
- Active hypothesis: Before implementing historical collection, the design needs an explicit `live / backfill / replay` split with a finalized-day rule so night/weekend backfill does not collide with same-day live news intake.

## Environment

- Host: local workspace
- Runtime target: Python `3.11+`
- Current local venv: `.venv` uses Python `3.11.8`
- Validation status: `$env:UV_CACHE_DIR='C:\workspace\study\kindshot\.uv-cache'; uv run --python 3.11 --extra dev pytest -q` passed (`167 passed, 3 skipped`)
- Tooling note: the sandbox user cannot use the default `uv` cache under `C:\Users\jooz1\AppData\Local\uv\cache`; use workspace-local `.uv-cache` for validation commands.

## Last Completed Step

- Updated the historical data collection design doc with explicit `live`, `backfill`, and `replay` modes.
- Added finalized-day and backfill-cursor rules so the collector only walks backward over dates that are safe to treat as closed.
- Clarified that microstructure data remains a live-sink concern even if price/news backfill succeeds.

## Next Intended Step

- At the next implementation step, start with a small feasibility probe for KIS historical-news/date support and collector state handling before building the full backfill module.

## Notes

- Keep branch-based work as the default.
- Keep automation limited to code changes, validation, summaries, and PR preparation.
- Keep merge and deployment as manual actions.
