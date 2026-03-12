"""Quick test: dump raw KIS news API response."""
import asyncio
import json
import os
import sys
import aiohttp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from kindshot.config import Config

async def main():
    config = Config()
    base = "https://openapivts.koreainvestment.com:29443" if config.kis_is_paper else "https://openapi.koreainvestment.com:9443"

    async with aiohttp.ClientSession() as session:
        # Get token
        async with session.post(
            f"{base}/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": config.kis_app_key,
                "appsecret": config.kis_app_secret,
            },
        ) as resp:
            token_data = await resp.json()
            token = token_data.get("access_token", "")
            print(f"Token: {token[:20]}..." if token else "NO TOKEN")
            if not token:
                print(f"Token response: {json.dumps(token_data, indent=2)}")
                return

        headers = {
            "authorization": f"Bearer {token}",
            "appkey": config.kis_app_key,
            "appsecret": config.kis_app_secret,
            "tr_id": "FHKST01011800",
            "custtype": "P",
        }

        for ft in ["", "081510"]:
            params = {
                "FID_NEWS_OFER_ENTP_CODE": "",
                "FID_COND_MRKT_CLS_CODE": "",
                "FID_INPUT_ISCD": "",
                "FID_TITL_CNTT": "",
                "FID_INPUT_DATE_1": "",
                "FID_INPUT_HOUR_1": ft,
                "FID_RANK_SORT_CLS_CODE": "",
                "FID_INPUT_SRNO": "",
            }
            async with session.get(
                f"{base}/uapi/domestic-stock/v1/quotations/news-title",
                headers=headers,
                params=params,
            ) as resp:
                data = await resp.json()
                output = data.get("output", [])
                rt_cd = data.get("rt_cd", "?")
                msg = data.get("msg1", "")
                print(f"\n=== from_time='{ft}' | rt_cd={rt_cd} | msg={msg} | output count={len(output) if isinstance(output, list) else 'not-list'} ===")
                if isinstance(output, list) and output:
                    times = [it.get("data_tm","") for it in output if it.get("data_tm")]
                    print(f"  time range: {min(times)} ~ {max(times)}")
                    print(f"  first: {json.dumps(output[0], ensure_ascii=False)[:120]}")
                elif not isinstance(output, list):
                    print(f"  output type: {type(output).__name__}, value: {str(output)[:200]}")

asyncio.run(main())
