"""
即時 K 棒聚合器單元測試 (tests/test_live_aggregator.py)

使用 Python 標準庫 unittest 驗證 RealTimeBarAggregator 之：
1. 首筆 Tick 初始化。
2. 同週期內 High / Low / Close / Volume 增量更新。
3. 跨越 5 分鐘邊界時自動封閉前一根棒並觸發回呼。
4. 強制收盤 (force_close_current_bar) 邊界情況。
"""

import unittest
from services.live.bar_aggregator import RealTimeBarAggregator, BarData


class TestRealTimeBarAggregator(unittest.TestCase):
    """即時 K 棒動態聚合器單元測試案例"""

    def setUp(self):
        # 1700000100 是 300 的精確倍數 (1700000100 % 300 == 0)
        self.base_ts = 1700000100

    def test_first_tick_initialization(self):
        """測試首筆 Tick 初始化"""
        agg = RealTimeBarAggregator(interval_seconds=300)
        ts = self.base_ts + 20
        closed = agg.feed_tick(timestamp=ts, price=20000.0, volume=2)

        self.assertIsNone(closed)
        self.assertIsNotNone(agg.current_bar)
        self.assertEqual(agg.current_bar.timestamp, self.base_ts)
        self.assertEqual(agg.current_bar.open, 20000.0)
        self.assertEqual(agg.current_bar.high, 20000.0)
        self.assertEqual(agg.current_bar.low, 20000.0)
        self.assertEqual(agg.current_bar.close, 20000.0)
        self.assertEqual(agg.current_bar.volume, 2)
        self.assertFalse(agg.current_bar.is_closed)

    def test_intra_bar_updates(self):
        """測試週期內 High / Low / Close / Volume 增量更新"""
        agg = RealTimeBarAggregator(interval_seconds=300)
        agg.feed_tick(timestamp=self.base_ts + 10, price=20000.0, volume=1)
        agg.feed_tick(timestamp=self.base_ts + 50, price=20050.0, volume=2)  # new high
        agg.feed_tick(timestamp=self.base_ts + 100, price=19980.0, volume=3)  # new low
        agg.feed_tick(timestamp=self.base_ts + 200, price=20020.0, volume=1)  # latest close

        bar = agg.current_bar
        self.assertEqual(bar.timestamp, self.base_ts)
        self.assertEqual(bar.open, 20000.0)
        self.assertEqual(bar.high, 20050.0)
        self.assertEqual(bar.low, 19980.0)
        self.assertEqual(bar.close, 20020.0)
        self.assertEqual(bar.volume, 7)
        self.assertFalse(bar.is_closed)

    def test_bar_close_on_interval_cross(self):
        """測試跨越週期時封閉前一根棒並建立新棒"""
        closed_bars = []
        updated_bars = []

        agg = RealTimeBarAggregator(
            interval_seconds=300,
            on_bar_update=lambda b: updated_bars.append(b.to_dict()),
            on_bar_close=lambda b: closed_bars.append(b.to_dict()),
        )

        # 第一根棒: bucket = self.base_ts
        agg.feed_tick(timestamp=self.base_ts + 50, price=20000.0, volume=2)
        agg.feed_tick(timestamp=self.base_ts + 280, price=20030.0, volume=1)

        self.assertEqual(len(closed_bars), 0)

        # 跨越至第二根棒: bucket = self.base_ts + 300
        closed = agg.feed_tick(timestamp=self.base_ts + 305, price=20040.0, volume=5)

        self.assertIsNotNone(closed)
        self.assertEqual(closed.timestamp, self.base_ts)
        self.assertEqual(closed.close, 20030.0)
        self.assertEqual(closed.volume, 3)
        self.assertTrue(closed.is_closed)

        self.assertEqual(len(closed_bars), 1)
        self.assertEqual(closed_bars[0]["time"], self.base_ts)

        # 當前新棒
        self.assertEqual(agg.current_bar.timestamp, self.base_ts + 300)
        self.assertEqual(agg.current_bar.open, 20040.0)
        self.assertEqual(agg.current_bar.volume, 5)

    def test_force_close_current_bar(self):
        """測試盤別結束時強制封閉當前棒"""
        closed_bars = []
        agg = RealTimeBarAggregator(
            interval_seconds=300,
            on_bar_close=lambda b: closed_bars.append(b),
        )
        agg.feed_tick(timestamp=self.base_ts + 100, price=20100.0, volume=4)

        closed = agg.force_close_current_bar()
        self.assertIsNotNone(closed)
        self.assertEqual(closed.close, 20100.0)
        self.assertTrue(closed.is_closed)
        self.assertEqual(len(closed_bars), 1)
        self.assertIsNone(agg.current_bar)


if __name__ == "__main__":
    unittest.main()
