"""
K 線重採樣服務單元測試 (tests/test_kline_service.py)

驗證 1m, 2m, 3m, 5m, 15m, 30m, 45m, 1h, 2h, 4h, 1D 等 11 種時序重採樣正確性、
OHLC 數值關係與成交量守恆性。
"""

import unittest
from services.storage.kline_service import KlineService


class TestKlineService(unittest.TestCase):
    """
    測試 KlineService 多週期聚合功能。
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.service = KlineService(db_path="data/market_data.db")

    def test_preload_all_intervals(self) -> None:
        """
        驗證全部 11 種週期均能正確完成聚合並取得非空資料。
        """
        counts = self.service.preload_all_intervals()
        expected_intervals = ["1m", "2m", "3m", "5m", "15m", "30m", "45m", "1h", "2h", "4h", "1d"]
        for interval in expected_intervals:
            self.assertIn(interval, counts)
            self.assertGreater(counts[interval], 0, f"週期 {interval} 之 K 棒數量應大於 0")

        # 驗證週期愈大，棒數愈少 (1m > 5m > 1h > 1d)
        self.assertGreater(counts["1m"], counts["5m"])
        self.assertGreater(counts["5m"], counts["1h"])
        self.assertGreater(counts["1h"], counts["1d"])

    def test_ohlc_relationships(self) -> None:
        """
        驗證 K 棒價格關係: High >= Max(Open, Close) 且 Low <= Min(Open, Close)。
        """
        bars = self.service.get_kbars("5m")
        self.assertGreater(len(bars), 0)

        for bar in bars[:100]:  # 抽樣驗證前 100 根
            self.assertGreaterEqual(bar["high"], bar["low"])
            self.assertGreaterEqual(bar["high"], bar["open"])
            self.assertGreaterEqual(bar["high"], bar["close"])
            self.assertLessEqual(bar["low"], bar["open"])
            self.assertLessEqual(bar["low"], bar["close"])
            self.assertGreaterEqual(bar["volume"], 0)

    def test_volume_conservation(self) -> None:
        """
        驗證不同週期聚合間之總成交量守恆性 (1m 總量應等於 5m 總量)。
        """
        bars_1m = self.service.get_kbars("1m")
        bars_5m = self.service.get_kbars("5m")

        vol_1m = sum(b["volume"] for b in bars_1m)
        vol_5m = sum(b["volume"] for b in bars_5m)

        self.assertEqual(vol_1m, vol_5m, "1 分鐘與 5 分鐘總成交量應完全守恆")

    def test_unsupported_interval(self) -> None:
        """
        驗證輸入無效週期時觸發防錯機制拋出 ValueError。
        """
        with self.assertRaises(ValueError):
            self.service.get_kbars("7m")


if __name__ == "__main__":
    unittest.main()
