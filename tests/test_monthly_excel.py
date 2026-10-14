"""
月份分頁 Excel 匯出與歷史範圍隔離單元測試 (tests/test_monthly_excel.py)

驗證：
1. 工作表命名（26-09、溢位 26-09 (2)）。
2. 依日曆月份切分、列數與起訖時間正確、標題列完整。
3. 單月超過列數上限時自動溢位分頁且不遺漏資料。
4. 舊版單一工作表活頁簿於改版前被備份為 .legacy.bak。
5. KlineService 預設排除歷史回補區段，include_history=True 才納入。
"""

import os
import shutil
import tempfile
import unittest

import openpyxl
import pandas as pd

from services.storage import KlineService
from services.storage.sqlite_manager import HISTORY_SYMBOL, SQLiteManager
from services.tick_export.monthly_excel import EXCEL_HEADERS, MonthlyExcelExporter


def _bars(times: list, base: float = 100.0) -> pd.DataFrame:
    """建立測試用秒 K DataFrame。"""
    n = len(times)
    return pd.DataFrame({
        "時間": times,
        "開盤價": [base + i for i in range(n)], "最高價": [base + i + 1 for i in range(n)],
        "最低價": [base + i - 1 for i in range(n)], "收盤價": [base + i for i in range(n)],
        "成交量": [i + 1 for i in range(n)],
    })


class TestMonthlyExcel(unittest.TestCase):
    """MonthlyExcelExporter 行為測試。"""

    def setUp(self) -> None:
        """建立含跨月資料之暫存資料庫。"""
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "m.db")
        self.xlsx = os.path.join(self.tmp.name, "out.xlsx")
        mgr = SQLiteManager(self.db_path)
        mgr.insert_second_bars(_bars([
            "2026-08-31 15:00:00", "2026-08-31 15:00:01",
        ]), symbol=HISTORY_SYMBOL)
        mgr.insert_second_bars(_bars([
            "2026-09-01 09:00:00", "2026-09-30 23:59:59",
            "2026-10-01 00:00:00", "2026-10-02 09:00:00", "2026-10-02 09:00:01",
        ]), symbol="TMFJ6")

    def tearDown(self) -> None:
        """清除暫存目錄。"""
        self.tmp.cleanup()

    def test_month_sheet_name(self) -> None:
        """工作表名稱以 YY-MM 呈現，溢位加註 (n)。"""
        self.assertEqual(MonthlyExcelExporter.month_sheet_name("2026-09"), "26-09")
        self.assertEqual(MonthlyExcelExporter.month_sheet_name("2026-09", 2), "26-09 (2)")
        self.assertEqual(MonthlyExcelExporter.month_sheet_name("2025-01"), "25-01")

    def test_export_splits_by_calendar_month(self) -> None:
        """依日曆月份切分：08 月 2 列、09 月 2 列、10 月 3 列，月邊界 23:59:59 / 00:00:00 不重不漏。"""
        summary = MonthlyExcelExporter(self.db_path, self.xlsx).export()
        self.assertEqual(summary["total_rows"], 7)

        wb = openpyxl.load_workbook(self.xlsx, read_only=True)
        try:
            self.assertEqual(wb.sheetnames, ["26-08", "26-09", "26-10"])
            expected = {"26-08": 2, "26-09": 2, "26-10": 3}
            for name, n_rows in expected.items():
                rows = list(wb[name].iter_rows(values_only=True))
                self.assertEqual(list(rows[0]), EXCEL_HEADERS)
                self.assertEqual(len(rows) - 1, n_rows, name)
            sep_rows = list(wb["26-09"].iter_rows(values_only=True))
            self.assertEqual(sep_rows[1][0], "2026-09-01 09:00:00")
            self.assertEqual(sep_rows[2][0], "2026-09-30 23:59:59")
        finally:
            wb.close()

    def test_overflow_creates_continuation_sheet(self) -> None:
        """單月資料超過 max_rows_per_sheet 時，自動以「26-10 (2)」接續且總列數不變。"""
        summary = MonthlyExcelExporter(self.db_path, self.xlsx, max_rows_per_sheet=2).export()
        names = [s["sheet"] for s in summary["sheets"]]
        self.assertEqual(names, ["26-08", "26-09", "26-10", "26-10 (2)"])
        self.assertEqual(summary["total_rows"], 7)
        self.assertEqual(sum(s["rows"] for s in summary["sheets"]), 7)

    def test_export_is_idempotent(self) -> None:
        """重複匯出結果一致（冪等）。"""
        first = MonthlyExcelExporter(self.db_path, self.xlsx).export()
        second = MonthlyExcelExporter(self.db_path, self.xlsx).export()
        self.assertEqual(
            [(s["sheet"], s["rows"]) for s in first["sheets"]],
            [(s["sheet"], s["rows"]) for s in second["sheets"]],
        )

    def test_legacy_single_sheet_workbook_is_backed_up(self) -> None:
        """舊版單一工作表活頁簿於改版前備份為 .legacy.bak，且備份內容保持舊版型。"""
        legacy = openpyxl.Workbook()
        legacy.active.title = "秒K歷史紀錄"
        legacy.active.append(EXCEL_HEADERS)
        legacy.save(self.xlsx)

        summary = MonthlyExcelExporter(self.db_path, self.xlsx).export()
        self.assertTrue(summary["legacy_backup"])
        self.assertTrue(os.path.exists(f"{self.xlsx}.legacy.bak"))
        inspect_copy = os.path.join(self.tmp.name, "legacy_inspect.xlsx")
        shutil.copy2(f"{self.xlsx}.legacy.bak", inspect_copy)
        bak = openpyxl.load_workbook(inspect_copy, read_only=True)
        try:
            self.assertEqual(bak.sheetnames, ["秒K歷史紀錄"])
        finally:
            bak.close()

    def test_empty_database_fails_fast(self) -> None:
        """資料庫無資料時快速失敗並附 Status + Root Cause + Suggested Fix。"""
        empty_db = os.path.join(self.tmp.name, "empty.db")
        SQLiteManager(empty_db)
        with self.assertRaises(ValueError) as ctx:
            MonthlyExcelExporter(empty_db, self.xlsx).export()
        self.assertIn("Root Cause", str(ctx.exception))
        self.assertIn("Suggested Fix", str(ctx.exception))


