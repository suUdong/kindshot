"""Tests for KIS REST client: graceful failure, px=0 guard, field mapping, orderbook spread, rate limiting."""

import re
import time

import pytest
from aioresponses import CallbackResult, aioresponses
import aiohttp

from kindshot.config import Config
from kindshot.kis_client import BASE_URL_PAPER, IndexDailyInfo, IndexInfo, KisClient, NewsDisclosure, NewsDisclosureFetchResult, OrderbookSnapshot, QuoteRiskState

PRICE_URL = re.compile(rf"^{re.escape(BASE_URL_PAPER)}/uapi/domestic-stock/v1/quotations/inquire-price\?.*")
ORDERBOOK_URL = re.compile(rf"^{re.escape(BASE_URL_PAPER)}/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn\?.*")
INDEX_URL = re.compile(rf"^{re.escape(BASE_URL_PAPER)}/uapi/domestic-stock/v1/quotations/inquire-index-price\?.*")
INDEX_DAILY_URL = re.compile(rf"^{re.escape(BASE_URL_PAPER)}/uapi/domestic-stock/v1/quotations/inquire-index-daily-price\?.*")
NEWS_URL = re.compile(rf"^{re.escape(BASE_URL_PAPER)}/uapi/domestic-stock/v1/quotations/news-title(?:\?.*)?$")


def _cfg(**kw) -> Config:
    return Config(kis_app_key="test_key", kis_app_secret="test_secret", kis_account_no="12345", **kw)


def _token_response():
    return {"access_token": "fake_token", "token_type": "Bearer"}


def _price_output(px="50000", cum="1000000000", open_px="49500", **extra_fields):
    return {
        "output": {
            "stck_prpr": px,
            "acml_tr_pbmn": cum,
            "stck_oprc": open_px,
            **extra_fields,
        }
    }


def _orderbook_output(
    askp1="50100",
    bidp1="49900",
    ask_size1="200",
    bid_size1="300",
    total_ask_size="5000",
    total_bid_size="7000",
):
    return {
        "output1": {
            "askp1": askp1,
            "bidp1": bidp1,
            "askp_rsqn1": ask_size1,
            "bidp_rsqn1": bid_size1,
            "total_askp_rsqn": total_ask_size,
            "total_bidp_rsqn": total_bid_size,
        }
    }


async def test_get_price_success():
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        kis._last_request = 0.0  # skip rate limit wait in tests
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", payload=_token_response())
            m.get(PRICE_URL, payload=_price_output())
            m.get(ORDERBOOK_URL, payload=_orderbook_output())
            result = await kis.get_price("005930")

    assert result is not None
    assert result.px == 50000.0
    assert result.open_px == 49500.0
    # spread_bps from orderbook: (50100-49900)/50000 * 10000 = 40.0 bps
    assert result.spread_bps is not None
    assert result.spread_bps == pytest.approx(40.0, abs=0.5)
    assert result.risk_state == QuoteRiskState()
    assert result.orderbook == OrderbookSnapshot(
        ask_price1=50100.0,
        bid_price1=49900.0,
        ask_size1=200,
        bid_size1=300,
        total_ask_size=5000,
        total_bid_size=7000,
        spread_bps=40.0,
    )
    assert result.cum_volume == 0.0
    assert result.listed_shares is None
    assert result.volume_turnover_rate is None
    assert result.prior_volume_rate is None


async def test_get_price_with_spread_from_orderbook():
    """Spread should be calculated from best ask/bid via orderbook API."""
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        kis._last_request = 0.0
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", payload=_token_response())
            m.get(PRICE_URL, payload=_price_output(px="100000"))
            m.get(ORDERBOOK_URL, payload=_orderbook_output(askp1="100050", bidp1="99950"))
            result = await kis.get_price("005930")

    assert result is not None
    # (100050 - 99950) / 100000 * 10000 = 10.0 bps
    assert result.spread_bps == pytest.approx(10.0, abs=0.5)


