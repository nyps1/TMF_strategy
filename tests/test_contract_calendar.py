"""
換月日曆單元測試 (tests/test_contract_calendar.py)

驗證「時間 → 合約月份 → 商品代碼」之換月規則，涵蓋到期日邊界、跨年與例外到期日。
測試期望值皆來自 Shioaji TMFR1 實際 Tick 之觀察 (例如 2026-08-19 日盤最後一筆 13:29:58)。
"""

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.tick_export import contract_calendar as cal  # noqa: E402


class TestThirdWednesday(unittest.TestCase):
    """第三個星期三計算。"""

    def test_known_expiry_dates(self) -> None:
        """對照期交所公告與實際資料觀察到之到期日。"""
        expected = {
            (2025, 1): date(2025, 1, 15),
            (2025, 10): date(2025, 10, 15),
            (2026, 7): date(2026, 7, 15),
            (2026, 8): date(2026, 8, 19),
            (2026, 9): date(2026, 9, 16),
            (2026, 10): date(2026, 10, 21),
        }
        for (year, month), want in expected.items():
            with self.subTest(year=year, month=month):
                self.assertEqual(cal.third_wednesday(year, month), want)
                self.assertEqual(want.weekday(), 2)

    def test_override_takes_precedence(self) -> None:
        """EXPIRY_OVERRIDES 之停市順延優先於預設規則。"""
        cal.roll_cutoff.cache_clear()
        cal.EXPIRY_OVERRIDES[(2026, 10)] = date(2026, 10, 22)
        try:
            self.assertEqual(cal.expiry_date(2026, 10), date(2026, 10, 22))
            self.assertEqual(cal.contract_code_for("2026-10-21 16:00:00"), "TMFJ6")
            self.assertEqual(cal.contract_code_for("2026-10-22 15:00:00"), "TMFK6")
        finally:
            cal.EXPIRY_OVERRIDES.pop((2026, 10), None)
            cal.roll_cutoff.cache_clear()


class TestContractResolution(unittest.TestCase):
    """時間 → 合約代碼。"""

    def test_regular_days(self) -> None:
        """月中一般日屬於當月合約。"""
        self.assertEqual(cal.contract_code_for("2026-10-14 10:00:00"), "TMFJ6")
        self.assertEqual(cal.contract_code_for("2026-07-22 15:00:00"), "TMFH6")

    def test_expiry_day_boundary(self) -> None:
        """到期日日盤 (含 13:29:58) 仍屬到期合約，15:00 夜盤起為次月合約。"""
        self.assertEqual(cal.contract_code_for("2026-08-19 13:29:58"), "TMFH6")
        self.assertEqual(cal.contract_code_for("2026-08-19 14:59:59"), "TMFH6")
        self.assertEqual(cal.contract_code_for("2026-08-19 15:00:00"), "TMFI6")
        self.assertEqual(cal.contract_code_for("2026-10-21 13:29:59"), "TMFJ6")
        self.assertEqual(cal.contract_code_for("2026-10-21 15:00:00"), "TMFK6")

    def test_sept_expiry_is_one_day_after_current_junction(self) -> None:
        """9 月合約到期日為 09-16：規則上 09-15 15:00 夜盤仍屬 9 月合約。"""
        self.assertEqual(cal.contract_code_for("2026-09-15 13:45:00"), "TMFI6")
        self.assertEqual(cal.contract_code_for("2026-09-15 15:00:00"), "TMFI6")
        self.assertEqual(cal.contract_code_for("2026-09-16 15:00:00"), "TMFJ6")

    def test_year_rollover(self) -> None:
        """12 月合約到期後換為隔年 1 月合約，年份個位數同步進位。"""
        self.assertEqual(cal.contract_code_for("2026-12-16 14:59:59"), "TMFL6")
        self.assertEqual(cal.contract_code_for("2026-12-16 15:00:00"), "TMFA7")
        self.assertEqual(cal.contract_code_for("2027-01-05 09:00:00"), "TMFA7")

    def test_accepts_datetime_and_string(self) -> None:
        """字串與 datetime 輸入結果一致。"""
        from datetime import datetime
        self.assertEqual(
            cal.contract_code_for(datetime(2026, 10, 14, 10, 0, 0)),
            cal.contract_code_for("2026-10-14 10:00:00")
        )


class TestCodeParsing(unittest.TestCase):
    """商品代碼解析與時間區間。"""

    def test_parse_physical_codes(self) -> None:
        """實體合約代碼解析為 (年, 月)。"""
        self.assertEqual(cal.parse_contract_code("TMFJ6"), (2026, 10))
        self.assertEqual(cal.parse_contract_code("TMFA7"), (2027, 1))
        self.assertEqual(cal.parse_contract_code("TMFH4"), (2024, 8))
        self.assertEqual(cal.parse_contract_code("TMFC3"), (2033, 3))

    def test_parse_rejects_continuous_and_garbage(self) -> None:
        """連續合約 TMFR1 與非法字串回傳 None。"""
        for code in ("TMFR1", "TMFR2", "MXFJ6", "", "TMFJ", None):
            with self.subTest(code=code):
                self.assertIsNone(cal.parse_contract_code(code))

    def test_code_roundtrip(self) -> None:
        """代碼 → 年月 → 代碼可往返。"""
        for year, month in ((2024, 8), (2026, 10), (2027, 1), (2026, 12)):
            code = cal.contract_code_for_month(year, month)
            self.assertEqual(cal.parse_contract_code(code), (year, month))

    def test_time_bounds_are_contiguous(self) -> None:
        """相鄰合約之時間區間首尾相接且無重疊。"""
        lo, hi = cal.contract_time_bounds(2026, 10)
        self.assertEqual((lo, hi), ("2026-09-16 15:00:00", "2026-10-21 15:00:00"))
        next_lo, _ = cal.contract_time_bounds(2026, 11)
        self.assertEqual(next_lo, hi)
        jan_lo, _ = cal.contract_time_bounds(2027, 1)
        self.assertEqual(jan_lo, "2026-12-16 15:00:00")

    def test_bounds_agree_with_resolution(self) -> None:
        """區間下界屬於該合約、下界前一秒屬於前一合約。"""
        lo, hi = cal.contract_time_bounds(2026, 10)
        self.assertEqual(cal.contract_month_for(lo), (2026, 10))
        self.assertEqual(cal.contract_month_for("2026-09-16 14:59:59"), (2026, 9))
        self.assertEqual(cal.contract_month_for(hi), (2026, 11))


if __name__ == "__main__":
    unittest.main()
