# 2026-03-29 Y2I Kindshot Feed Contract Alignment

## Goal

Make Kindshot consume the dedicated `y2i` export contract (`kindshot_feed.json`) by default while preserving compatibility with the older `signal_tracker.json` snapshot.

## Hypothesis

If Kindshot's Y2i feed parser switches to the purpose-built `kindshot_feed.json` contract and normalizes the legacy tracker snapshot as a fallback, then the `y2i -> kindshot` path becomes reliable again without breaking existing local setups that still point at the older artifact.

## Evidence

- `y2i` already exports `.omx/state/kindshot_feed.json` through both:
  - `omx-brainstorm export-kindshot-feed`
  - `scripts/run_channel_30d_comparison.py`
- Kindshot still defaults `Y2I_SIGNAL_PATH` to `~/workspace/y2i/.omx/state/signal_tracker.json`.
- The current Kindshot parser expects legacy fields (`signal_score`, `channel_slug`) and therefore ignores the richer contract fields already emitted for Kindshot (`confidence`, `channel`, `channel_weight`, `consensus_signal`, `evidence`).
- Same-day duplicate ticker signals can now arrive from multiple y2i channels, so Kindshot should prefer the strongest candidate rather than whichever row appears first.

## Scope

- Update Kindshot config defaults and Y2i feed parsing only.
- Keep Y2i export shape unchanged.
- Preserve compatibility with legacy tracker snapshots.
- Update tests and operator-facing documentation for the new default path.

## Design

### 1. Default path alignment

- Change Kindshot's default `Y2I_SIGNAL_PATH` to `~/workspace/y2i/.omx/state/kindshot_feed.json`.
- Keep the env override intact so existing deployments can still point elsewhere.

### 2. Dual-contract parser

- Continue reading a JSON object with a top-level `signals` array.
- Normalize both contracts into one internal shape:
  - `ticker`
  - `company_name`
  - `signal_date`
  - `verdict`
  - `channel`
  - normalized score:
    - prefer `signal_score`
    - otherwise derive from `confidence * 100`
  - confidence:
    - prefer `confidence`
    - otherwise derive from `signal_score / 100`
  - optional consensus and channel-weight metadata

### 3. Duplicate handling

- Keep Kindshot's existing runtime-level dedup boundary of one `(ticker, signal_date)` item.
- For duplicates within the same poll, choose the strongest candidate by:
  - `consensus_signal`
  - normalized score
  - confidence
  - channel weight
  - verdict rank
- This keeps Kindshot from emitting multiple same-day entries for one ticker while still preferring the strongest y2i evidence.

### 4. Observability and compatibility

- Keep the emitted `RawDisclosure` title format stable enough for existing logs/tests:
  - `[Y2I:<channel>]`
  - verdict
  - score
- Preserve `y2i://signal/...` links and `dorg="y2i"`.
- Do not change downstream trading, risk, or deploy behavior.

## Validation

1. targeted `pytest -q ../kindshot/tests/test_y2i_feed.py`
2. targeted y2i contract verification for existing export tests
3. `python3 -m compileall ../kindshot/src ../kindshot/tests`
4. broader Kindshot test coverage around feed wiring if needed

## Rollback

- Revert the Kindshot config default, parser normalization, and Y2i feed tests/docs.
- Existing users can still force the legacy path with `Y2I_SIGNAL_PATH`.
- No deploy, secret, or live-trading rollback is required.
