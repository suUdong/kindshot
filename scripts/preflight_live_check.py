#!/usr/bin/env python3
"""Operator helper — runs preflight_check against the current env and prints READY/BLOCKED.

Pre-cutover dry-run. Exit code: 0 when no ERROR rows, 1 otherwise.
WARNINGs do not block. See docs/2026-05-11-ks-live-migration.md §2.

Usage:
    PYTHONPATH=src python3 scripts/preflight_live_check.py
    PYTHONPATH=src python3 scripts/preflight_live_check.py --json
    PYTHONPATH=src python3 scripts/preflight_live_check.py --env-file .env.live
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))


def _load_env_file(path: str) -> dict[str, str]:
    loaded: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"env file not found: {path}")
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        loaded[key.strip()] = value.strip().strip("'\"")
    return loaded


def run(env_file: str | None) -> tuple[int, list[dict[str, str]]]:
    from kindshot.config import Config, preflight_check

    if env_file:
        for k, v in _load_env_file(env_file).items():
            os.environ.setdefault(k, v)

    config = Config()
    issues = preflight_check(config)
    payload = [{"level": lvl, "message": msg} for lvl, msg in issues]
    exit_code = 1 if any(item["level"] == "ERROR" for item in payload) else 0
    return exit_code, payload


def format_human(payload: list[dict[str, str]], *, exit_code: int) -> str:
    lines: list[str] = []
    lines.append("Kindshot live-cutover preflight")
    lines.append("=" * 60)
    if not payload:
        lines.append("OK — no issues reported.")
    else:
        for item in payload:
            tag = "ERROR" if item["level"] == "ERROR" else "WARN "
            lines.append(f"[{tag}] {item['message']}")
    lines.append("=" * 60)
    lines.append("READY for live cutover." if exit_code == 0 else "BLOCKED — fix ERROR rows above.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="KS pre-cutover preflight helper")
    parser.add_argument("--env-file", default=None, help="Optional dotenv to merge into os.environ")
    parser.add_argument("--json", action="store_true", help="Emit JSON only (machine-readable)")
    args = parser.parse_args()

    try:
        exit_code, payload = run(args.env_file)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    if args.json:
        print(json.dumps({"exit_code": exit_code, "issues": payload}, indent=2))
    else:
        print(format_human(payload, exit_code=exit_code))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
