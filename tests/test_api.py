"""
API 路由單元測試 (tests/test_api.py)

驗證 FastAPI 路由分發正確性、回傳結構符合標準化 JSON 規範，
並確保 SQLite 秒 K 線總結與區間查詢功能無誤。
"""

import unittest
import asyncio
from backend.main import (
    get_micro_taiwan_summary_endpoint,
    get_micro_taiwan_second_bars_endpoint,
    get_kline_intervals_endpoint,
    get_kline_endpoint
)


class TestAPIEndpoints(unittest.TestCase):
    """
    測試後端 API 路由分發與資料回應格式。
    """

    def test_micro_taiwan_summary_endpoint(self) -> None:
        """
        測試 GET /api/history/micro-taiwan-10/summary 端點回應。
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            res = loop.run_until_complete(get_micro_taiwan_summary_endpoint())
            self.assertEqual(res.get("status"), "SUCCESS")
            data = res.get("data", {})
            self.assertEqual(data.get("table_name"), "tmf10_second_bars")
            self.assertEqual(data.get("total_rows"), 689139)
        finally:
            loop.close()

    def test_micro_taiwan_second_bars_endpoint(self) -> None:
        """
        測試 GET /api/history/micro-taiwan-10/second-bars 區間查詢端點回應。
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            res = loop.run_until_complete(
                get_micro_taiwan_second_bars_endpoint(
                    start_time="2026-09-16 08:45:00",
                    end_time="2026-09-16 08:45:10",
                    symbol="TMFJ6"
                )
            )
            self.assertEqual(res.get("status"), "SUCCESS")
            data = res.get("data", {})
            bars = data.get("bars", [])
            self.assertGreater(len(bars), 0)
            self.assertIn("時間", bars[0])
            self.assertIn("收盤價", bars[0])
        finally:
            loop.close()

    def test_get_kline_intervals_endpoint(self) -> None:
        """
        測試 GET /api/kline/intervals 支援週期清單端點。
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            res = loop.run_until_complete(get_kline_intervals_endpoint())
            self.assertEqual(res.get("status"), "SUCCESS")
            data = res.get("data", [])
            keys = [item["key"] for item in data]
            for expected in ["1m", "2m", "3m", "5m", "15m", "30m", "45m", "1h", "2h", "4h", "1d"]:
                self.assertIn(expected, keys)
        finally:
            loop.close()

    def test_get_kline_endpoint(self) -> None:
        """
        測試 GET /api/kline?interval=5m 端點回應格式。
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            res = loop.run_until_complete(get_kline_endpoint(interval="5m", limit=10))
            self.assertEqual(res.get("status"), "SUCCESS")
            data = res.get("data", {})
            self.assertEqual(data.get("interval"), "5m")
            self.assertEqual(data.get("count"), 10)
            bars = data.get("bars", [])
            self.assertEqual(len(bars), 10)
            self.assertIn("open", bars[0])
            self.assertIn("close", bars[0])
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
