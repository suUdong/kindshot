#!/usr/bin/env python
"""Run codex exec with retry/backoff on quota or rate-limit failures."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import os
import re
import subprocess
import sys
import time

RATE_LIMIT_TOKENS = (
    "rate limit",
    "too many requests",
    "insufficient_quota",
    "quota",
    "http 429",
    " 429 ",
    "retry after",
    "try again in",
    "resource_exhausted",
    "throttled",
)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run codex exec and back off on rate-limit/quota errors."
    )
    parser.add_argument("--repo-dir", default=".", help="Repository directory for codex exec -C")
    parser.add_argument(
        "--prompt-file",
        default=".codex/prompts/self_improve.md",
        help="Prompt file path",
    )
    parser.add_argument(
        "--codex-bin",
        default=os.getenv("CODEX_BIN", "codex"),
        help="Codex executable name or path",
    )
    parser.add_argument(
        "--approval-policy",
        default=os.getenv("CODEX_APPROVAL_POLICY", "never"),
        choices=("untrusted", "on-failure", "on-request", "never"),
        help="Approval policy passed to codex exec -a",
    )
    parser.add_argument(
        "--sandbox",
        default=os.getenv("CODEX_SANDBOX", "workspace-write"),
        choices=("read-only", "workspace-write", "danger-full-access"),
        help="Sandbox mode passed to codex exec -s",
    )
    parser.add_argument(
        "--max-attempts",
        type=_positive_int,
        default=int(os.getenv("CODEX_MAX_ATTEMPTS", "6")),
        help="Total attempts including first try",
    )
    parser.add_argument(
        "--base-delay-sec",
        type=_positive_int,
        default=int(os.getenv("CODEX_BASE_DELAY_SEC", "900")),
        help="Initial backoff delay in seconds",
    )
    parser.add_argument(
        "--max-delay-sec",
        type=_positive_int,
        default=int(os.getenv("CODEX_MAX_DELAY_SEC", "14400")),
        help="Maximum backoff delay in seconds",
    )
    parser.add_argument(
        "--run-reason",
        default=os.getenv("RUN_REASON", "").strip(),
        help="Optional reason appended to the prompt",
    )
    return parser.parse_args()


def _is_rate_limited(output: str) -> bool:
    text = output.lower()
    return any(token in text for token in RATE_LIMIT_TOKENS)


def _retry_after_seconds(output: str) -> int | None:
    text = output.lower()
    patterns = (
        r"try again in\s+(\d+)\s*s",
        r"try again in\s+(\d+)\s*seconds?",
        r"retry after\s+(\d+)\s*s",
        r"retry after\s+(\d+)\s*seconds?",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            seconds = int(match.group(1))
            if seconds > 0:
                return seconds
    return None


def _read_prompt(path: str, run_reason: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        prompt = handle.read().strip()
    if run_reason:
        prompt = f"{prompt}\n\nRun context: {run_reason}"
    return prompt


def _execute(cmd: list[str], cwd: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        msg = f"{exc}\nHint: verify codex CLI is installed and available in PATH."
        print(msg, file=sys.stderr)
        return 127, msg

    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    return proc.returncode, f"{proc.stdout}\n{proc.stderr}"


def main() -> int:
    args = _parse_args()
    prompt = _read_prompt(args.prompt_file, args.run_reason)

    cmd = [
        args.codex_bin,
        "exec",
        "-C",
        args.repo_dir,
        "-a",
        args.approval_policy,
        "-s",
        args.sandbox,
        prompt,
    ]

    last_rc = 1
    for attempt in range(1, args.max_attempts + 1):
        print(
            f"[codex-loop] attempt {attempt}/{args.max_attempts} "
            f"(approval={args.approval_policy}, sandbox={args.sandbox})"
        )
        rc, combined = _execute(cmd, cwd=args.repo_dir)
        last_rc = rc
        if rc == 0:
            print("[codex-loop] completed successfully")
            return 0

        if not _is_rate_limited(combined):
            print("[codex-loop] failed with non-rate-limit error; not retrying", file=sys.stderr)
            return rc

        if attempt == args.max_attempts:
            break

        hinted_delay = _retry_after_seconds(combined)
        backoff_delay = args.base_delay_sec * (2 ** (attempt - 1))
        delay = max(backoff_delay, hinted_delay or 0)
        delay = min(delay, args.max_delay_sec)
        wake_at = datetime.now() + timedelta(seconds=delay)
        print(
            f"[codex-loop] rate-limit/quota detected. "
            f"sleeping {delay}s until {wake_at.isoformat(timespec='seconds')}"
        )
        time.sleep(delay)

    print("[codex-loop] max attempts reached after repeated rate-limit/quota failures", file=sys.stderr)
    return last_rc


if __name__ == "__main__":
    raise SystemExit(main())
