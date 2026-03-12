"""Quick test: compare KIS news API with/without from_time."""
import asyncio
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from kindshot.kis_client import KisClient

async def main():
    cfg_cls = type("C", (), {
        "kis_app_key": os.environ["KIS_APP_KEY"],
        "kis_app_secret": os.environ["KIS_APP_SECRET"],
        "kis_account": os.environ.get("KIS_ACCOUNT", ""),
        "kis_base_url": os.environ.get("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443"),
        "kis_rate_limit_delay": 0.1,
    })()
    client = KisClient(cfg_cls)
    await client.initialize()

    # Test 1: no from_time (should get latest)
    items_no_ft = await client.get_news_disclosures(from_time="")
    # Test 2: with from_time
    items_with_ft = await client.get_news_disclosures(from_time="081510")

    print(f"=== No from_time: {len(items_no_ft)} items ===")
    for it in items_no_ft[:5]:
        print(f"  {it.get('data_tm','')} | {it.get('cntt_usiq_srno','')} | {it.get('hts_pbnt_titl_cntt','')[:40]}")
    if items_no_ft:
        times = [it.get("data_tm","") for it in items_no_ft if it.get("data_tm")]
        print(f"  time range: {min(times)} ~ {max(times)}")

    print(f"\n=== from_time=081510: {len(items_with_ft)} items ===")
    for it in items_with_ft[:5]:
        print(f"  {it.get('data_tm','')} | {it.get('cntt_usiq_srno','')} | {it.get('hts_pbnt_titl_cntt','')[:40]}")
    if items_with_ft:
        times = [it.get("data_tm","") for it in items_with_ft if it.get("data_tm")]
        print(f"  time range: {min(times)} ~ {max(times)}")

    await client.close()

asyncio.run(main())
