"""trade_db 모듈 테스트."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from kindshot.trade_db import TradeDB, backfill_from_logs, _version_for_date, _parse_hour


@pytest.fixture
def tmp_db(tmp_path: Path) -> TradeDB:
    db = TradeDB(tmp_path / "test.db")
    yield db
    db.close()


@pytest.fixture
def sample_logs(tmp_path: Path) -> tuple[Path, Path]:
    """최소한의 로그+스냅샷 fixture 생성."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    snaps_dir = tmp_path / "snapshots"
    snaps_dir.mkdir()

    # 이벤트 로그
    event = {
        "type": "event",
        "event_id": "test001",
        "schema_version": "0.1.3",
        "ticker": "005930",
        "corp_name": "삼성전자",
        "headline": "삼성전자, 신규 수주 계약 체결",
        "bucket": "POS_STRONG",
        "keyword_hits": ["수주"],
        "news_category": "수주공시",
        "news_signal": {
            "contract_amount_eok": 8237,
            "impact_score": 86,
            "cluster": {"cluster_id": "cluster001", "cluster_size": 2},
        },
        "decision_action": "BUY",
        "decision_confidence": 85,
        "decision_size_hint": "M",
        "decision_reason": "rule_fallback:수주",
        "guardrail_result": "PASS",
        "skip_stage": "",
        "detected_at": "2026-03-18T10:30:00+09:00",
        "ctx": {
            "ret_today": 1.5,
            "adv_value_20d": 500000000000.0,
            "spread_bps": 5.0,
            "rsi_14": 55.0,
            "vol_pct_20d": 2.0,
        },
        "market_ctx": {
            "kospi_change_pct": 0.5,
            "kosdaq_change_pct": 0.3,
            "kospi_breadth_ratio": 0.6,
            "kosdaq_breadth_ratio": 0.55,
        },
    }
    event2 = {
        **event,
        "event_id": "test002",
        "ticker": "000660",
        "corp_name": "SK하이닉스",
        "headline": "SK하이닉스, 공급계약 체결",
        "decision_confidence": 90,
        "detected_at": "2026-03-18T14:15:00+09:00",
    }

    log_path = logs_dir / "kindshot_20260318.jsonl"
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
        f.write(json.dumps(event2, ensure_ascii=False) + "\n")

    # 스냅샷
    horizons = [
        ("t0", 0.0),
        ("t+30s", 0.002),
        ("t+1m", 0.005),
        ("t+2m", 0.008),
        ("t+5m", 0.012),
        ("t+10m", 0.015),
        ("t+15m", 0.01),
        ("t+30m", 0.005),
        ("close", 0.003),
    ]
    snap_path = snaps_dir / "20260318.jsonl"
    with open(snap_path, "w", encoding="utf-8") as f:
        for eid in ["test001", "test002"]:
            for horizon, ret in horizons:
                snap = {
                    "event_id": eid,
                    "horizon": horizon,
                    "px": 75000 if eid == "test001" else 120000,
                    "ret_long_vs_t0": ret if horizon != "t0" else None,
                    "spread_bps": 5.0 if horizon == "t0" else 4.0,
                }
                f.write(json.dumps(snap) + "\n")

    return logs_dir, snaps_dir