async def test_get_price_orderbook_failure_returns_none_spread():
    """If orderbook API fails, spread_bps should be None but price still returned."""
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        kis._last_request = 0.0
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", payload=_token_response())
            m.get(PRICE_URL, payload=_price_output())
            m.get(ORDERBOOK_URL, status=500)
            result = await kis.get_price("005930")

    assert result is not None
    assert result.px == 50000.0
    assert result.spread_bps is None


async def test_get_price_px_zero_returns_none():
    """px=0 should be treated as UNAVAILABLE."""
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        kis._last_request = 0.0
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", payload=_token_response())
            m.get(PRICE_URL, payload=_price_output(px="0"))
            result = await kis.get_price("005930")

    assert result is None


async def test_get_price_empty_output_returns_none():
    """Missing/empty output field should return None."""
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        kis._last_request = 0.0
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", payload=_token_response())
            m.get(PRICE_URL, payload={"msg_cd": "EGW00000"})
            result = await kis.get_price("005930")

    assert result is None


async def test_get_price_no_credentials():
    """No KIS credentials = no token = None."""
    cfg = Config(kis_app_key="", kis_app_secret="")  # Explicitly no keys
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        result = await kis.get_price("005930")

    assert result is None


async def test_get_index_change_missing_prdy_ctrt_returns_none():
    """Missing prdy_ctrt should return None (fail-close), not 0.0."""
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        kis._last_request = 0.0
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
        kis._last_request = 0.0
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
        kis._last_request = 0.0
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", payload=_token_response())
            m.get(INDEX_URL, payload={"output": {"prdy_ctrt": "-1.23"}})
            result = await kis.get_index_change("0001")

    assert result == -1.23


async def test_get_index_info_returns_typed_result():
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        kis._last_request = 0.0
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", payload=_token_response())
            m.get(INDEX_URL, payload={"output": {"bstp_nmix_prdy_ctrt": "-0.77", "ascn_issu_cnt": "320", "down_issu_cnt": "540", "stnr_issu_cnt": "45"}})
            result = await kis.get_index_info("2001")

    assert isinstance(result, IndexInfo)
    assert result is not None
    assert result.iscd == "2001"
    assert result.change_pct == -0.77
    assert result.up_issue_count == 320
    assert result.down_issue_count == 540
    assert result.flat_issue_count == 45
    assert result.fetch_latency_ms >= 0


async def test_get_index_daily_info_returns_exact_date_row():
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        kis._last_request = 0.0
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", payload=_token_response())
            m.get(
                INDEX_DAILY_URL,
                payload={
                    "output2": [
                        {
                            "stck_bsop_date": "20260312",
                            "bstp_nmix_prpr": "510.0",
                            "bstp_nmix_oprc": "500.0",
                            "bstp_nmix_hgpr": "515.0",
                            "bstp_nmix_lwpr": "495.0",
                            "acml_vol": "1200",
                            "acml_tr_pbmn": "45000",
                        },
                        {
                            "stck_bsop_date": "20260313",
                            "bstp_nmix_prpr": "520.0",
                            "bstp_nmix_oprc": "511.0",
                            "bstp_nmix_hgpr": "525.0",
                            "bstp_nmix_lwpr": "509.0",
                            "acml_vol": "1300",
                            "acml_tr_pbmn": "47000",
                        },
                    ]
                },
            )
            result = await kis.get_index_daily_info("0001", "20260313")

    assert isinstance(result, IndexDailyInfo)
    assert result is not None
    assert result.iscd == "0001"
    assert result.date == "20260313"
    assert result.close == 520.0
    assert result.open_px == 511.0
    assert result.high == 525.0
    assert result.low == 509.0
    assert result.volume == 1300.0
    assert result.value == 47000.0


async def test_get_index_daily_info_returns_none_when_exact_date_missing():
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        kis._last_request = 0.0
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", payload=_token_response())
            m.get(
                INDEX_DAILY_URL,
                payload={
                    "output2": [
                        {
                            "stck_bsop_date": "20260312",
                            "bstp_nmix_prpr": "510.0",
                            "bstp_nmix_oprc": "500.0",
                            "bstp_nmix_hgpr": "515.0",
                            "bstp_nmix_lwpr": "495.0",
                        }
                    ]
                },
            )
            result = await kis.get_index_daily_info("0001", "20260313")

    assert result is None


