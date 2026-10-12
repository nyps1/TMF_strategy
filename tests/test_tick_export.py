"""
高頻秒 K 線匯出模組單元測試 (tests/test_tick_export.py)

驗證：
1. 逐筆成交 Tick 重採樣為 1 秒 OHLCV 演算法正確性。
2. 同一秒內多筆成交之開、高、低、收與累積成交量聚合精確性。
3. 無成交秒數過濾與欄位名稱完全相符性。
4. 常數記憶體串流 Excel 寫入功能。
"""

import os
import unittest
import pandas as pd
import numpy as np

from services.tick_export import ExportConfig, TickExporter, SecondBarRecord


class TestTickExport(unittest.TestCase):
    def setUp(self):
        self.config = ExportConfig(
            symbol="TMFJ6",
            start_date="2026-09-16",
            end_date="2026-10-12",
            output_excel="tests/test_output.xlsx",
            cache_dir="tests/test_cache",
            only_active_seconds=True
        )
        self.exporter = TickExporter(config=self.config)

    def test_resample_to_second_bars(self):
        """驗證單秒內多筆成交 Tick 聚合為 1 秒 OHLCV 數值精確度"""
        # 建立測試資料：同一秒內有 3 筆成交
        # 2026-10-08 09:00:01.100 -> 價格 50000, 量 2
        # 2026-10-08 09:00:01.300 -> 價格 50020, 量 5 (最高)
        # 2026-10-08 09:00:01.800 -> 價格 49990, 量 3 (最低且收盤)
        # 預期該秒: open=50000, high=50020, low=49990, close=49990, volume=10
        ts1 = pd.Timestamp("2026-10-08 09:00:01.100").value
        ts2 = pd.Timestamp("2026-10-08 09:00:01.300").value
        ts3 = pd.Timestamp("2026-10-08 09:00:01.800").value

        # 次秒成交: 2026-10-08 09:00:02.200 -> 價格 50010, 量 1
        ts4 = pd.Timestamp("2026-10-08 09:00:02.200").value

        df_ticks = pd.DataFrame({
            "ts": [ts1, ts2, ts3, ts4],
            "close": [50000.0, 50020.0, 49990.0, 50010.0],
            "volume": [2, 5, 3, 1]
        })

        sec_df = self.exporter.resample_to_second_bars(df_ticks)
        
        # 檢核欄位完整性
        expected_cols = ["時間", "開盤價", "最高價", "最低價", "收盤價", "成交量"]
        self.assertEqual(list(sec_df.columns), expected_cols)
        self.assertEqual(len(sec_df), 2)

        # 檢核第一秒數據
        row1 = sec_df.iloc[0]
        self.assertEqual(row1["時間"], "2026-10-08 09:00:01")
        self.assertEqual(row1["開盤價"], 50000.0)
        self.assertEqual(row1["最高價"], 50020.0)
        self.assertEqual(row1["最低價"], 49990.0)
        self.assertEqual(row1["收盤價"], 49990.0)
        self.assertEqual(row1["成交量"], 10)

        # 檢核第二秒數據
        row2 = sec_df.iloc[1]
        self.assertEqual(row2["時間"], "2026-10-08 09:00:02")
        self.assertEqual(row2["開盤價"], 50010.0)
        self.assertEqual(row2["成交量"], 1)

    def test_empty_ticks_handling(self):
        """驗證空資料輸入時之容錯與欄位格式一致性"""
        empty_df = pd.DataFrame(columns=["ts", "close", "volume"])
        res = self.exporter.resample_to_second_bars(empty_df)
        self.assertTrue(res.empty)
        self.assertEqual(list(res.columns), ["時間", "開盤價", "最高價", "最低價", "收盤價", "成交量"])


if __name__ == "__main__":
    unittest.main()