class TestTradeDB:
    def test_init_creates_tables(self, tmp_db: TradeDB) -> None:
        assert tmp_db.trade_count() == 0

    def test_upsert_and_count(self, tmp_db: TradeDB) -> None:
        tmp_db.upsert_trade({
            "event_id": "e1",
            "date": "20260318",
            "ticker": "005930",
            "exit_ret_pct": 1.5,
            "version_tag": "v65",
            "hour_slot": 10,
        })
        tmp_db.commit()
        assert tmp_db.trade_count() == 1
        assert tmp_db.has_date("20260318")
        assert not tmp_db.has_date("20260319")

    def test_upsert_replace(self, tmp_db: TradeDB) -> None:
        tmp_db.upsert_trade({"event_id": "e1", "date": "20260318", "ticker": "005930", "confidence": 80})
        tmp_db.upsert_trade({"event_id": "e1", "date": "20260318", "ticker": "005930", "confidence": 90})
        tmp_db.commit()
        assert tmp_db.trade_count() == 1
        rows = tmp_db.query("SELECT confidence FROM trades WHERE event_id = 'e1'")
        assert rows[0]["confidence"] == 90

    def test_version_summary_empty(self, tmp_db: TradeDB) -> None:
        assert tmp_db.version_summary() == []

    def test_version_summary(self, tmp_db: TradeDB) -> None:
        for i, ret in enumerate([1.0, -0.5, 2.0]):
            tmp_db.upsert_trade({
                "event_id": f"e{i}",
                "date": "20260318",
                "ticker": "005930",
                "exit_ret_pct": ret,
                "version_tag": "v65",
                "hour_slot": 10,
            })
        tmp_db.commit()
        summary = tmp_db.version_summary()
        assert len(summary) == 1
        assert summary[0]["version_tag"] == "v65"
        assert summary[0]["total_trades"] == 3
        assert summary[0]["wins"] == 2

    def test_ticker_summary(self, tmp_db: TradeDB) -> None:
        tmp_db.upsert_trade({"event_id": "e1", "date": "20260318", "ticker": "005930", "corp_name": "삼성전자", "exit_ret_pct": 1.0})
        tmp_db.upsert_trade({"event_id": "e2", "date": "20260318", "ticker": "000660", "corp_name": "SK하이닉스", "exit_ret_pct": -0.5})
        tmp_db.commit()
        summary = tmp_db.ticker_summary()
        assert len(summary) == 2
        assert summary[0]["ticker"] == "005930"  # 수익 높은 쪽이 먼저

    def test_hour_summary(self, tmp_db: TradeDB) -> None:
        tmp_db.upsert_trade({"event_id": "e1", "date": "20260318", "ticker": "005930", "exit_ret_pct": 1.0, "hour_slot": 9})
        tmp_db.upsert_trade({"event_id": "e2", "date": "20260318", "ticker": "005930", "exit_ret_pct": -0.5, "hour_slot": 14})
        tmp_db.commit()
        summary = tmp_db.hour_summary()
        assert len(summary) == 2
        assert summary[0]["hour_slot"] == 9

    def test_category_summary(self, tmp_db: TradeDB) -> None:
        tmp_db.upsert_trade({"event_id": "e1", "date": "20260318", "ticker": "005930", "exit_ret_pct": 1.0, "news_category": "수주공시"})
        tmp_db.upsert_trade({"event_id": "e2", "date": "20260318", "ticker": "000660", "exit_ret_pct": -0.5, "news_category": "실적"})
        tmp_db.commit()
        summary = tmp_db.category_summary()
        assert len(summary) == 2


class TestVersionMapping:
    def test_version_for_date_pre_v59(self) -> None:
        assert _version_for_date("20260318") == "pre-v59"
        assert _version_for_date("20260325") == "pre-v59"

    def test_version_for_date_v64(self) -> None:
        assert _version_for_date("20260327") == "v64"

    def test_version_for_unknown_date(self) -> None:
        assert _version_for_date("20250101") == "pre-v59"


class TestParseHour:
    def test_valid_iso(self) -> None:
        assert _parse_hour("2026-03-18T10:30:00+09:00") == 10

    def test_empty(self) -> None:
        assert _parse_hour("") == 0

    def test_invalid(self) -> None:
        assert _parse_hour("not-a-date") == 0