class TestHistoryScopeIsolation(unittest.TestCase):
    """KlineService 歷史範圍隔離測試（保護儀表板與回測不因歷史回補而膨脹）。"""

    def setUp(self) -> None:
        """建立同時含歷史回補 (TMFR1) 與實體合約 (TMFJ6) 之資料庫。"""
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "m.db")
        mgr = SQLiteManager(self.db_path)
        mgr.insert_second_bars(_bars(["2026-09-14 09:00:00", "2026-09-14 09:01:00"]), symbol=HISTORY_SYMBOL)
        mgr.insert_second_bars(_bars(["2026-09-16 09:00:00", "2026-09-16 09:01:00"]), symbol="TMFJ6")

    def tearDown(self) -> None:
        """清除暫存目錄。"""
        self.tmp.cleanup()

    def test_default_excludes_history(self) -> None:
        """預設僅載入實體合約資料，範圍與回補前一致。"""
        bars = KlineService(self.db_path).get_kbars("1m")
        self.assertEqual([b["datetime"][:10] for b in bars], ["2026-09-16", "2026-09-16"])

    def test_include_history_loads_everything(self) -> None:
        """include_history=True 才納入歷史回補區段。"""
        bars = KlineService(self.db_path, include_history=True).get_kbars("1m")
        self.assertEqual(len(bars), 4)
        self.assertEqual(bars[0]["datetime"][:10], "2026-09-14")


class TestDailySyncExcelPath(unittest.TestCase):
    """DailyDataSyncService.append_to_excel 之排程路徑回歸測試。"""

    def setUp(self) -> None:
        """建立隔離之暫存路徑與服務實體。"""
        from services.tick_export import DailyDataSyncService
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "m.db")
        self.xlsx = os.path.join(self.tmp.name, "daily.xlsx")
        self.svc = DailyDataSyncService(
            db_path=self.db_path, excel_path=self.xlsx,
            parquet_path=os.path.join(self.tmp.name, "p.parquet"),
            cache_dir=os.path.join(self.tmp.name, "cache"),
        )

    def tearDown(self) -> None:
        """清除暫存目錄。"""
        self.tmp.cleanup()

    def test_append_to_excel_rebuilds_month_sheets_from_sqlite(self) -> None:
        """排程流程 (SQLite 先寫入 → append_to_excel) 產出一月一工作表，回傳新增列數。"""
        history = _bars(["2026-09-14 09:00:00", "2026-09-14 09:00:01"])
        new_rows = _bars(["2026-10-14 04:59:57", "2026-10-14 04:59:58"], base=200.0)
        SQLiteManager(self.db_path).insert_second_bars(history, symbol=HISTORY_SYMBOL)
        self.svc.append_to_sqlite(new_rows, symbol="TMFJ6")

        appended = self.svc.append_to_excel(new_rows)
        self.assertEqual(appended, 2)

        wb = openpyxl.load_workbook(self.xlsx, read_only=True)
        try:
            self.assertEqual(wb.sheetnames, ["26-09", "26-10"])
            self.assertEqual(len(list(wb["26-10"].iter_rows(values_only=True))) - 1, 2)
        finally:
            wb.close()

    def test_append_to_excel_noop_when_no_new_rows(self) -> None:
        """無新增資料時不得觸碰 Excel 檔案。"""
        self.assertEqual(self.svc.append_to_excel(_bars([])), 0)
        self.assertFalse(os.path.exists(self.xlsx))


if __name__ == "__main__":
    unittest.main()
