"""
策略決策層單元測試 (tests/test_strategy.py)

驗證 BollingerPullbackStrategy 決策邏輯：
1. Bar 1 突破布林通道。
2. Bar 2 頂部 1/4 拉回確認成立並發出 BUY_OPEN 進場訊號。
3. 盤中 on_tick 觸價停損。
4. 收盤 on_bar_close ATR 動能停利。
"""

import unittest
import pandas as pd
from services.live.bar_aggregator import BarData
from services.strategy import (
    StrategyConfig,
    BollingerPullbackCoreStrategy,
    HtfMaContinuationStrategy,
    BollingerPullbackStrategy,
    PositionSide,
    SignalType,
)
from services.backtest import (
    BacktestConfig,
    BaselinePullbackEngine,
    HtfMaContinuationEngine,
)


class TestBollingerPullbackStrategy(unittest.TestCase):
    """驗證原版基準核心父類別與方案一繼承子類別之決策邏輯"""

    def setUp(self):
        self.config = StrategyConfig(
            bb_length=20,
            bb_std=2.0,
            atr_length=14,
            atr_tp_multiplier=2.0,
            pullback_ratio=0.25,
        )
        self.core_strategy = BollingerPullbackCoreStrategy(self.config)
        self.plan1_strategy = HtfMaContinuationStrategy(self.config)
        self.strategy = BollingerPullbackStrategy(self.config)

    def test_three_bar_state_machine_long(self):
        """測試原版基準核心三根 K 棒多頭突破與情境一拉回確認進場"""
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
            sig = self.core_strategy.on_bar_close(bar, history, PositionSide.NONE)
            self.assertIsNone(sig)

        # Bar 1: 強勢長紅棒突破布林上軌
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
        sig1 = self.core_strategy.on_bar_close(bar1, history, PositionSide.NONE)
        self.assertIsNone(sig1)
        self.assertEqual(self.core_strategy.pending_signal_side, PositionSide.LONG)

        # Bar 2: 拉回棒 (close <= 20140 且 close >= 20107.5) -> 原版核心確認成立立即發出 BUY_OPEN
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
        sig2 = self.core_strategy.on_bar_close(bar2, history, PositionSide.NONE)

        self.assertIsNotNone(sig2)
        self.assertEqual(sig2.signal_type, SignalType.BUY_OPEN)
        self.assertEqual(sig2.side, PositionSide.LONG)
        self.assertEqual(sig2.price, 20130.0)
        self.assertEqual(sig2.stop_loss_price, 20000.0)

    def test_intra_bar_stop_loss(self):
        """測試盤中 on_tick 觸價停損"""
        sl_price = 19950.0
        entry_price = 20050.0

        sig = self.core_strategy.on_tick(
            timestamp=1700000100,
            price=20000.0,
            current_side=PositionSide.LONG,
            entry_price=entry_price,
            stop_loss_price=sl_price,
        )
        self.assertIsNone(sig)

        sig_sl = self.core_strategy.on_tick(
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

    def test_core_vs_plan1_inheritance_isolation(self):
        """驗證原版核心父類別拒絕情境三追高，而繼承子類別 (方案一) 在 30m & 1h 均線同向上彎時允許進場"""
        history = []
        base_time = 1700006400

        for i in range(260):
            ts = base_time + i * 300
            px = 20000.0 + min(i, 240) * 5.0
            bar = BarData(
                timestamp=ts,
                open=px - 5.0,
                high=px + 10.0,
                low=px - 10.0,
                close=px,
                volume=10,
                is_closed=True,
            )
            history.append(bar)
            self.core_strategy.on_bar_close(bar, history, PositionSide.NONE)
            self.plan1_strategy.on_bar_close(bar, history, PositionSide.NONE)

        self.core_strategy.reset()
        self.plan1_strategy.reset()

        # Bar 1: 長紅棒突破布林上軌
        ts_bar1 = base_time + 260 * 300
        last_px = 20000.0 + 240 * 5.0
        bar1 = BarData(
            timestamp=ts_bar1,
            open=last_px,
            high=last_px + 160.0,
            low=last_px - 10.0,
            close=last_px + 150.0,
            volume=50,
            is_closed=True,
        )
        history.append(bar1)
        self.core_strategy.on_bar_close(bar1, history, PositionSide.NONE)
        self.plan1_strategy.on_bar_close(bar1, history, PositionSide.NONE)

        # Bar 2: 情境三動能延續 (cur_close > signal_bar_close)
        ts_bar2 = base_time + 261 * 300
        bar2 = BarData(
            timestamp=ts_bar2,
            open=bar1.close,
            high=bar1.close + 40.0,
            low=bar1.close - 5.0,
            close=bar1.close + 30.0,
            volume=40,
            is_closed=True,
        )
        history.append(bar2)

        # 1. 原版核心父類別 (BollingerPullbackCoreStrategy) 僅允許情境一，應拒絕情境三 (回傳 None)
        sig_core = self.core_strategy.on_bar_close(bar2, history, PositionSide.NONE)
        self.assertIsNone(sig_core)

        # 2. 方案一繼承子類別 (HtfMaContinuationStrategy) 應觸發 BUY_OPEN
        sig_plan1 = self.plan1_strategy.on_bar_close(bar2, history, PositionSide.NONE)
        self.assertIsNotNone(sig_plan1)
        self.assertEqual(sig_plan1.signal_type, SignalType.BUY_OPEN)
        self.assertIn("30m&1h均線共振延續", sig_plan1.reason)

    def test_backtest_engine_inheritance_and_custom_channel_filter(self):
        """驗證回測引擎父類別 BaselinePullbackEngine 與繼承新增自訂過濾通道子類別之運作"""
        rows = []
        base_ts = pd.Timestamp("2026-05-04 00:00:00")
        for i in range(260):
            px = 20000.0 + min(i, 240) * 5.0
            rows.append(
                {
                    "ts": base_ts + pd.Timedelta(minutes=5 * i),
                    "open": px - 5.0,
                    "high": px + 10.0,
                    "low": px - 10.0,
                    "close": px,
                    "volume": 10,
                }
            )

        last_px = 20000.0 + 240 * 5.0
        # Bar 260 (Bar 1): 長紅突破布林上軌
        rows.append(
            {
                "ts": base_ts + pd.Timedelta(minutes=5 * 260),
                "open": last_px,
                "high": last_px + 160.0,
                "low": last_px - 10.0,
                "close": last_px + 150.0,
                "volume": 50,
            }
        )
        # Bar 261 (Bar 2): 情境三繼續創高 (僅方案一子類別會確認成立)
        rows.append(
            {
                "ts": base_ts + pd.Timedelta(minutes=5 * 261),
                "open": last_px + 150.0,
                "high": last_px + 190.0,
                "low": last_px + 145.0,
                "close": last_px + 180.0,
                "volume": 40,
            }
        )
        # Bar 262 (Bar 3): 開盤進場執行
        rows.append(
            {
                "ts": base_ts + pd.Timedelta(minutes=5 * 262),
                "open": last_px + 180.0,
                "high": last_px + 210.0,
                "low": last_px + 175.0,
                "close": last_px + 200.0,
                "volume": 30,
            }
        )
        df = pd.DataFrame(rows)

        cfg = BacktestConfig(initial_capital=105150.0)
        base_engine = BaselinePullbackEngine(cfg)
        plan1_engine = HtfMaContinuationEngine(cfg)

        m_base = base_engine.run(df.copy())
        m_plan1 = plan1_engine.run(df.copy())

        # 在最後 3 根 K 棒之情境三突破中，plan1_engine 會多進場 1 筆交易
        self.assertEqual(m_plan1.total_trades, m_base.total_trades + 1)

        # 測試以繼承方式新增「自訂過濾通道」子類別 (阻擋所有進場)
        class BlockedChannelFilterEngine(HtfMaContinuationEngine):
            def _pass_entry_filters(self, i: int, target_side: PositionSide, cur_close: float) -> bool:
                return False

        blocked_engine = BlockedChannelFilterEngine(cfg)
        m_blocked = blocked_engine.run(df.copy())
        self.assertEqual(m_blocked.total_trades, 0)


if __name__ == "__main__":
    unittest.main()