class TestBackfill:
    def test_backfill_from_logs(self, tmp_path: Path, sample_logs: tuple[Path, Path]) -> None:
        logs_dir, snaps_dir = sample_logs
        db = TradeDB(tmp_path / "backfill.db")
        try:
            count = backfill_from_logs(db, logs_dir, snaps_dir)
            assert count == 2
            assert db.trade_count() == 2
            assert db.has_date("20260318")

            # 스냅샷 데이터 검증
            rows = db.query("SELECT * FROM trades WHERE event_id = 'test001'")
            assert len(rows) == 1
            row = rows[0]
            assert row["ticker"] == "005930"
            assert row["confidence"] == 85
            assert row["hour_slot"] == 10
            assert row["contract_amount_eok"] == 8237
            assert row["impact_score"] == 86
            assert row["news_cluster_id"] == "cluster001"
            assert row["ret_t5m"] is not None
            assert row["entry_px"] == 75000
            assert row["spread_t0"] == 5.0
            assert row["spread_t5m"] == 4.0

            # 중복 실행 방지
            count2 = backfill_from_logs(db, logs_dir, snaps_dir)
            assert count2 == 0  # skip already backfilled
        finally:
            db.close()

    def test_backfill_force(self, tmp_path: Path, sample_logs: tuple[Path, Path]) -> None:
        logs_dir, snaps_dir = sample_logs
        db = TradeDB(tmp_path / "backfill_force.db")
        try:
            backfill_from_logs(db, logs_dir, snaps_dir)
            count = backfill_from_logs(db, logs_dir, snaps_dir, force=True)
            assert count == 2  # force re-backfill
        finally:
            db.close()

    def test_backfill_empty_logs(self, tmp_path: Path) -> None:
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        snaps_dir = tmp_path / "snaps"
        snaps_dir.mkdir()
        db = TradeDB(tmp_path / "empty.db")
        try:
            count = backfill_from_logs(db, logs_dir, snaps_dir)
            assert count == 0
        finally:
            db.close()

    def test_backfill_uses_embedded_price_snapshots_when_external_file_missing(self, tmp_path: Path) -> None:
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        snaps_dir = tmp_path / "snaps"
        snaps_dir.mkdir()

        log_path = logs_dir / "kindshot_20260327.jsonl"
        rows = [
            {
                "type": "event",
                "event_id": "embedded001",
                "ticker": "005930",
                "corp_name": "삼성전자",
                "headline": "삼성전자 흑자 전환",
                "bucket": "POS_STRONG",
                "keyword_hits": ["흑자전환"],
                "decision_action": "BUY",
                "decision_confidence": 84,
                "decision_size_hint": "M",
                "decision_reason": "historical buy",
                "detected_at": "2026-03-27T10:00:00+09:00",
                "ctx": {
                    "ret_today": 1.0,
                    "adv_value_20d": 10000000000.0,
                    "spread_bps": 10.0,
                },
                "market_ctx": {},
            },
            {
                "type": "price_snapshot",
                "event_id": "embedded001",
                "horizon": "t0",
                "px": 100.0,
                "ret_long_vs_t0": None,
                "spread_bps": 10.0,
            },
            {
                "type": "price_snapshot",
                "event_id": "embedded001",
                "horizon": "t+5m",
                "px": 101.0,
                "ret_long_vs_t0": 0.01,
                "spread_bps": 8.0,
            },
            {
                "type": "price_snapshot",
                "event_id": "embedded001",
                "horizon": "t+20m",
                "px": 102.0,
                "ret_long_vs_t0": 0.02,
                "spread_bps": 6.0,
            },
            {
                "type": "price_snapshot",
                "event_id": "embedded001",
                "horizon": "close",
                "px": 101.5,
                "ret_long_vs_t0": 0.015,
                "spread_bps": 5.0,
            },
        ]
        log_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")

        db = TradeDB(tmp_path / "embedded.db")
        try:
            count = backfill_from_logs(db, logs_dir, snaps_dir)
            assert count == 1
            row = db.query(
                "SELECT ret_t5m, ret_t20m, ret_close, exit_ret_pct, spread_t0, spread_t20m, spread_close "
                "FROM trades WHERE event_id = 'embedded001'"
            )[0]
            assert row["ret_t5m"] == 1.0
            assert row["ret_t20m"] == 2.0
            assert row["ret_close"] == 1.5
            assert row["exit_ret_pct"] is not None
            assert row["spread_t0"] == 10.0
            assert row["spread_t20m"] == 6.0
            assert row["spread_close"] == 5.0
        finally:
            db.close()
