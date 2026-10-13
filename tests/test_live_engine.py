"""
即時交易引擎單元測試 (tests/test_live_engine.py)

驗證 LiveTradingEngine：
1. 接收 Tick 動態聚合。
2. 突破拉回進場自動下單與 safe_qty 風控。
3. 盤中 Tick 觸價停損平倉。
4. 損益計算與手續費、滑價扣除。
"""

import unittest
from services.strategy.models import StrategyConfig, PositionSide
from services.strategy.bollinger_pullback import BollingerPullbackStrategy
from services.live.engine import LiveTradingEngine


class TestLiveTradingEngine(unittest.TestCase):
    """驗證即時交易與風控引擎"""

    def setUp(self):
        self.config = StrategyConfig(
            bb_length=5,
            bb_std=1.5,
            atr_length=5,
            initial_capital=100000.0,
            max_leverage=5.0,
            margin_req=30000.0,
            fee_per_contract=10.0,
            tax_rate=0.00002,
            slippage_points=1.0,
            pullback_ratio=0.25,
        )
        self.strategy = BollingerPullbackStrategy(self.config)
        self.emitted_events = []
        self.engine = LiveTradingEngine(
            strategy=self.strategy,
            on_event=lambda ev_type, data: self.emitted_events.append((ev_type, data)),
        )

    def test_live_engine_flow(self):
        base_time = 1700000100

        # 送入 6 根穩定的 K 棒建構指標 (每根 300 秒)
        for i in range(6):
            ts = base_time + i * 300
            self.engine.on_tick(timestamp=ts + 10, price=20000.0, volume=5)
            self.engine.on_tick(timestamp=ts + 290, price=20000.0, volume=5)

        # Bar 1 (bucket = base_time + 6 * 300): 向上暴衝突破
        # open = 20020, close = 20140, body = 120, q1 = 20140 - 0.25 * 120 = 20110
        ts_b1 = base_time + 6 * 300
        self.engine.on_tick(timestamp=ts_b1 + 10, price=20020.0, volume=10)
        self.engine.on_tick(timestamp=ts_b1 + 150, price=20150.0, volume=30)
        self.engine.on_tick(timestamp=ts_b1 + 290, price=20140.0, volume=10)

        # Bar 2 (bucket = base_time + 7 * 300): 頂部拉回確認
        # close = 20130 <= 20140 且 close >= 20110 -> 滿足確認
        ts_b2 = base_time + 7 * 300
        self.engine.on_tick(timestamp=ts_b2 + 10, price=20140.0, volume=10)
        self.engine.on_tick(timestamp=ts_b2 + 100, price=20120.0, volume=20)
        self.engine.on_tick(timestamp=ts_b2 + 290, price=20130.0, volume=10)

        # Bar 3 第一筆 Tick 到達 (促使 Bar 2 閉合並觸發拉回確認進場)
        ts_b3 = base_time + 8 * 300
        self.engine.on_tick(timestamp=ts_b3 + 5, price=20132.0, volume=10)

        status_after_entry = self.engine.get_status()
        self.assertEqual(status_after_entry["position"]["side"], "LONG")
        # safe_qty 應大於 0 (本金 100,000 / 保證金 30,000 = 3 口)
        self.assertGreater(status_after_entry["position"]["qty"], 0)
        # 進場價需含 1 點滑價 (20130 + 1 = 20131)
        self.assertEqual(status_after_entry["position"]["entry_price"], 20131.0)

        # 模擬盤中觸價停損：價格急殺至 19900 (低於停損價 20020)
        self.engine.on_tick(timestamp=ts_b3 + 50, price=19900.0, volume=50)

        status_after_sl = self.engine.get_status()
        self.assertEqual(status_after_sl["position"]["side"], "NONE")
        self.assertEqual(len(self.engine.completed_trades), 1)

        trade = self.engine.completed_trades[0]
        self.assertEqual(trade["side"], "LONG")
        self.assertLess(trade["net_pnl"], 0)  # 虧損平倉
        self.assertEqual(trade["exit_reason"], "盤中觸價停損 (Intra-bar Stop Loss)")


if __name__ == "__main__":
    unittest.main()
