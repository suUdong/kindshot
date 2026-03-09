"""Entry point for `python -m kindshot`."""

import asyncio
import sys
from pathlib import Path

from kindshot.main import _parse_args, run


def main() -> None:
    args = _parse_args()
    if args.replay:
        from kindshot.config import load_config
        from kindshot.replay import replay

        config = load_config()
        log_path = Path(args.replay)
        if not log_path.exists():
            print(f"Error: replay file not found: {log_path}")
            sys.exit(1)
        asyncio.run(replay(log_path, config))
    else:
        asyncio.run(run())


if __name__ == "__main__":
    main()
