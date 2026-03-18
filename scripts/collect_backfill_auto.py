#!/usr/bin/env python3
"""Run scheduler-friendly collector backfill with optional Telegram reporting."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from kindshot.backfill_auto import backfill_lock, compute_auto_backfill_plan, default_lock_path, format_auto_noop_message
from kindshot.collector import compute_finalized_date, load_collection_log_summary, load_collector_state, run_backfill
from kindshot.config import load_config
from kindshot.telegram_ops import format_backfill_notification, send_telegram_message


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run automatic collect backfill batch")
    parser.add_argument("--max-days", type=int, default=4, help="maximum number of dates to process in one run")
    parser.add_argument("--oldest-date", default="", metavar="YYYYMMDD", help="do not backfill older than this date")
    parser.add_argument("--notify-noop", action="store_true", help="send Telegram message when there is nothing to do")
    return parser.parse_args()


def _send_if_configured(message: str, *, notify_required: bool) -> None:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        if notify_required:
            raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required for this notification")
        return
    send_telegram_message(message, bot_token, chat_id)


async def _main() -> int:
    args = _parse_args()
    config = load_config()
    state = load_collector_state(config.collector_state_path)
    finalized_date = compute_finalized_date(
        cutoff_hour=config.finalize_cutoff_hour_kst,
        cutoff_minute=config.finalize_cutoff_minute_kst,
    )
    plan = compute_auto_backfill_plan(config, max_days=args.max_days, oldest_date=args.oldest_date)
    if plan is None:
        noop_message = format_auto_noop_message(
            None,
            cursor_date=state.cursor_date,
            oldest_date=args.oldest_date,
            finalized_date=finalized_date or "-",
        )
        print(noop_message)
        if args.notify_noop:
            _send_if_configured(noop_message, notify_required=False)
        return 0

    try:
        with backfill_lock(default_lock_path(config)):
            result = await run_backfill(
                config,
                from_date=plan.requested_from,
                to_date=plan.requested_to,
            )
    except FileExistsError:
        print(f"Kindshot Backfill AUTO LOCKED\nlock={default_lock_path(config)}", file=sys.stderr)
        return 4
    except Exception:
        summary = load_collection_log_summary(config.collector_log_path)
        state = load_collector_state(config.collector_state_path)
        message = format_backfill_notification(None, state, summary, error=sys.exc_info()[1])
        print(message)
        _send_if_configured(message, notify_required=False)
        raise

    summary = load_collection_log_summary(config.collector_log_path)
    state = load_collector_state(config.collector_state_path)
    message = format_backfill_notification(result, state, summary)
    print(message)
    _send_if_configured(message, notify_required=False)
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
