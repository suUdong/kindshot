#!/usr/bin/env python3
"""Telegram 연동 테스트 — BUY/SELL/Daily Summary 샘플 메시지 발송.

Usage:
    # 실제 발송 (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 필요)
    python scripts/test_telegram.py

    # 포맷만 출력 (발송 안 함)
    python scripts/test_telegram.py --dry-run

    # 실제 send 경로를 네트워크 없이 검증
    python scripts/test_telegram.py --simulate-send

    # 특정 메시지만
    python scripts/test_telegram.py --type buy
    python scripts/test_telegram.py --type sell
    python scripts/test_telegram.py --type daily
    python scripts/test_telegram.py --type guardrail
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from urllib.request import Request

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from kindshot.performance import DailySummary, TradeRecord
from kindshot.telegram_ops import (
    format_buy_signal,
    format_daily_summary_signal,
    format_high_conf_skip_signal,
    format_sell_signal,
    send_telegram_message,
    telegram_configured,
    _telegram_target,
)


def _sample_buy() -> str:
    return format_buy_signal(
        ticker="005930",
        corp_name="삼성전자",
        headline="삼성전자, 반도체 신규 공급계약 체결 (1조원 규모)",
        bucket="supply_contract",
        confidence=85,
        size_hint="M",
        reason="대형 공급계약 체결로 매출 성장 기대",
        keyword_hits=["공급계약", "체결"],
        hold_minutes=30,
        ret_today=0.5,
        spread_bps=12,
        adv_display="1.2T",
        mode="paper",
        decision_source="LLM",
        tp_pct=2.0,
        sl_pct=-1.5,
    )


def _sample_sell() -> str:
    return format_sell_signal(
        ticker="005930",
        exit_type="take_profit",
        horizon="t+15m",
        ret_pct=2.15,
        pnl_won=107500,
        confidence=85,
        size_won=5_000_000,
        hold_seconds=900,
        mode="paper",
        open_positions=2,
    )


def _sample_guardrail() -> str:
    return format_high_conf_skip_signal(
        ticker="035420",
        corp_name="NAVER",
        headline="네이버, AI 사업부 분사 결정",
        confidence=82,
        skip_reason="CHASE_BUY",
        shadow_scheduled=True,
        mode="paper",
    )


def _sample_daily() -> str:
    summary = DailySummary(
        date="2026-03-27",
        total_trades=5,
        wins=3,
        losses=2,
        win_rate=60.0,
        total_pnl_pct=1.85,
        total_pnl_won=92500,
        trades=[
            TradeRecord(ticker="005930", entry_px=50000, exit_px=51000, pnl_pct=2.0),
            TradeRecord(ticker="035420", entry_px=30000, exit_px=29700, pnl_pct=-1.0),
            TradeRecord(ticker="000660", entry_px=80000, exit_px=81600, pnl_pct=2.0),
        ],
    )
    return format_daily_summary_signal(
        summary,
        open_positions=1,
        daily_pnl_won=92500,
        consecutive_stop_losses=0,
        report_path="data/performance/2026-03-27_summary.json",
    )


SAMPLES = {
    "buy": ("BUY Signal", _sample_buy),
    "sell": ("SELL Signal", _sample_sell),
    "guardrail": ("Guardrail Block", _sample_guardrail),
    "daily": ("Daily Summary", _sample_daily),
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Telegram 연동 테스트")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="포맷만 출력, 발송 안 함")
    mode.add_argument("--simulate-send", action="store_true", help="실제 전송 경로를 네트워크 없이 시뮬레이션")
    parser.add_argument("--type", choices=list(SAMPLES.keys()), help="특정 메시지만 테스트")
    return parser


def _make_simulated_opener(captured: dict[str, object]) -> Callable[[Request, float], object]:
    class _Resp:
        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def read(self) -> bytes:
            return json.dumps({"ok": True, "result": {"message_id": 1}}).encode("utf-8")

    def _fake_urlopen(req: Request, timeout: float) -> _Resp:
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _Resp()

    return _fake_urlopen


def run(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    types = [args.type] if args.type else list(SAMPLES.keys())

    if not args.dry_run and not args.simulate_send and not telegram_configured():
        print("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 설정되지 않았습니다.")
        print("  export TELEGRAM_BOT_TOKEN=your_bot_token")
        print("  export TELEGRAM_CHAT_ID=your_chat_id")
        print("  또는 --dry-run / --simulate-send 로 코드 경로를 확인하세요.")
        return 1

    for msg_type in types:
        label, factory = SAMPLES[msg_type]
        text = factory()
        print(f"\n{'=' * 50}")
        print(f"  [{label}]")
        print(f"{'=' * 50}")
        print(text)

        if args.simulate_send:
            captured: dict[str, object] = {}
            try:
                ok = send_telegram_message(
                    text,
                    "SIMULATED_BOT_TOKEN",
                    "SIMULATED_CHAT_ID",
                    opener=_make_simulated_opener(captured),
                )
                status = "OK (simulated)" if ok else "FAIL (simulated)"
            except Exception as e:
                status = f"ERROR: {e}"
            print(f"\n  → 발송 결과: {status}")
            if captured:
                body = captured.get("body", {})
                chat_id = body.get("chat_id", "-") if isinstance(body, dict) else "-"
                print(f"    simulated_url={captured.get('url', '-')}")
                print(f"    simulated_chat_id={chat_id}")
                print(f"    simulated_timeout={captured.get('timeout', '-')}")
        elif not args.dry_run:
            target = _telegram_target()
            assert target is not None
            bot_token, chat_id = target
            try:
                ok = send_telegram_message(text, bot_token, chat_id)
                status = "OK" if ok else "FAIL"
            except Exception as e:
                status = f"ERROR: {e}"
            print(f"\n  → 발송 결과: {status}")

    if args.dry_run:
        print(f"\n{'=' * 50}")
        print("  [DRY-RUN] 실제 발송하지 않았습니다.")
        print(f"{'=' * 50}")

    if args.simulate_send:
        print(f"\n{'=' * 50}")
        print("  [SIMULATED-SEND] 네트워크 없이 send 경로를 검증했습니다.")
        print(f"{'=' * 50}")

    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
