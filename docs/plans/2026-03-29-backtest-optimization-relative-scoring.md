# 2026-03-29 Backtest Optimization Relative Scoring

## Goal

Make the local exit-parameter optimizer rank candidates by actual improvement versus the active baseline instead of falsely pinning the current baseline at the top when every candidate still has negative raw returns.

## Hypothesis

If `scripts/backtest_analysis.py` scores exit candidates on a fully relative basis against the current baseline for total PnL, average PnL, profit factor, and drawdown, then the optimizer will stop falsely preferring the baseline in negative-return scenarios and the refreshed local optimization run will produce a trustworthy recommendation even when the final answer is "no parameter delta".

## Evidence

- Fresh `2026-03-29` local rerun on `logs/kindshot_*.jsonl` reconstructed `14` executed BUY trades across `20260319`, `20260320`, and `20260327`.
- The active env-backed runtime settings currently replay as:
  - `tp=2.0`
  - `sl=-1.5`
  - `trail_activation=0.5`
  - `trail=(0.5, 0.8, 1.0)`
  - `max_hold=30`
  - `t5m_loss_exit=True`
- That baseline produced:
  - total PnL `-2.086%`
  - profit factor `0.21`
  - MDD `-2.421%`
- Multiple non-baseline exit candidates tie the baseline on this window, so the final refreshed recommendation is legitimately "keep the current env-backed exit parameters".
- Independent of the current tie, the old ranking logic still had a correctness flaw:
  - baseline score was hard-coded to `0`
  - non-baseline candidates used a mix of relative and absolute terms, so a less-bad candidate in an all-negative set could still rank below baseline for the wrong reason
- The new regression test reproduces that failure mode directly and proves the relative scoring fix.

## Scope

- Update only the local backtest-analysis ranking logic.
- Keep runtime trading behavior unchanged.
- Recompute operator-facing analysis artifacts after the scoring fix.

## Design

### 1. Relative exit score

- Keep the baseline candidate at `score=0`.
- Compute every non-baseline exit score as a delta from the baseline across:
  - total PnL
  - average PnL
  - win rate
  - profit factor component
  - drawdown magnitude
- Preserve the existing bounded profit-factor component so infinite PF does not explode the ranking.

### 2. Regression coverage

- Add a focused unit test that reproduces the failure mode:
  - baseline and candidate are both negative
  - candidate is less negative with better PF and lower drawdown
  - optimized ranking must place the candidate above baseline

### 3. Artifact refresh

- Re-run:
  - `scripts/backtest_analysis.py`
  - `scripts/auto_tune_strategy.py`
  - `scripts/monthly_full_strategy_backtest.py`
- Use the refreshed outputs as the final parameter-optimization evidence for this run.
- If the refreshed outputs tie on score and performance, keep the current recommended env block unchanged.

## Validation

1. targeted pytest for backtest analysis, auto-tune, and monthly backtest reporting
2. `python3 -m compileall src scripts tests`
3. rerun the three local analysis commands
4. full `pytest -q`

## Rollback

- Revert the scoring change and the regression test.
- Discard the refreshed local analysis artifacts if needed.
- No deploy, secret, or live-trading rollback is required because runtime behavior does not change in this slice.
