"""
SQLite 儲存管理模組單元測試 (tests/test_sqlite_storage.py)

驗證：
1. 資料表建立與欄位綱要正確性。
2. 批次寫入效能與資料完整性。
3. 冪等性（重複寫入不重複累計）。
4. 時間區間索引查詢功能。
5. 統計摘要計算準確性。
"""

import os
import unittest
import pandas as pd

from services.storage import SQLiteManager


class TestSQLiteStorage(unittest.TestCase):
    def setUp(self):
        self.test_db = "tests/test_market_data.db"
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
        self.manager = SQLiteManager(db_path=self.test_db)

    def tearDown(self):
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
        # 清理 WAL 與 SHM 暫存檔
        for ext in ["-wal", "-shm"]:
            f = self.test_db + ext
            if os.path.exists(f):
                os.remove(f)

    def test_batch_insert_and_query(self):
        """驗證秒 K 線批次寫入與區間檢索邏輯"""
        df = pd.DataFrame({
            "時間": ["2026-10-08 09:00:01", "2026-10-08 09:00:02", "2026-10-08 09:00:03"],
            "開盤價": [50000.0, 50010.0, 50020.0],
            "最高價": [50020.0, 50030.0, 50040.0],
            "最低價": [49990.0, 50000.0, 50010.0],
            "收盤價": [50010.0, 50025.0, 50035.0],
            "成交量": [15, 20, 35]
        })

        inserted = self.manager.insert_second_bars(df, symbol="TMFJ6")
        self.assertEqual(inserted, 3)

        summary = self.manager.get_summary()
        self.assertEqual(summary["total_rows"], 3)
        self.assertEqual(summary["total_volume"], 70)
        self.assertEqual(summary["earliest_time"], "2026-10-08 09:00:01")
        self.assertEqual(summary["latest_time"], "2026-10-08 09:00:03")

        # 區間查詢驗證
        res_df = self.manager.query_bars_by_time_range(
            start_time="2026-10-08 09:00:01",
            end_time="2026-10-08 09:00:02",
            symbol="TMFJ6"
        )
        self.assertEqual(len(res_df), 2)
        self.assertEqual(res_df["時間"].iloc[0], "2026-10-08 09:00:01")
        self.assertEqual(res_df["收盤價"].iloc[0], 50010.0)

    def test_idempotent_reinsert(self):
        """驗證重複寫入具備冪等性（不重複增加筆數）"""
        df = pd.DataFrame({
            "時間": ["2026-10-08 09:00:01"],
            "開盤價": [50000.0],
            "最高價": [50020.0],
            "最低價": [49990.0],
            "收盤價": [50010.0],
            "成交量": [15]
        })

        self.manager.insert_second_bars(df, symbol="TMFJ6")
        # 再次寫入相同時間戳（但更新成交量為 25）
        df_updated = df.copy()
        df_updated["成交量"] = 25
        self.manager.insert_second_bars(df_updated, symbol="TMFJ6")

        summary = self.manager.get_summary()
        self.assertEqual(summary["total_rows"], 1)
        self.assertEqual(summary["total_volume"], 25)

    def test_default_query_is_continuous_near_month_chain(self):
        """預設 (TMFR1) 為近月連續資料：跨越換月之各合約依時間串接；指定實體代碼則僅回傳該合約。"""
        def _frame(times):
            n = len(times)
            return pd.DataFrame({
                "時間": times, "開盤價": [1.0] * n, "最高價": [2.0] * n,
                "最低價": [0.5] * n, "收盤價": [1.5] * n, "成交量": [1] * n,
            })

        self.manager.insert_second_bars(_frame(["2026-10-21 13:29:58"]), symbol="TMFJ6")
        self.manager.insert_second_bars(_frame(["2026-10-21 15:00:01"]), symbol="TMFK6")

        whole_chain = self.manager.query_bars_by_time_range("2026-10-21 00:00:00", "2026-10-22 00:00:00")
        self.assertEqual(list(whole_chain["時間"]), ["2026-10-21 13:29:58", "2026-10-21 15:00:01"])

        only_oct = self.manager.query_bars_by_time_range(
            "2026-10-21 00:00:00", "2026-10-22 00:00:00", symbol="TMFJ6"
        )
        self.assertEqual(list(only_oct["時間"]), ["2026-10-21 13:29:58"])


if __name__ == "__main__":
    unittest.main()
