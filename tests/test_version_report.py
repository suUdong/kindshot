"""version_report 모듈 테스트."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kindshot.trade_db import TradeDB
from kindshot.version_report import (
    generate_actual_version_report,
    generate_version_comparison,
    report_to_json,
    report_to_text,
    save_report,
    _calc_mdd,
    _calc_profit_factor,
)


@pytest.fixture
def populated_db(tmp_path: Path) -> TradeDB:
    """트레이드가 채워진 DB fixture."""
    db = TradeDB(tmp_path / "test.db")
    trades = [
        {"event_id": "e1", "date": "20260318", "ticker": "005930", "headline": "삼성전자 수주",
         "keyword_hits": '["수주"]', "bucket": "POS_STRONG", "confidence": 85,
         "exit_ret_pct": 1.5, "version_tag": "pre-v59", "hour_slot": 10,
         "ret_t0": 0.0, "ret_t30s": 0.2, "ret_t1m": 0.5, "ret_t2m": 0.8,
         "ret_t5m": 1.2, "ret_t10m": 1.5, "ret_t15m": 1.0, "ret_t30m": 0.5, "ret_close": 0.3},
        {"event_id": "e2", "date": "20260318", "ticker": "000660", "headline": "SK하이닉스 공급계약",
         "keyword_hits": '["공급계약"]', "bucket": "POS_STRONG", "confidence": 90,
         "exit_ret_pct": -0.7, "version_tag": "pre-v59", "hour_slot": 14,
         "ret_t0": 0.0, "ret_t30s": -0.1, "ret_t1m": -0.3, "ret_t2m": -0.5,
         "ret_t5m": -0.7, "ret_t10m": -0.4, "ret_t15m": -0.2, "ret_t30m": 0.1, "ret_close": 0.0},
        {"event_id": "e3", "date": "20260319", "ticker": "035420", "headline": "네이버 투자유치",
         "keyword_hits": '["투자"]', "bucket": "POS_STRONG", "confidence": 80,
         "exit_ret_pct": 2.0, "version_tag": "pre-v59", "hour_slot": 11,
         "ret_t0": 0.0, "ret_t30s": 0.5, "ret_t1m": 1.0, "ret_t2m": 1.5,
         "ret_t5m": 2.0, "ret_t10m": 2.5, "ret_t15m": 2.0, "ret_t30m": 1.5, "ret_close": 1.0},
    ]
    for t in trades:
        db.upsert_trade(t)
    db.commit()
    yield db
    db.close()


class TestCalcHelpers:
    def test_profit_factor_normal(self) -> None:
        results = [
            {"exit_ret_pct": 2.0},
            {"exit_ret_pct": -1.0},
        ]
        assert _calc_profit_factor(results) == 2.0

    def test_profit_factor_no_loss(self) -> None:
        results = [{"exit_ret_pct": 1.0}]
        assert _calc_profit_factor(results) == float("inf")

    def test_profit_factor_no_win(self) -> None:
        results = [{"exit_ret_pct": -1.0}]
        assert _calc_profit_factor(results) == 0.0

    def test_profit_factor_empty(self) -> None:
        assert _calc_profit_factor([]) == 0.0

    def test_mdd_basic(self) -> None:
        results = [
            {"date": "20260318", "exit_ret_pct": 2.0},
            {"date": "20260319", "exit_ret_pct": -3.0},
            {"date": "20260320", "exit_ret_pct": 1.0},
        ]
        # cum: 2.0, -1.0, 0.0  peak: 2.0, 2.0, 2.0  dd: 0, -3.0, -2.0
        assert _calc_mdd(results) == -3.0

    def test_mdd_empty(self) -> None:
        assert _calc_mdd([]) == 0.0


class TestVersionReport:
    def test_generate_actual(self, populated_db: TradeDB) -> None:
        metrics = generate_actual_version_report(populated_db)
        assert len(metrics) >= 1
        m = metrics[0]
        assert m.version == "pre-v59"
        assert m.total_trades == 3
        assert m.wins == 2
        assert m.win_rate == pytest.approx(66.7, abs=0.1)

    def test_generate_simulated(self, populated_db: TradeDB) -> None:
        metrics = generate_version_comparison(populated_db)
        assert len(metrics) >= 2  # pre-v59 + v64 at minimum
        # 각 버전에 대해 동일한 3건의 트레이드로 시뮬레이션
        for m in metrics:
            assert m.total_trades >= 0

    def test_report_to_json(self, populated_db: TradeDB) -> None:
        metrics = generate_actual_version_report(populated_db)
        json_str = report_to_json(metrics)
        data = json.loads(json_str)
        assert isinstance(data, list)
        assert data[0]["version"] == "pre-v59"

    def test_report_to_text(self, populated_db: TradeDB) -> None:
        metrics = generate_actual_version_report(populated_db)
        text = report_to_text(metrics)
        assert "버전별 성과 비교 리포트" in text
        assert "pre-v59" in text

    def test_report_to_text_empty(self) -> None:
        text = report_to_text([])
        assert "No version data" in text

    def test_save_report(self, populated_db: TradeDB, tmp_path: Path) -> None:
        out_dir = tmp_path / "reports"
        path = save_report(populated_db, out_dir, simulated=False)
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, list)

    def test_save_report_simulated(self, populated_db: TradeDB, tmp_path: Path) -> None:
        out_dir = tmp_path / "reports"
        path = save_report(populated_db, out_dir, simulated=True)
        assert path.exists()
        assert "simulated" in path.name
