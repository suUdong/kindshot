"""Tests for dart_enricher: CorpCodeMapper + DartEnricher."""

from __future__ import annotations

import io
import json
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kindshot.dart_enricher import (
    BuybackInfo,
    CorpCodeMapper,
    DartEnricher,
    _is_direct_method,
    _parse_int,
)


# ── Helper: _parse_int ───────────────────────────────────


class TestParseInt:
    def test_normal(self):
        assert _parse_int("1000") == 1000

    def test_comma(self):
        assert _parse_int("1,000,000") == 1_000_000

    def test_spaces(self):
        assert _parse_int(" 500 ") == 500

    def test_dash(self):
        assert _parse_int("-") == 0

    def test_empty(self):
        assert _parse_int("") == 0

    def test_invalid(self):
        assert _parse_int("abc") == 0


# ── Helper: _is_direct_method ────────────────────────────


class TestIsDirectMethod:
    def test_direct(self):
        assert _is_direct_method("직접취득") is True

    def test_trust(self):
        assert _is_direct_method("신탁계약체결") is False

    def test_direct_variant(self):
        assert _is_direct_method("장내직접취득") is True

    def test_empty(self):
        assert _is_direct_method("") is False


# ── BuybackInfo ──────────────────────────────────────────


class TestBuybackInfo:
    def test_frozen(self):
        info = BuybackInfo(
            corp_code="00126380",
            corp_name="삼성전자",
            ticker="005930",
            rcept_no="20260329000001",
            method="직접취득",
            is_direct=True,
            planned_shares=100000,
            planned_amount=50_000_000_000,
            purpose="주가안정",
            period_start="2026.03.29",
            period_end="2026.06.29",
        )
        assert info.is_direct is True
        assert info.planned_amount == 50_000_000_000
        with pytest.raises(AttributeError):
            info.ticker = "999999"  # type: ignore[misc]


# ── CorpCodeMapper ───────────────────────────────────────


def _make_corp_code_zip(entries: list[tuple[str, str]]) -> bytes:
    """corp_code XML ZIP 생성 헬퍼."""
    root = ET.Element("result")
    for corp_code, stock_code in entries:
        item = ET.SubElement(root, "list")
        cc = ET.SubElement(item, "corp_code")
        cc.text = corp_code
        sc = ET.SubElement(item, "stock_code")
        sc.text = stock_code
        cn = ET.SubElement(item, "corp_name")
        cn.text = f"Corp_{stock_code}"

    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("CORPCODE.xml", xml_bytes)
    return buf.getvalue()


class TestCorpCodeMapper:
    def test_parse_zip(self):
        raw = _make_corp_code_zip([
            ("00126380", "005930"),
            ("00164779", "000660"),
        ])
        mapping = CorpCodeMapper._parse_zip(raw)
        assert mapping["005930"] == "00126380"
        assert mapping["000660"] == "00164779"
        assert len(mapping) == 2

    def test_parse_zip_skips_empty_stock_code(self):
        raw = _make_corp_code_zip([
            ("00126380", "005930"),
            ("00999999", ""),  # 비상장사 (stock_code 없음)
        ])
        mapping = CorpCodeMapper._parse_zip(raw)
        assert len(mapping) == 1
        assert "005930" in mapping

    def test_cache_load(self, tmp_path: Path):
        from unittest.mock import patch as _patch
        from datetime import datetime

        cache_path = tmp_path / "dart_corp_codes.json"
        # 오늘 날짜로 캐시 저장
        with _patch("kindshot.dart_enricher.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 29, 9, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            today = "20260329"

        cache_data = {
            "date": today,
            "count": 2,
            "map": {"005930": "00126380", "000660": "00164779"},
        }
        cache_path.write_text(json.dumps(cache_data), encoding="utf-8")

        config = MagicMock()
        config.data_dir = tmp_path
        config.dart_api_key = "test_key"

        session = MagicMock()
        mapper = CorpCodeMapper(config, session, cache_dir=tmp_path)

        result = mapper._try_load_cache(today)
        assert result is True
        assert mapper.get_corp_code("005930") == "00126380"
        assert mapper.get_corp_code("000660") == "00164779"
        assert mapper.get_corp_code("999999") is None


# ── DartEnricher ─────────────────────────────────────────


class TestDartEnricher:
    @pytest.mark.asyncio
    async def test_fetch_buyback_success(self):
        config = MagicMock()
        config.dart_api_key = "test_key"
        config.dart_base_url = "https://opendart.fss.or.kr/api"

        ds005_response = {
            "status": "000",
            "list": [{
                "corp_name": "삼성전자",
                "aqpln_stk_qy": "100,000",
                "aqpln_stk_prc": "50,000,000,000",
                "aq_mth": "직접취득",
                "aq_pp": "주가안정 및 주주가치 제고",
                "aq_expd_bgd": "2026.03.29",
                "aq_expd_edd": "2026.06.29",
            }],
        }

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=ds005_response)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_resp)

        mapper = MagicMock()
        mapper.ensure_loaded = AsyncMock()
        mapper.get_corp_code = MagicMock(return_value="00126380")

        enricher = DartEnricher(config, session, mapper)
        info = await enricher.fetch_buyback("005930", "20260329000001")

        assert info is not None
        assert info.ticker == "005930"
        assert info.is_direct is True
        assert info.planned_shares == 100_000
        assert info.planned_amount == 50_000_000_000
        assert info.method == "직접취득"
        assert info.purpose == "주가안정 및 주주가치 제고"

    @pytest.mark.asyncio
    async def test_fetch_buyback_no_corp_code(self):
        config = MagicMock()
        config.dart_api_key = "test_key"
        session = MagicMock()
        mapper = MagicMock()
        mapper.ensure_loaded = AsyncMock()
        mapper.get_corp_code = MagicMock(return_value=None)

        enricher = DartEnricher(config, session, mapper)
        info = await enricher.fetch_buyback("999999", "20260329000001")
        assert info is None

    @pytest.mark.asyncio
    async def test_fetch_buyback_no_data(self):
        config = MagicMock()
        config.dart_api_key = "test_key"
        config.dart_base_url = "https://opendart.fss.or.kr/api"

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"status": "013", "message": "조회된 데이터가 없습니다"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_resp)

        mapper = MagicMock()
        mapper.ensure_loaded = AsyncMock()
        mapper.get_corp_code = MagicMock(return_value="00126380")

        enricher = DartEnricher(config, session, mapper)
        info = await enricher.fetch_buyback("005930", "20260329000001")
        assert info is None

    @pytest.mark.asyncio
    async def test_fetch_buyback_trust_method(self):
        config = MagicMock()
        config.dart_api_key = "test_key"
        config.dart_base_url = "https://opendart.fss.or.kr/api"

        ds005_response = {
            "status": "000",
            "list": [{
                "corp_name": "현대차",
                "aqpln_stk_qy": "50,000",
                "aqpln_stk_prc": "10,000,000,000",
                "aq_mth": "신탁계약체결",
                "aq_pp": "주가안정",
                "aq_expd_bgd": "2026.04.01",
                "aq_expd_edd": "2026.07.01",
            }],
        }

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=ds005_response)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_resp)

        mapper = MagicMock()
        mapper.ensure_loaded = AsyncMock()
        mapper.get_corp_code = MagicMock(return_value="00164779")

        enricher = DartEnricher(config, session, mapper)
        info = await enricher.fetch_buyback("005380", "20260329000002")

        assert info is not None
        assert info.is_direct is False
        assert info.method == "신탁계약체결"
        assert info.planned_amount == 10_000_000_000
