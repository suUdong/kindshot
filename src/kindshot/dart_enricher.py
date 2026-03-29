"""DART DS005 구조화 데이터 조회 모듈.

corpCode.xml → ticker→corp_code 매핑 + trsrStockAqDecsn.json 자사주 매입 조회.
"""

from __future__ import annotations

import io
import json
import logging
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiohttp

from kindshot.config import Config
from kindshot.tz import KST as _KST

logger = logging.getLogger(__name__)


# ── Data Models ───────────────────────────────────────────


@dataclass(frozen=True)
class BuybackInfo:
    """자사주 취득 결정 구조화 데이터."""

    corp_code: str
    corp_name: str
    ticker: str
    rcept_no: str
    method: str  # "직접취득" | "신탁계약체결" 등
    is_direct: bool  # 직접매입 여부
    planned_shares: int  # 취득 예정 주식수
    planned_amount: int  # 취득 예정 금액 (원)
    purpose: str  # 취득 목적
    period_start: str  # 취득 예정기간 시작 (YYYY.MM.DD 등)
    period_end: str  # 취득 예정기간 종료


def _parse_int(value: str) -> int:
    """숫자 문자열 파싱 (콤마, 공백 제거)."""
    cleaned = value.replace(",", "").replace(" ", "").strip()
    if not cleaned or cleaned == "-":
        return 0
    try:
        return int(cleaned)
    except ValueError:
        return 0


def _is_direct_method(method: str) -> bool:
    """직접취득 여부 판별."""
    return "직접" in method


# ── CorpCode Mapper ───────────────────────────────────────


class CorpCodeMapper:
    """DART corpCode.xml → {ticker: corp_code} 매핑.

    하루 1회 corpCode.xml ZIP을 다운로드 → XML 파싱 → 딕셔너리 빌드.
    메모리 캐시 + 디스크 캐시(data/dart_corp_codes.json).
    """

    _CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"

    def __init__(self, config: Config, session: aiohttp.ClientSession, *, cache_dir: Optional[Path] = None) -> None:
        self._config = config
        self._session = session
        self._map: dict[str, str] = {}  # ticker → corp_code
        self._cache_path = (cache_dir or config.data_dir) / "dart_corp_codes.json"
        self._loaded_date: Optional[str] = None

    @property
    def loaded(self) -> bool:
        return bool(self._map)

    def get_corp_code(self, ticker: str) -> Optional[str]:
        return self._map.get(ticker)

    async def ensure_loaded(self) -> None:
        """필요 시 캐시 로드 또는 API 다운로드."""
        today = datetime.now(_KST).strftime("%Y%m%d")
        if self._loaded_date == today and self._map:
            return

        # 디스크 캐시 시도
        if self._try_load_cache(today):
            return

        # API 다운로드
        await self._download_and_parse()
        self._loaded_date = today

    def _try_load_cache(self, today: str) -> bool:
        if not self._cache_path.exists():
            return False
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            if data.get("date") != today:
                return False
            mapping = data.get("map", {})
            if not mapping:
                return False
            self._map = mapping
            self._loaded_date = today
            logger.info("CorpCodeMapper: loaded %d entries from cache", len(self._map))
            return True
        except Exception:
            logger.warning("CorpCodeMapper: cache load failed", exc_info=True)
            return False

    async def _download_and_parse(self) -> None:
        api_key = self._config.dart_api_key
        if not api_key:
            logger.warning("CorpCodeMapper: DART_API_KEY not set")
            return

        url = f"{self._CORP_CODE_URL}?crtfc_key={api_key}"
        try:
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    logger.warning("CorpCodeMapper: HTTP %d", resp.status)
                    return
                raw = await resp.read()
        except Exception:
            logger.exception("CorpCodeMapper: download failed")
            return

        try:
            mapping = self._parse_zip(raw)
        except Exception:
            logger.exception("CorpCodeMapper: parse failed")
            return

        if not mapping:
            logger.warning("CorpCodeMapper: empty mapping after parse")
            return

        self._map = mapping
        logger.info("CorpCodeMapper: parsed %d entries from API", len(self._map))

        # 디스크 캐시 저장
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "date": datetime.now(_KST).strftime("%Y%m%d"),
                "count": len(self._map),
                "map": self._map,
            }
            self._cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception:
            logger.warning("CorpCodeMapper: cache save failed", exc_info=True)

    @staticmethod
    def _parse_zip(raw: bytes) -> dict[str, str]:
        """ZIP → XML 파싱 → {stock_code: corp_code} 딕셔너리."""
        mapping: dict[str, str] = {}
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            for name in zf.namelist():
                if not name.endswith(".xml"):
                    continue
                with zf.open(name) as f:
                    tree = ET.parse(f)
                root = tree.getroot()
                for item in root.iter("list"):
                    corp_code_el = item.find("corp_code")
                    stock_code_el = item.find("stock_code")
                    if corp_code_el is None or stock_code_el is None:
                        continue
                    corp_code = (corp_code_el.text or "").strip()
                    stock_code = (stock_code_el.text or "").strip()
                    if stock_code and corp_code:
                        mapping[stock_code] = corp_code
        return mapping


