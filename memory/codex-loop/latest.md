Hypothesis: Before implementing historical collection, the design needs an explicit `live / backfill / replay` split with a finalized-day rule so night/weekend backfill does not collide with same-day live news intake.

Changed files:
- `docs/plans/2026-03-13-data-collection-infra.md`
- `memory/codex-loop/latest.md`

Validation:
- Manual review passed for the updated design document structure and the new operational sections covering finalized-day logic, backfill cursoring, and mode separation.

Risk and rollback note:
- Risk is low because this run only changes the design document, but implementation work should treat KIS historical-news support as a hypothesis until verified.
- Roll back by reverting `docs/plans/2026-03-13-data-collection-infra.md` and this summary file if you want to return to the earlier collector draft.
