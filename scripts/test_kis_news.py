"""Quick test: compare KIS news API with/without from_time."""
import asyncio
import os
import sys
import aiohttp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from kindshot.kis_client import KisClient
from kindshot.config import Config

async def main():
    config = Config()
    async with aiohttp.ClientSession() as session:
        client = KisClient(config, session)

        # Test 1: no from_time (should get latest)
        items_no_ft = await client.get_news_disclosures(from_time="")
        # Test 2: with from_time
        items_with_ft = await client.get_news_disclosures(from_time="081510")

        print(f"=== No from_time: {len(items_no_ft)} items ===")
        for it in items_no_ft[:5]:
            print(f"  {it.get('data_tm','')} | {it.get('cntt_usiq_srno','')} | {it.get('hts_pbnt_titl_cntt','')[:50]}")
        if items_no_ft:
            times = [it.get("data_tm","") for it in items_no_ft if it.get("data_tm")]
            if times:
                print(f"  time range: {min(times)} ~ {max(times)}")

        print(f"\n=== from_time=081510: {len(items_with_ft)} items ===")
        for it in items_with_ft[:5]:
            print(f"  {it.get('data_tm','')} | {it.get('cntt_usiq_srno','')} | {it.get('hts_pbnt_titl_cntt','')[:50]}")
        if items_with_ft:
            times = [it.get("data_tm","") for it in items_with_ft if it.get("data_tm")]
            if times:
                print(f"  time range: {min(times)} ~ {max(times)}")

asyncio.run(main())
