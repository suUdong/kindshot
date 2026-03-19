#!/usr/bin/env python3
"""Run scheduler-friendly collector backfill with optional Telegram reporting.

새벽에 시작해서 --stop-hour(KST) 전까지 반복 수집.
한 라운드에 --max-days 일치씩 처리, 최대 --max-rounds 라운드.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta

from kindshot.backfill_auto import backfill_lock, compute_auto_backfill_plan, default_lock_path, format_auto_noop_message
from kindshot.collector import compute_finalized_date, load_collection_log_summary, load_collector_state, run_backfill
from kindshot.config import load_config
from kindshot.telegram_ops import format_backfill_notification, send_telegram_message

_KST = timezone(timedelta(hours=9))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run automatic collect backfill batch")
    parser.add_argument("--max-days", type=int, default=4, help="maximum number of dates to process per batch")
    parser.add_argument("--max-rounds", type=int, default=20, help="maximum number of batches before stopping (0=unlimited)")
    parser.add_argument("--stop-hour", type=int, default=7, help="KST hour to stop backfill (default: 7 = 오전 7시)")
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
    try:
        send_telegram_message(message, bot_token, chat_id)
    except Exception as exc:
        print(f"[warn] telegram send failed: {exc}", file=sys.stderr)


def _past_stop_hour(stop_hour: int) -> bool:
    """KST 기준 stop_hour 이후이면 True."""
    now_kst = datetime.now(_KST)
    return now_kst.hour >= stop_hour


async def _main() -> int:
    args = _parse_args()
    config = load_config()

    round_num = 0
    total_processed = 0

    try:
        with backfill_lock(default_lock_path(config)):
            while True:
                # 시간 제한 체크 (새벽 작업 → 오전 뉴스 시작 전 중단)
                if _past_stop_hour(args.stop_hour):
                    print(f"[info] KST {args.stop_hour}시 이후, 백필 중단 (rounds={round_num}, processed={total_processed})")
                    break

                # 라운드 제한 체크
                if args.max_rounds > 0 and round_num >= args.max_rounds:
                    print(f"[info] max rounds ({args.max_rounds}) reached, stopping")
                    break

                plan = compute_auto_backfill_plan(config, max_days=args.max_days, oldest_date=args.oldest_date)
                if plan is None:
                    if round_num == 0:
                        state = load_collector_state(config.collector_state_path)
                        finalized_date = compute_finalized_date(
                            cutoff_hour=config.finalize_cutoff_hour_kst,
                            cutoff_minute=config.finalize_cutoff_minute_kst,
                        )
                        noop_message = format_auto_noop_message(
                            None,
                            cursor_date=state.cursor_date,
                            oldest_date=args.oldest_date,
                            finalized_date=finalized_date or "-",
                        )
                        print(noop_message)
                        if args.notify_noop:
                            _send_if_configured(noop_message, notify_required=False)
                    else:
                        print(f"[info] backfill caught up after {round_num} rounds, {total_processed} dates total")
                    break

                round_num += 1
                print(f"[round {round_num}] {plan.requested_from} -> {plan.requested_to}")

                result = await run_backfill(
                    config,
                    from_date=plan.requested_from,
                    to_date=plan.requested_to,
                )
                total_processed += len(result.processed_dates) if result else 0

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

    # 최종 요약 알림
    summary = load_collection_log_summary(config.collector_log_path)
    state = load_collector_state(config.collector_state_path)
    message = format_backfill_notification(None, state, summary)
    if total_processed > 0:
        message = f"Kindshot Backfill AUTO DONE\nrounds={round_num} total_processed={total_processed}\n{message}"
    print(message)
    _send_if_configured(message, notify_required=False)
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
