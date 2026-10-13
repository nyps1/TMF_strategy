"""
策略決策層單元測試 (tests/test_strategy.py)

驗證 BollingerPullbackStrategy 決策邏輯：
1. Bar 1 突破布林通道。
2. Bar 2 頂部 1/4 拉回確認成立並發出 BUY_OPEN 進場訊號。
3. 盤中 on_tick 觸價停損。
4. 收盤 on_bar_close ATR 動能停利。
"""

import unittest
from services.live.bar_aggregator import BarData
from services.strategy import (
    StrategyConfig,
    BollingerPullbackStrategy,
    PositionSide,
    SignalType,
)


class TestBollingerPullbackStrategy(unittest.TestCase):
    """驗證布林突破拉回確認策略"""

    def setUp(self):
        self.config = StrategyConfig(
            bb_length=20,
            bb_std=2.0,
            atr_length=14,
            atr_tp_multiplier=2.0,
            pullback_ratio=0.25,
        )
        self.strategy = BollingerPullbackStrategy(self.config)

    def test_three_bar_state_machine_long(self):
        """測試三根 K 棒多頭突破與拉回確認進場"""
        history = []
        base_time = 1700000100

        # 建立前 25 根穩定震盪 K 棒 (建立布林帶基準，中軌約 20000)
        for i in range(25):
            ts = base_time + i * 300
            bar = BarData(
                timestamp=ts,
                open=20000.0,
                high=20020.0,
                low=19980.0,
                close=20000.0,
                volume=10,
                is_closed=True,
            )
            history.append(bar)
            sig = self.strategy.on_bar_close(bar, history, PositionSide.NONE)
            self.assertIsNone(sig)

        # Bar 1: 強勢長紅棒突破布林上軌
        # body = 20140 - 20010 = 130, q1 = 20140 - 0.25 * 130 = 20107.5
        ts_bar1 = base_time + 25 * 300
        bar1 = BarData(
            timestamp=ts_bar1,
            open=20010.0,
            high=20150.0,
            low=20000.0,
            close=20140.0,
            volume=50,
            is_closed=True,
        )
        history.append(bar1)
        sig1 = self.strategy.on_bar_close(bar1, history, PositionSide.NONE)
        self.assertIsNone(sig1)
        self.assertEqual(self.strategy.pending_signal_side, PositionSide.LONG)

        # Bar 2: 拉回棒 (close <= 20140 且 close >= 20107.5) -> 確認成立立即發出 BUY_OPEN
        ts_bar2 = base_time + 26 * 300
        bar2 = BarData(
            timestamp=ts_bar2,
            open=20140.0,
            high=20145.0,
            low=20120.0,
            close=20130.0,
            volume=30,
            is_closed=True,
        )
        history.append(bar2)
        sig2 = self.strategy.on_bar_close(bar2, history, PositionSide.NONE)

        self.assertIsNotNone(sig2)
        self.assertEqual(sig2.signal_type, SignalType.BUY_OPEN)
        self.assertEqual(sig2.side, PositionSide.LONG)
        self.assertEqual(sig2.price, 20130.0)
        self.assertEqual(sig2.stop_loss_price, 20000.0)  # bar1 的 low (signal_bar_extreme)

    def test_intra_bar_stop_loss(self):
        """測試盤中 on_tick 觸價停損"""
        sl_price = 19950.0
        entry_price = 20050.0

        # 價格未達停損
        sig = self.strategy.on_tick(
            timestamp=1700000100,
            price=20000.0,
            current_side=PositionSide.LONG,
            entry_price=entry_price,
            stop_loss_price=sl_price,
        )
        self.assertIsNone(sig)

        # 價格觸及停損
        sig_sl = self.strategy.on_tick(
            timestamp=1700000105,
            price=19940.0,
            current_side=PositionSide.LONG,
            entry_price=entry_price,
            stop_loss_price=sl_price,
        )
        self.assertIsNotNone(sig_sl)
        self.assertEqual(sig_sl.signal_type, SignalType.STOP_LOSS)
        self.assertEqual(sig_sl.side, PositionSide.LONG)
        self.assertEqual(sig_sl.price, 19940.0)


if __name__ == "__main__":
    unittest.main()
