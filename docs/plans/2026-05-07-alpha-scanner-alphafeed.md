# Alpha-scanner AlphaFeed Plan

## Hypothesis

Kindshot's 14 recent paper entries are unprofitable because the current entry stream is dominated by short-horizon news signals. Adding an alpha-scanner high-conviction source gives Kindshot a slower, score-backed candidate stream that can be evaluated independently as `SignalSource.ALPHA`.

## Scope

- Consume alpha-scanner through the existing `ALPHA_SCANNER_API_BASE_URL`.
- Add an AlphaFeed endpoint on alpha-scanner that returns recent `BUY`/`STRONG_BUY` rows that passed the signal quality filter.
- Add a Kindshot strategy that polls the feed and emits `TradeSignal` records with `source=ALPHA`.
- Keep the strategy disabled unless explicitly enabled by config.
- Add a simulation/report path in alpha-scanner so the extracted candidates can be compared with a supplied Kindshot baseline return.

## Rollout

1. Deploy alpha-scanner API with `/kindshot/alpha-feed`.
2. Set `ALPHA_FEED_ENABLED=true` and `ALPHA_SCANNER_API_BASE_URL=http://...` in paper only.
3. Watch `STRATEGY_SIGNAL` records where source is `ALPHA`.
4. Promote only after paper evidence beats the configured baseline with enough sample size.

## Observability

- AlphaFeed payload includes `generated_at`, `as_of`, `lookback_days`, `signals`, and `paper_trade`.
- Each Kindshot alpha signal includes `event_id=alpha_<signal_id>`, metadata with score, delta, regime, feed age, and source signal id.
- Existing strategy runtime logs `STRATEGY_SIGNAL` decisions and event records.

## Validation

- Alpha-scanner unit tests cover filter behavior, payload shape, HTTP route, CLI output, and paper comparison.
- Kindshot unit tests cover feed fetch fallback, strategy signal conversion, and registry wiring.
- Local DB freshness is checked explicitly; stale DB is a validation gap, not hidden.

## Rollback

- Set `ALPHA_FEED_ENABLED=false` to disable the strategy.
- Existing news, DART, technical, and sector snapshot paths remain unchanged.