# ── DartEnricher ──────────────────────────────────────────


class DartEnricher:
    """DART DS005 구조화 데이터 조회 (자사주 취득 결정)."""

    def __init__(self, config: Config, session: aiohttp.ClientSession, mapper: CorpCodeMapper) -> None:
        self._config = config
        self._session = session
        self._mapper = mapper

    async def fetch_buyback(self, ticker: str, rcept_no: str) -> Optional[BuybackInfo]:
        """자사주 취득 결정 구조화 데이터 조회.

        Args:
            ticker: 종목코드 (6자리)
            rcept_no: 접수번호 (list.json에서 받은 값)

        Returns:
            BuybackInfo or None (조회 실패/데이터 없음)
        """
        await self._mapper.ensure_loaded()
        corp_code = self._mapper.get_corp_code(ticker)
        if not corp_code:
            logger.warning("DartEnricher: corp_code not found for ticker=%s", ticker)
            return None

        api_key = self._config.dart_api_key
        if not api_key:
            return None

        # DS005: trsrStockAqDecsn.json
        today = datetime.now(_KST).strftime("%Y%m%d")
        params = {
            "crtfc_key": api_key,
            "corp_code": corp_code,
            "bgn_de": today,
            "end_de": today,
        }
        url = f"{self._config.dart_base_url}/trsrStockAqDecsn.json"

        try:
            async with self._session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.warning("DartEnricher: DS005 HTTP %d for %s", resp.status, ticker)
                    return None
                data = await resp.json(content_type=None)
        except Exception:
            logger.exception("DartEnricher: DS005 fetch error for %s", ticker)
            return None

        status = data.get("status", "")
        if status == "013":
            # 조회된 데이터 없음
            logger.info("DartEnricher: no DS005 data for %s (status=013)", ticker)
            return None
        if status != "000":
            logger.warning("DartEnricher: DS005 status=%s message=%s", status, data.get("message", ""))
            return None

        items = data.get("list", [])
        if not items:
            return None

        # 첫 번째 항목 사용 (같은 날 동일 종목 중복 공시는 드묾)
        item = items[0]
        method = item.get("aq_mth", "").strip()
        return BuybackInfo(
            corp_code=corp_code,
            corp_name=item.get("corp_name", "").strip(),
            ticker=ticker,
            rcept_no=rcept_no,
            method=method,
            is_direct=_is_direct_method(method),
            planned_shares=_parse_int(item.get("aqpln_stk_qy", "0")),
            planned_amount=_parse_int(item.get("aqpln_stk_prc", "0")),
            purpose=item.get("aq_pp", "").strip(),
            period_start=item.get("aq_expd_bgd", "").strip(),
            period_end=item.get("aq_expd_edd", "").strip(),
        )
