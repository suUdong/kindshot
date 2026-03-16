"""Entry point for `python -m kindshot`."""

import asyncio
import sys
from pathlib import Path

from kindshot.collector import collect_main
from kindshot.config import load_config
from kindshot.main import _parse_args, run


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "collect":
        asyncio.run(collect_main(sys.argv[2:], load_config()))
        return

    args = _parse_args()
    if args.replay:
        from kindshot.replay import replay

        config = load_config()
        log_path = Path(args.replay)
        if not log_path.exists():
            print(f"Error: replay file not found: {log_path}")
            sys.exit(1)
        asyncio.run(replay(log_path, config, report_output_path=args.replay_report_out or ""))
    elif args.replay_runtime_date:
        from kindshot.replay import replay_runtime_date

        config = load_config()
        asyncio.run(replay_runtime_date(args.replay_runtime_date, config, report_output_path=args.replay_report_out or ""))
    elif args.replay_day:
        from kindshot.replay import replay_day

        config = load_config()
        asyncio.run(replay_day(args.replay_day, config, report_output_path=args.replay_report_out or ""))
    elif args.replay_day_status:
        from kindshot.replay import replay_day_status

        config = load_config()
        replay_day_status(args.replay_day_status, config, output_path=args.replay_status_out or "")
    elif args.replay_ops_summary:
        from kindshot.replay import replay_ops_summary

        config = load_config()
        replay_ops_summary(config, limit=max(1, int(args.replay_ops_limit or 10)), output_path=args.replay_ops_out or "")
    elif args.replay_ops_queue_ready:
        from kindshot.replay import replay_ops_queue_ready

        config = load_config()
        replay_ops_queue_ready(
            config,
            limit=max(1, int(args.replay_ops_run_limit or 5)),
            include_reported=bool(args.replay_ops_include_reported),
            require_runtime=bool(args.replay_ops_require_runtime),
            require_collector=bool(args.replay_ops_require_collector),
            min_merged_events=max(0, int(args.replay_ops_min_merged_events or 1)),
            output_path=args.replay_ops_queue_out or "",
        )
    elif args.replay_ops_run_ready:
        from kindshot.replay import replay_ops_run_ready

        config = load_config()
        asyncio.run(
            replay_ops_run_ready(
                config,
                limit=max(1, int(args.replay_ops_run_limit or 5)),
                include_reported=bool(args.replay_ops_include_reported),
                require_runtime=bool(args.replay_ops_require_runtime),
                require_collector=bool(args.replay_ops_require_collector),
                min_merged_events=max(0, int(args.replay_ops_min_merged_events or 1)),
                output_path=args.replay_ops_run_out or "",
            )
        )
    elif args.replay_ops_cycle_ready:
        from kindshot.replay import replay_ops_cycle_ready

        config = load_config()
        asyncio.run(
            replay_ops_cycle_ready(
                config,
                limit=max(1, int(args.replay_ops_run_limit or 5)),
                include_reported=bool(args.replay_ops_include_reported),
                require_runtime=bool(args.replay_ops_require_runtime),
                require_collector=bool(args.replay_ops_require_collector),
                min_merged_events=max(0, int(args.replay_ops_min_merged_events or 1)),
                continue_on_error=bool(args.replay_ops_continue_on_error),
                output_path=args.replay_ops_cycle_out or "",
            )
        )
    else:
        asyncio.run(run())


if __name__ == "__main__":
    main()
