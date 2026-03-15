Hypothesis: collector discovery contract만으로는 replay 연결이 끝나지 않으므로, replay 쪽도 manifest index와 day manifest를 정식 helper로 읽어 collector artifact를 직접 소비할 수 있어야 한다.

Changed files:
- `docs/plans/2026-03-13-data-collection-infra.md`
- `src/kindshot/replay.py`
- `tests/test_replay.py`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- `python3 -m compileall src/kindshot tests` passed.
- `python3 -m pytest tests/test_replay.py -q` could not run because the current environment has no `pytest`.
- Workspace `.venv` does not provide a runnable pytest entrypoint in this environment.

- Risk and rollback note:
- Risk is low because this only adds replay-side helpers for reading existing collector artifacts and does not change trading or collector collection behavior.
- Roll back by reverting `docs/plans/2026-03-13-data-collection-infra.md`, `src/kindshot/replay.py`, `tests/test_replay.py`, `memory/codex-loop/session.md`, and `memory/codex-loop/latest.md`.