async def test_token_failure_returns_none():
    """Token endpoint failure should gracefully return None."""
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", status=500)
            result = await kis.get_price("005930")

    assert result is None


async def test_stats_snapshot_tracks_request_failures():
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        kis._last_request = 0.0
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", payload=_token_response())
            m.get(PRICE_URL, exception=aiohttp.ClientError("boom"))
            result = await kis.get_price("005930")

    assert result is None
    stats = kis.stats_snapshot()
    assert stats["request_failures"]["FHKST01010100"] == 1


async def test_stats_snapshot_tracks_invalid_payloads():
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        kis._last_request = 0.0
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", payload=_token_response())
            m.get(PRICE_URL, payload={"output": "invalid"})
            result = await kis.get_price("005930")

    assert result is None
    stats = kis.stats_snapshot()
    assert stats["invalid_payloads"]["FHKST01010100"] == 1


async def test_rate_limit_enforced():
    """Consecutive calls should respect rate limit delay."""
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        kis._rate_limit = 0.1  # 100ms for test speed

        t0 = time.monotonic()
        await kis._rate_limit_wait()
        await kis._rate_limit_wait()
        elapsed = time.monotonic() - t0

    # Second call should wait ~0.1s
    assert elapsed >= 0.08


async def test_open_px_returned():
    """open_px should be extracted from stck_oprc field."""
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        kis._last_request = 0.0
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", payload=_token_response())
            m.get(PRICE_URL, payload=_price_output(px="50000", open_px="49000"))
            m.get(ORDERBOOK_URL, payload=_orderbook_output())
            result = await kis.get_price("005930")

    assert result is not None
    assert result.open_px == 49000.0


async def test_get_price_maps_quote_risk_state():
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        kis._last_request = 0.0
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", payload=_token_response())
            m.get(
                PRICE_URL,
                payload=_price_output(
                    temp_stop_yn="Y",
                    sltr_yn="N",
                    short_over_yn="Y",
                    vi_cls_code="D",
                    ovtm_vi_cls_code="O",
                    invt_caful_yn="Y",
                    mrkt_warn_cls_code="03",
                    mang_issu_cls_code="M",
                ),
            )
            m.get(ORDERBOOK_URL, payload=_orderbook_output())
            result = await kis.get_price("005930")

    assert result is not None
    assert result.risk_state == QuoteRiskState(
        temp_stop_yn="Y",
        sltr_yn="N",
        short_over_yn="Y",
        vi_cls_code="D",
        ovtm_vi_cls_code="O",
        invt_caful_yn="Y",
        mrkt_warn_cls_code="03",
        mang_issu_cls_code="M",
    )


async def test_get_price_orderbook_snapshot_parses_zero_sizes_as_zero():
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        kis._last_request = 0.0
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", payload=_token_response())
            m.get(PRICE_URL, payload=_price_output())
            m.get(ORDERBOOK_URL, payload=_orderbook_output(ask_size1="0", bid_size1="0", total_ask_size="0", total_bid_size="0"))
            result = await kis.get_price("005930")

    assert result is not None
    assert result.orderbook is not None
    assert result.orderbook.ask_size1 == 0
    assert result.orderbook.total_bid_size == 0


async def test_get_price_maps_participation_fields():
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        kis._last_request = 0.0
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", payload=_token_response())
            m.get(
                PRICE_URL,
                payload=_price_output(
                    acml_vol="1234567",
                    lstn_stcn="250000000",
                    vol_tnrt="0.49",
                    prdy_vrss_vol_rate="215.31",
                ),
            )
            m.get(ORDERBOOK_URL, payload=_orderbook_output())
            result = await kis.get_price("005930")

    assert result is not None
    assert result.cum_volume == 1234567.0
    assert result.listed_shares == 250000000.0
    assert result.volume_turnover_rate == 0.49
    assert result.prior_volume_rate == 215.31


