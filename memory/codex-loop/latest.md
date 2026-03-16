Hypothesis: replay 운영 자동화에 쓰려면 queue, run, summary를 한 번에 묶는 higher-level ops cycle과 batch failure policy가 필요하다.

Changed files:
- `docs/plans/2026-03-13-data-collection-infra.md`
- `src/kindshot/__main__.py`
- `src/kindshot/config.py`
- `src/kindshot/main.py`
- `src/kindshot/replay.py`
- `tests/test_replay.py`
- `memory/codex-loop/latest.md`
- `memory/codex-loop/session.md`

Validation:
- `python3 -m compileall src/kindshot tests` passed.
- `python3 -m pytest tests/test_replay.py -q` could not run because the current environment has no `pytest`.
- Workspace `.venv` does not provide a runnable pytest entrypoint in this environment.

- Risk and rollback note:
- Risk is moderate because this expands replay ops CLI into higher-level orchestration with failure-policy handling, but it remains additive and leaves existing day-level replay/status/summary paths unchanged.
- Roll back by reverting `docs/plans/2026-03-13-data-collection-infra.md`, `src/kindshot/__main__.py`, `src/kindshot/config.py`, `src/kindshot/main.py`, `src/kindshot/replay.py`, `tests/test_replay.py`, `memory/codex-loop/session.md`, and `memory/codex-loop/latest.md`.
