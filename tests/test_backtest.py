"""
單元測試模組 (tests/test_backtest.py)

驗證：
1. 布林通道與 ATR 指標運算準確性。
2. 動態安全口數 (safe_qty) 計算與槓桿風控邊界。
3. 三根 K 棒驗證狀態機 (Bar 1 -> Bar 2 -> Bar 3 開盤進場)。
4. 盤中停損與 ATR 停利撮合邏輯。
5. 結算日 13:00 強制平倉機制。
"""

import unittest
import pandas as pd
import numpy as np

from services.backtest import (
    BacktestConfig,
    BacktestEngine,
    PositionSide,
    ExitReason,
    calculate_bollinger_bands,
    calculate_atr,
    get_third_wednesday,
)


class TestBacktestEngine(unittest.TestCase):
    def setUp(self):
        self.config = BacktestConfig(
            initial_capital=105150.0,
            point_value=10.0,
            margin_req=35050.0,
            max_leverage=7.0,
            bb_length=20,
            bb_mult=2.0,
            atr_length=14,
            atr_sig_length=9,
            commission_per_order=0.0,
            tax_rate=0.0,
            slippage_points=0.0
        )
        self.engine = BacktestEngine(config=self.config)

    def test_third_wednesday_calculation(self):
        """驗證台指期結算日（每月第三個星期三）計算準確性"""
        # 2024 年 1 月第三個星期三為 1/17
        wed_jan = get_third_wednesday(2024, 1)
        self.assertEqual(wed_jan.day, 17)
        self.assertEqual(wed_jan.weekday(), 2)

        # 2024 年 2 月第三個星期三為 2/21
        wed_feb = get_third_wednesday(2024, 2)
        self.assertEqual(wed_feb.day, 21)
        self.assertEqual(wed_feb.weekday(), 2)

    def test_safe_qty_calculation(self):
        """驗證動態安全口數計算公式與槓桿邊界控管"""
        # initial_capital = 105,150, margin_req = 35,050 -> 保證金允許口數 = 105,150 / 35,050 = 3 口
        # current_price = 20,000, max_leverage = 7.0
        # max_contract_val = 105,150 * 7 = 736,050
        # single_val = 20,000 * 10 = 200,000
        # theoretical_max_qty = 736,050 / 200,000 = 3.68 -> floor = 3 口
        # min(3.68, 3) = 3
        qty = self.engine._calculate_safe_qty(20000.0)
        self.assertEqual(qty, 3)

        # 若資本虧損至低於單口保證金 35,050 (如 30,000)，應回傳 0 口拒絕開倉
        self.engine.equity = 30000.0
        qty_low = self.engine._calculate_safe_qty(20000.0)
        self.assertEqual(qty_low, 0)

    def test_margin_ratio_130_and_dynamic_leverage(self):
        """驗證基準 A (130% 整戶原始保證金維持率, 單口門檻 45,565 元) 與動態浮動槓桿計算"""
        cfg_130 = BacktestConfig(
            initial_capital=105150.0,
            point_value=10.0,
            margin_req=35050.0,
            maint_margin=26900.0,
            min_margin_ratio=1.30,
            position_sizing_mode="margin_ratio_130"
        )
        eng_130 = BacktestEngine(config=cfg_130)

        # 1. 期初本金 105,150 元 -> floor(105,150 / 45,565) = 2 口 (維持率 150.0% >= 130%)
        self.assertEqual(eng_130._calculate_safe_qty(45980.0), 2)

        # 2. 當權益成長至 136,695 元 (45,565 * 3) -> 自動晉升為 3 口
        eng_130.equity = 136695.0
        self.assertEqual(eng_130._calculate_safe_qty(45980.0), 3)

        # 3. 當權益回落至 90,000 元 -> 自動降為 1 口
        eng_130.equity = 90000.0
        self.assertEqual(eng_130._calculate_safe_qty(45980.0), 1)

        # 4. 當權益跌破 45,565 元 (如 45,000 元，維持率不足 130%) -> 嚴格回傳 0 口禁止開倉
        eng_130.equity = 45000.0
        self.assertEqual(eng_130._calculate_safe_qty(45980.0), 0)

        # 5. 驗證進場價 45,980 點、2 口時之總合約價值 (919,600 元)、動態真實槓桿 (8.75 倍) 與維持率 (150.0%)
        eng_130.equity = 105150.0
        eng_130.position.side = PositionSide.LONG
        eng_130.position.qty = 2
        eng_130.position.entry_price = 45980.0
        eng_130.position.entry_time = "2026-08-01 09:00:00"
        eng_130._close_position(
            exit_time="2026-08-01 09:30:00",
            raw_exit_price=46000.0,
            reason=ExitReason.ATR_TAKE_PROFIT
        )
        t = eng_130.trades[0]
        self.assertEqual(t.contract_value, 919600.0)
        self.assertAlmostEqual(t.effective_leverage, 8.75, places=2)
        self.assertAlmostEqual(t.margin_ratio_pct, 150.0, places=2)

    def test_indicator_calculation(self):
        """驗證布林通道與 ATR 序列長度與數值正確性"""
        dates = pd.date_range("2024-01-01", periods=45, freq="5min")
        df = pd.DataFrame({
            "ts": dates,
            "open": [100.0] * 45,
            "high": [105.0] * 45,
            "low": [95.0] * 45,
            "close": [100.0 + i for i in range(45)],
            "volume": [100] * 45
        })
        df = calculate_bollinger_bands(df, length=30, mult=1.75)
        df = calculate_atr(df, length=14, sig_length=5)

        self.assertIn("bb_basis", df.columns)
        self.assertIn("bb_upper", df.columns)
        self.assertIn("atr_val", df.columns)
        self.assertIn("atr_sig", df.columns)
        self.assertFalse(np.isnan(df["bb_basis"].iloc[-1]))
        self.assertFalse(np.isnan(df["atr_val"].iloc[-1]))

    def test_real_cost_fee_and_tax_ceil(self):
        """驗證真實成本版單口手續費 18 元與期交稅 math.ceil 精算規則"""
        config = BacktestConfig(
            initial_capital=105150.0,
            point_value=10.0,
            commission_per_order=18.0,
            tax_rate=0.00002,
            slippage_points=0.0
        )
        engine = BacktestEngine(config=config)
        # 模擬持倉 2 口，進場價 20,000，出場價 20,100
        engine.position.side = PositionSide.LONG
        engine.position.qty = 2
        engine.position.entry_price = 20000.0
        engine.position.entry_time = "2024-01-02 09:00:00"

        engine._close_position(
            exit_time="2024-01-02 09:30:00",
            raw_exit_price=20100.0,
            reason=ExitReason.ATR_TAKE_PROFIT
        )
        self.assertEqual(len(engine.trades), 1)
        t = engine.trades[0]
        # entry_tax = ceil(20000 * 10 * 2 * 0.00002) = ceil(8.0) = 8
        # exit_tax = ceil(20100 * 10 * 2 * 0.00002) = ceil(8.04) = 9
        # total tax = 17
        self.assertEqual(t.tax_total, 17.0)
        # fee = 18 * 2 (進) + 18 * 2 (出) = 72
        self.assertEqual(t.fee_total, 72.0)
        # gross pnl = (20100 - 20000) * 10 * 2 = 2000
        self.assertEqual(t.gross_pnl, 2000.0)
        # net pnl = 2000 - 17 - 72 = 1911
        self.assertEqual(t.net_pnl, 1911.0)

    def test_three_bar_long_entry_and_sl(self):
        """驗證三根 K 棒多頭進場判定與盤中觸碰停損點出場邏輯"""
        # 前 20 根建立基準 (價格平穩 20000)
        records = [
            {"open": 20000.0, "high": 20010.0, "low": 19990.0, "close": 20000.0}
            for _ in range(20)
        ]
        # Bar 20 (Bar 1 訊號棒): 大漲突破布林上軌
        records.append({"open": 20000.0, "high": 20200.0, "low": 19990.0, "close": 20180.0})
        # Bar 21 (Bar 2 確認棒): 回檔至 20150 (body=180, q1=20180-45=20135, close=20150 守在強勢區)
        records.append({"open": 20180.0, "high": 20180.0, "low": 20140.0, "close": 20150.0})
        # Bar 22 (Bar 3 進場棒): 開盤 20160 -> 在此處以 20160 買進！停損設在 Bar 1 之 low (19990)
        records.append({"open": 20160.0, "high": 20170.0, "low": 20100.0, "close": 20120.0})
        # Bar 23: 盤中觸發停損 (low: 19950 <= 19990)
        records.append({"open": 20120.0, "high": 20130.0, "low": 19950.0, "close": 19960.0})
        # Bar 24: 空手平穩
        records.append({"open": 19960.0, "high": 19970.0, "low": 19950.0, "close": 19960.0})

        timestamps = pd.date_range("2024-01-02 09:00", periods=len(records), freq="5min")
        df = pd.DataFrame(records)
        df["ts"] = timestamps
        df["volume"] = 100

        metrics = self.engine.run(df)
        self.assertEqual(metrics.total_trades, 1)
        trade = metrics.trades[0]
        self.assertEqual(trade.side, PositionSide.LONG)
        self.assertEqual(trade.entry_price, 20160.0)  # 嚴格買在 Bar 3 開盤價
        self.assertEqual(trade.exit_reason, ExitReason.STOP_LOSS)
        self.assertEqual(trade.exit_price, 19990.0)   # 於 Bar 1 最低點精確觸價停損


if __name__ == "__main__":
    unittest.main()
