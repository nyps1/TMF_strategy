"""
每日同步近月合約處理單元測試 (tests/test_daily_sync_contract.py)

驗證：
1. 同步一律請求近月連續合約 TMFR1，不再寫死 TMFJ6 / TMF202610。
2. 寫入 SQLite 時依換月日曆逐列標記實體合約，跨越到期日換月點的批次會拆成兩個合約。
3. 合約清單缺少 TMFR1 時自動手動建構，不因清單內容而中斷同步。
"""

import os
import tempfile
import unittest
from typing import Any, List
from unittest.mock import MagicMock

import pandas as pd

from services.storage import SQLiteManager
from services.tick_export import DailyDataSyncService


def _fake_api(found: bool = True) -> Any:
    """
    建立假 Shioaji API，記錄 Contracts.Futures.TMF.get 的查詢參數。

    參數:
        found (bool): True 時 get() 回傳具 code 屬性之合約物件；False 時回傳 None。

    返回:
        Any: 具 Contracts.Futures.TMF.get 的 MagicMock。
    """
    api = MagicMock()
    contract = MagicMock()
    contract.code = "TMFR1"
    api.Contracts.Futures.TMF.get.return_value = contract if found else None
    return api


class TestNearMonthContract(unittest.TestCase):
    """近月連續合約解析與逐列標記。"""

    def setUp(self) -> None:
        """建立隔離之暫存路徑與服務實體。"""
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "m.db")
        self.svc = DailyDataSyncService(
            db_path=self.db_path,
            excel_path=os.path.join(self.tmp.name, "daily.xlsx"),
            parquet_path=os.path.join(self.tmp.name, "p.parquet"),
            cache_dir=os.path.join(self.tmp.name, "cache"),
        )

    def tearDown(self) -> None:
        """清除暫存目錄。"""
        self.tmp.cleanup()

    def _symbols(self) -> List[tuple]:
        """讀回 SQLite 內各列之 (時間, symbol)。"""
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute("SELECT time, symbol FROM tmf10_second_bars ORDER BY time;").fetchall()
        finally:
            conn.close()

    def test_default_contract_is_continuous_near_month(self) -> None:
        """Tick 匯出設定與預設合約皆為 TMFR1，不再有寫死之月份合約。"""
        self.assertEqual(DailyDataSyncService.NEAR_MONTH_SYMBOL, "TMFR1")
        self.assertEqual(self.svc.tick_exporter.config.symbol, "TMFR1")

    def test_resolve_uses_contract_list_when_present(self) -> None:
        """合約清單內有 TMFR1 時直接使用，且僅以 TMFR1 查詢。"""
        api = _fake_api(found=True)
        contract = self.svc.resolve_near_month_contract(api)
        self.assertEqual(contract.code, "TMFR1")
        api.Contracts.Futures.TMF.get.assert_called_once_with("TMFR1")

    def test_resolve_builds_contract_when_list_lacks_it(self) -> None:
        """清單缺少 TMFR1 (例如輕量登入) 時手動建構等價合約。"""
        contract = self.svc.resolve_near_month_contract(_fake_api(found=False))
        self.assertEqual(contract.code, "TMFR1")

    def test_append_to_sqlite_tags_each_row_by_roll_calendar(self) -> None:
        """未指定 symbol 時，到期日 13:29:58 屬 10 月合約、15:00:00 起屬 11 月合約。"""
        df = pd.DataFrame({
            "時間": ["2026-10-14 10:00:00", "2026-10-21 13:29:58", "2026-10-21 15:00:00", "2026-10-22 09:00:00"],
            "開盤價": [1.0, 2.0, 3.0, 4.0], "最高價": [1.0, 2.0, 3.0, 4.0],
            "最低價": [1.0, 2.0, 3.0, 4.0], "收盤價": [1.0, 2.0, 3.0, 4.0], "成交量": [1, 1, 1, 1],
        })
        inserted = self.svc.append_to_sqlite(df)
        self.assertEqual(inserted, 4)
        self.assertEqual([s for _, s in self._symbols()], ["TMFJ6", "TMFJ6", "TMFK6", "TMFK6"])

    def test_append_to_sqlite_explicit_symbol_still_honoured(self) -> None:
        """明確指定 symbol 時維持舊行為，所有列使用該代碼。"""
        df = pd.DataFrame({
            "時間": ["2026-10-14 10:00:00"], "開盤價": [1.0], "最高價": [1.0],
            "最低價": [1.0], "收盤價": [1.0], "成交量": [1],
        })
        self.svc.append_to_sqlite(df, symbol="TMFR1")
        self.assertEqual(self._symbols(), [("2026-10-14 10:00:00", "TMFR1")])

    def test_sync_across_expiry_requests_tmfr1_and_tags_both_contracts(self) -> None:
        """
        端到端 (Shioaji 以假物件取代)：到期日 10-21 之後的同步仍可執行，
        請求的合約為 TMFR1，且資料依換月日曆拆為 TMFJ6 與 TMFK6。
        """
        ticks = pd.DataFrame({
            "ts": pd.to_datetime(["2026-10-21 13:29:58", "2026-10-21 15:00:01", "2026-10-22 09:00:00"]),
            "close": [100.0, 110.0, 111.0],
            "volume": [1, 2, 3],
        })
        self.svc.tick_exporter.fetch_and_cache_ticks = MagicMock(return_value=ticks)
        api = _fake_api(found=True)

        result = self.svc.sync(api, target_date="2026-10-22")

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["contract"], "TMFR1")
        self.assertEqual(result["physical_contracts"], ["TMFJ6", "TMFK6"])
        requested_contract = self.svc.tick_exporter.fetch_and_cache_ticks.call_args.kwargs["contract"]
        self.assertEqual(requested_contract.code, "TMFR1")
        self.assertEqual([s for _, s in self._symbols()], ["TMFJ6", "TMFK6", "TMFK6"])
        self.assertEqual(SQLiteManager(self.db_path).get_summary()["symbol_count"], 2)


if __name__ == "__main__":
    unittest.main()