async def test_get_news_disclosures_wraps_single_output_dict():
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        kis._last_request = 0.0
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", payload=_token_response())
            m.get(NEWS_URL, payload={"output": {"cntt_usiq_srno": "NEWS001", "data_tm": "143000"}})
            result = await kis.get_news_disclosures()

    assert result == [{"cntt_usiq_srno": "NEWS001", "data_tm": "143000"}]


async def test_get_news_disclosure_items_returns_typed_rows():
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        kis._last_request = 0.0
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", payload=_token_response())
            m.get(
                NEWS_URL,
                payload={
                    "output": {
                        "cntt_usiq_srno": "NEWS001",
                        "data_dt": "20260312",
                        "data_tm": "143000",
                        "hts_pbnt_titl_cntt": "삼성전자(005930) 공급계약 체결",
                        "dorg": "한국거래소",
                        "iscd1": "005930",
                    }
                },
            )
            result = await kis.get_news_disclosure_items()

    assert result == [
        NewsDisclosure(
            news_id="NEWS001",
            data_dt="20260312",
            data_tm="143000",
            title="삼성전자(005930) 공급계약 체결",
            dorg="한국거래소",
            tickers=("005930",),
            provider_code="",
        )
    ]


async def test_get_news_disclosures_invalid_output_returns_empty():
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        kis._last_request = 0.0
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", payload=_token_response())
            m.get(NEWS_URL, payload={"output": "invalid"})
            result = await kis.get_news_disclosures()

    assert result == []


async def test_get_news_disclosures_paginates_on_tr_cont_m():
    cfg = _cfg()
    request_tr_conts: list[str] = []

    def _callback(url, **kwargs):
        request_tr_conts.append(kwargs["headers"].get("tr_cont", ""))
        if len(request_tr_conts) == 1:
            return CallbackResult(
                status=200,
                payload={"output": [{"cntt_usiq_srno": "NEWS001"}]},
                headers={"tr_cont": "M"},
            )
        return CallbackResult(
            status=200,
            payload={"output": [{"cntt_usiq_srno": "NEWS002"}]},
            headers={"tr_cont": "D"},
        )

    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        kis._last_request = 0.0
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", payload=_token_response())
            m.get(NEWS_URL, callback=_callback, repeat=True)
            result = await kis.get_news_disclosures()

    assert result == [{"cntt_usiq_srno": "NEWS001"}, {"cntt_usiq_srno": "NEWS002"}]
    assert request_tr_conts == ["", "N"]


async def test_get_news_disclosure_items_passes_date_and_hour_params():
    captured_queries: list[dict[str, str]] = []

    def _callback(url, **kwargs):
        captured_queries.append(dict(url.query))
        return CallbackResult(
            status=200,
            payload={"output": []},
            headers={"tr_cont": "D"},
        )

    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        kis._last_request = 0.0
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", payload=_token_response())
            m.get(NEWS_URL, callback=_callback)
            await kis.get_news_disclosure_items(date="0020260310", from_time="235959")

    assert len(captured_queries) == 1
    assert captured_queries[0]["FID_INPUT_DATE_1"] == "0020260310"
    assert captured_queries[0]["FID_INPUT_HOUR_1"] == "235959"


async def test_get_news_disclosure_fetch_result_marks_pagination_truncated():
    cfg = _cfg()
    async with aiohttp.ClientSession() as session:
        kis = KisClient(cfg, session)
        kis._last_request = 0.0
        with aioresponses() as m:
            m.post(f"{BASE_URL_PAPER}/oauth2/tokenP", payload=_token_response())
            for idx in range(10):
                m.get(
                    NEWS_URL,
                    payload={"output": [{"cntt_usiq_srno": f"NEWS{idx:03d}"}]},
                    headers={"tr_cont": "M"},
                )
            result = await kis.get_news_disclosure_fetch_result()

    assert isinstance(result, NewsDisclosureFetchResult)
    assert result.pagination_truncated is True
    assert len(result.items) == 10
