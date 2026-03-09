"""Tests for KIS REST client: graceful failure, px=0 guard, field mapping."""

import re

import pytest
from aioresponses import aioresponses
import aiohttp

from kindshot.config import Config
from kindshot.kis_client import KisClient, BASE_URL_PAPER

PRICE_URL = re.compile(rf"^{re.escape(BASE_URL_PAPER)}/uapi/domestic-stock/v1/quotations/inquire-price\?.*")
INDEX_URL = re.compile(rf"^{re.escape(BASE_URL_PAPER)}/uapi/domestic-stock/v1/quotations/inquire-index-price\?.*")


def _cfg(**kw) -> Config:
    return Config(kis_app_key="test_key", kis_app_secret="test_secret", kis_account_no="12345", **kw)


def _token_response():
    return {"access_token": "fake_token", "token_type": "Bearer"}


def _price_output(px="50000", cum="1000000000"):
    return {
        "output": {
            "stck_prpr": px,
            "acml_tr_pbmn": cum,
        }
    }


async def test_get_price_success():
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", payload=_token_response())
            m.get(PRICE_URL,
                  payload=_price_output())
            result = await kis.get_price("005930")

    assert result is not None
    assert result.px == 50000.0
    # spread_bps is None from inquire-price (needs 호가 API)
    assert result.spread_bps is None


async def test_get_price_px_zero_returns_none():
    """px=0 should be treated as UNAVAILABLE."""
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", payload=_token_response())
            m.get(PRICE_URL,
                  payload=_price_output(px="0"))
            result = await kis.get_price("005930")

    assert result is None


async def test_get_price_empty_output_returns_none():
    """Missing/empty output field should return None."""
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", payload=_token_response())
            m.get(PRICE_URL,
                  payload={"msg_cd": "EGW00000"})
            result = await kis.get_price("005930")

    assert result is None


async def test_get_price_no_credentials():
    """No KIS credentials = no token = None."""
    cfg = Config()  # No kis keys
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        result = await kis.get_price("005930")

    assert result is None


async def test_get_index_change_missing_prdy_ctrt_returns_none():
    """Missing prdy_ctrt should return None (fail-close), not 0.0."""
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", payload=_token_response())
            m.get(INDEX_URL, payload={"output": {}})
            result = await kis.get_index_change("0001")

    assert result is None


async def test_get_index_change_empty_string_returns_none():
    """Empty string prdy_ctrt should return None (fail-close)."""
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", payload=_token_response())
            m.get(INDEX_URL, payload={"output": {"prdy_ctrt": ""}})
            result = await kis.get_index_change("0001")

    assert result is None


async def test_get_index_change_valid_value():
    """Valid prdy_ctrt should be parsed as float."""
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", payload=_token_response())
            m.get(INDEX_URL, payload={"output": {"prdy_ctrt": "-1.23"}})
            result = await kis.get_index_change("0001")

    assert result == -1.23


async def test_token_failure_returns_none():
    """Token endpoint failure should gracefully return None."""
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", status=500)
            result = await kis.get_price("005930")

    assert result is None
