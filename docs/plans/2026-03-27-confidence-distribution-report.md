# 2026-03-27 Confidence Distribution Report

## Goal

Add a standalone analysis script that makes confidence-distribution changes easy to inspect across one or more Kindshot JSONL trading logs.

## Problem

The NVIDIA day1 investigation exposed a strong pathology:

- structured LLM decisions were all `SKIP`
- every LLM confidence was `50`

That pattern matters operationally because it suggests the model is not acting like a useful ranking signal. After the NVIDIA LLM upgrade, the team needs a reusable way to answer:

- Did confidence spread out?
- Did the modal exact score stop collapsing to one value?
- Are distributions different by `decision_source`?
- Are BUY and SKIP confidences separating in a healthier way?

Right now that still requires ad hoc parsing.

## Scope

Implement a bounded standalone report:

1. Add `scripts/confidence_report.py`
2. Support:
   - `--date YYYYMMDD` repeated
   - `--log-file /path/to/log.jsonl` repeated
3. For each input log, report:
   - total decisions
   - `decision_source` split
   - exact confidence frequency top values
   - confidence bands (`<50`, `50-59`, `60-69`, `70-79`, `80-89`, `90+`)
   - action split (`BUY`, `SKIP`) by band
   - mode confidence and mode share
   - a simple collapse flag when one exact confidence dominates the cohort
4. Add an overall comparison section across the selected logs
5. Add explicit before/after delta and change verdicts for the `LLM` cohort
5. Add focused tests

## Non-Goals

- No deployment changes
- No visualization library or plots
- No strategy changes
- No provider-identity inference beyond what the log can support

## Design

### Entry point

Examples:

- `python3 scripts/confidence_report.py --log-file /tmp/kindshot-nvidia-day1/kindshot_20260326.jsonl`
- `python3 scripts/confidence_report.py --date 20260326 --date 20260327`

### Grouping model

Each input log is a cohort.

Within each cohort, compute:

- all decisions
- `LLM`-only decisions
- optional per-source breakdown

### Output sections

1. `Cohort`
   - path/date
   - line count / decision count
2. `Source Split`
   - counts by `decision_source`
3. `Confidence Exact Values`
   - top exact confidence values and shares
4. `Confidence Bands`
   - band counts overall
   - band counts by action
5. `Collapse Check`
   - mode confidence
   - mode share
   - flag such as:
     - `collapsed` if mode share >= 80%
     - `clustered` if mode share >= 60%
     - `spread` otherwise
6. `Comparison`
   - side-by-side summary across cohorts
   - when 2+ cohorts are present, emit a delta summary:
     - previous `LLM` mode confidence -> current
     - previous `LLM` mode share -> current
     - previous collapse flag -> current
     - change verdict: `improved`, `unchanged`, `regressed`, or `insufficient-data`

### Key implementation choice

Keep it standalone under `scripts/`, but reuse the same log-parsing style as `scripts/trading_log_report.py`.

### Why exact-value mode share matters

The NVIDIA day1 issue is not just "low confidence". It is "confidence collapsed to one exact value". A mode-share indicator detects that directly.

### Why a delta verdict matters

Operators do not just need two rows of numbers. They need a direct answer to "did the post-upgrade distribution stop collapsing?" A simple change verdict keeps the report actionable.

## Validation

- `python3 -m py_compile scripts/confidence_report.py`
- `source .venv/bin/activate && python -m pytest tests/test_confidence_report.py -q`
- run against `/tmp/kindshot-nvidia-day1/kindshot_20260326.jsonl` and confirm the `LLM` cohort reports mode confidence `50` with a dominant share

## Rollback

- Revert `scripts/confidence_report.py`
- Revert `tests/test_confidence_report.py`
- Revert this design doc and handoff updates tied to the slice
