# 2026-03-27 v66 Trading Analysis Auto-Tune

## Goal

Turn the existing backtest log analysis into a full-history diagnostic surface that can rank where v66 entries and exits are working, then emit recommended parameter settings for the next bounded strategy hypothesis.

## Why now

- v66 deployment is complete.
- The next high-value step is evidence collection and parameter discovery, not immediate live behavior change.
- Current `scripts/backtest_analysis.py` is too shallow for disciplined tuning because it lacks news-type cohorts, matrix outputs, and recommendation logic.

## Scope

This slice is tooling-only:

1. Extend `scripts/backtest_analysis.py`
2. Add a standalone auto-tuning script
3. Add tests
4. Produce local analysis artifacts and a run summary

## Current constraints

- Keep production and deployment behavior unchanged.
- Use only local historical evidence in the workspace.
- Do not overwrite unrelated user edits already present in the worktree.
- Prefer deterministic, reviewable heuristics over opaque optimization.

## Design

### Full-history reconstruction

Use the local `logs/kindshot_*.jsonl` set as the canonical source for executed BUY history. Continue reconstructing from event/decision/price-snapshot rows only.

### Matrix outputs

Add machine-readable cohort summaries for:

- ticker
- exact hour
- coarse hour bucket (`pre_open`, `open`, `mid_morning`, `midday`, `afternoon`, `late`)
- news type
- selected intersections that matter for decisions (`news_type x hour_bucket`)

Each cohort should expose at least:

- count
- win rate
- average PnL
- total PnL
- profit factor when calculable

### Entry recommendations

Rank entry cohorts that outperform the global baseline while meeting a minimum sample threshold. Use a conservative score so tiny cohorts do not dominate.

### Exit recommendations

Simulate bounded parameter candidates over reconstructed trade paths. Recommended exit settings should come from the best composite score, not from a single headline metric.

### Auto-tune output

The new script should read the analysis JSON and produce:

- recommended env-style parameter values
- supporting evidence
- JSON output suitable for future automation

No config file mutation is allowed in this run.

## Validation

- compile scripts and sources
- targeted tests for the analysis and tuning scripts
- execute the enriched analysis on the local history
- execute the tuning script on the generated analysis artifact

## Rollback

- revert the new analysis/tuning logic and tests
- discard generated recommendation artifacts
