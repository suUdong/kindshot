#!/usr/bin/env python3
"""Run collector backfill and send one Telegram notification."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from kindshot.collector import (
    load_collection_log_summary,
    load_collector_state,
    run_backfill,
)
from kindshot.config import load_config
from kindshot.telegram_ops import format_backfill_notification, send_telegram_message


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run collect backfill and notify via Telegram")
    parser.add_argument("--cursor", default="", metavar="YYYYMMDD")
    parser.add_argument("--from", dest="from_date", default="", metavar="YYYYMMDD")
    parser.add_argument("--to", dest="to_date", default="", metavar="YYYYMMDD")
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        print("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required", file=sys.stderr)
        return 2

    config = load_config()
    result = None
    error: Exception | None = None
    try:
        result = await run_backfill(
            config,
            cursor=args.cursor,
            from_date=args.from_date,
            to_date=args.to_date,
        )
    except Exception as exc:  # pragma: no cover - behavior exercised by formatting tests
        error = exc

    state = load_collector_state(config.collector_state_path)
    summary = load_collection_log_summary(config.collector_log_path)
    message = format_backfill_notification(result, state, summary, error=error)
    print(message)

    sent = send_telegram_message(message, bot_token, chat_id)
    if not sent:
        print("Telegram delivery failed", file=sys.stderr)
        return 3
    if error is not None:
        raise error
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
