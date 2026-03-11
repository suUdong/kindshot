Hypothesis: Medical-device and drug approval headlines using `식약처 허가` can still fall through unless the regulator phrasing is explicit; classifying `식약처 허가` as `POS_STRONG` will recover those catalyst events.

Changed files:
- `src/kindshot/bucket.py`
- `tests/test_bucket.py`

Validation:
- `python.exe -m compileall src tests` passed
- `pytest -q tests/test_bucket.py -p no:cacheprovider` passed (`13 passed`)
- Full `pytest -q` was not rerun in this loop because the environment previously showed temp/cleanup permission failures unrelated to the touched code.

Rollback note:
- Remove `식약처 허가` from `POS_STRONG_KEYWORDS` to restore the previous stricter bucket behavior.
