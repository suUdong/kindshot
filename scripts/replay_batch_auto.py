#!/usr/bin/env python3
"""Replay 배치 자동화 — 미처리 날짜 자동 리플레이 + 성과 리포트 + 텔레그램 알림.

사용법:
    python scripts/replay_batch_auto.py                     # 기본 실행 (최대 5일)
    python scripts/replay_batch_auto.py --max-days 10       # 최대 10일
    python scripts/replay_batch_auto.py --include-reported   # 이미 리포트된 날짜도 재실행
    python scripts/replay_batch_auto.py --no-telegram       # 텔레그램 알림 비활성화

크론 등록 예시 (backfill 크론 30분 후):
    10 3 * * * cd /opt/kindshot && . .venv/bin/activate && \
        TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... \
        timeout 2h python scripts/replay_batch_auto.py --max-days 5 \
        >> /opt/kindshot/logs/replay_batch_auto.log 2>&1
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

_KST = timezone(timedelta(hours=9))


def _load_config():
    from dotenv import load_dotenv
    load_dotenv()
    from kindshot.config import Config
    return Config()


def _aggregate_performance(config) -> dict:
    """모든 day_reports에서 성과 집계."""
    reports_dir = config.replay_day_reports_dir
    if not reports_dir.exists():
        return {"total_dates": 0, "total_buys": 0}

    total_buys = 0
    total_wins = 0
    total_return = 0.0
    returns_list: list[float] = []
    dates_with_data = 0

    for report_file in sorted(reports_dir.glob("*.json")):
        try:
            report = json.loads(report_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        summary = report.get("summary", {})
        buy_count = int(summary.get("buy_decisions", 0) or 0)
        if buy_count == 0:
            continue

        dates_with_data += 1
        total_buys += buy_count

        for ret_entry in report.get("returns", []):
            ret_pct = ret_entry.get("return_pct")
            if ret_pct is not None:
                returns_list.append(float(ret_pct))
                total_return += float(ret_pct)
                if float(ret_pct) > 0:
                    total_wins += 1

    win_rate = (total_wins / len(returns_list) * 100) if returns_list else 0
    avg_return = (total_return / len(returns_list)) if returns_list else 0

    # Profit factor
    gross_win = sum(r for r in returns_list if r > 0)
    gross_loss = abs(sum(r for r in returns_list if r < 0))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")

    return {
        "total_dates": dates_with_data,
        "total_buys": total_buys,
        "trades_with_returns": len(returns_list),
        "total_wins": total_wins,
        "win_rate_pct": round(win_rate, 1),
        "avg_return_pct": round(avg_return, 2),
        "profit_factor": round(profit_factor, 2),
        "best_pct": round(max(returns_list), 2) if returns_list else 0,
        "worst_pct": round(min(returns_list), 2) if returns_list else 0,
    }


def _build_telegram_message(cycle_report: dict, perf: dict) -> str:
    """텔레그램 알림 메시지 생성."""
    now = datetime.now(_KST).strftime("%Y-%m-%d %H:%M")
    executed = cycle_report.get("executed_count", 0)
    failed = cycle_report.get("failed_count", 0)

    lines = [
        f"📊 Replay Batch Report ({now})",
        f"실행: {executed}일 | 실패: {failed}일",
    ]

    # 실행된 날짜별 요약
    for row in cycle_report.get("rows", []):
        if not row.get("executed"):
            continue
        date = row.get("date", "?")
        buys = row.get("summary", {}).get("buy_decisions", 0)
        price = row.get("summary", {}).get("price_data_trades", 0)
        lines.append(f"  {date}: BUY={buys} price_data={price}")

    # 전체 성과
    if perf.get("trades_with_returns", 0) > 0:
        lines.append("")
        lines.append(f"📈 전체 성과 ({perf['total_dates']}일)")
        lines.append(f"승률: {perf['win_rate_pct']}% ({perf['total_wins']}/{perf['trades_with_returns']})")
        lines.append(f"평균: {perf['avg_return_pct']:+.2f}% | PF: {perf['profit_factor']}")
        lines.append(f"최고: {perf['best_pct']:+.2f}% | 최저: {perf['worst_pct']:+.2f}%")

    return "\n".join(lines)


def _send_telegram(message: str) -> bool:
    """텔레그램 메시지 전송."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        print("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID 미설정, 알림 생략")
        return False

    from kindshot.telegram_ops import send_telegram_message
    return send_telegram_message(message, bot_token, chat_id)


async def main() -> None:
    max_days = 5
    include_reported = "--include-reported" in sys.argv
    no_telegram = "--no-telegram" in sys.argv

    for i, arg in enumerate(sys.argv):
        if arg == "--max-days" and i + 1 < len(sys.argv):
            max_days = int(sys.argv[i + 1])

    config = _load_config()
    from kindshot.replay import replay_ops_cycle_ready

    print(f"=== Replay Batch Auto ({datetime.now(_KST).strftime('%Y-%m-%d %H:%M')}) ===")
    print(f"max_days={max_days}, include_reported={include_reported}")

    cycle_report = await replay_ops_cycle_ready(
        config,
        limit=max_days,
        include_reported=include_reported,
        require_runtime=True,
        min_merged_events=1,
        continue_on_error=True,
    )

    executed = cycle_report.get("executed_count", 0)
    failed = cycle_report.get("failed_count", 0)
    print(f"\n실행 완료: {executed}일, 실패: {failed}일")

    # 전체 성과 집계
    perf = _aggregate_performance(config)
    if perf.get("trades_with_returns", 0) > 0:
        print(f"\n=== 전체 성과 ===")
        print(f"날짜: {perf['total_dates']}일, 거래: {perf['trades_with_returns']}건")
        print(f"승률: {perf['win_rate_pct']}% ({perf['total_wins']}/{perf['trades_with_returns']})")
        print(f"평균: {perf['avg_return_pct']:+.2f}%, PF: {perf['profit_factor']}")
    else:
        print("\n성과 데이터 없음 (BUY 건 없거나 가격 데이터 부족)")

    # 성과 요약 저장
    perf_path = Path("data/replay/performance_summary.json")
    perf_path.parent.mkdir(parents=True, exist_ok=True)
    perf["generated_at"] = datetime.now(timezone.utc).isoformat()
    perf_path.write_text(json.dumps(perf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"성과 요약 저장: {perf_path}")

    # 텔레그램 알림
    if not no_telegram and executed > 0:
        msg = _build_telegram_message(cycle_report, perf)
        if _send_telegram(msg):
            print("텔레그램 알림 전송 완료")
        else:
            print("텔레그램 알림 전송 실패 또는 미설정")


if __name__ == "__main__":
    asyncio.run(main())
