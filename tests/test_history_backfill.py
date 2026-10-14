"""
歷史回補服務單元測試 (tests/test_history_backfill.py)

驗證：
1. 候選交易日產生規則（排除週末、由新到舊）。
2. 單日處理：Tick → 秒 K → SQLite（symbol 標記、邊界過濾、快取落地與重用）。
3. 配額守門：額度不足時 QUOTA_PAUSED 且不發出任何 API 請求。
4. 續跑與冪等：已完成日期不重抓；EMPTY 日期不重試（除非 retry_empty）。
5. 回補寫入絕不覆蓋既有實體合約資料。
"""

import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from typing import Any, Dict, List

import pandas as pd

from services.storage.sqlite_manager import HISTORY_SYMBOL, SQLiteManager
from services.tick_export.history_backfill import (
    BackfillStateStore,
    HistoricalBackfillService,
    TICK_BYTES,
)


def _ts(s: str) -> int:
    """將時間字串轉為奈秒 Unix 時間戳（與 Shioaji ticks.ts 之格式一致）。"""
    return pd.Timestamp(s).value


class FakeApi:
    """模擬 Shioaji API：可預設各日期之 Tick 與用量回報。"""

    def __init__(self, day_ticks: Dict[str, List[tuple]], used_bytes: int = 0, limit_bytes: int = 500 * 1024 * 1024):
        """
        參數:
            day_ticks (Dict[str, List[tuple]]): 日期 → [(時間字串, 價格, 量), ...]；不存在之日期回傳空 Tick。
            used_bytes (int): usage().bytes 之回報值。
            limit_bytes (int): usage().limit_bytes。
        """
        self.day_ticks = day_ticks
        self.used_bytes = used_bytes
        self.limit_bytes = limit_bytes
        self.ticks_calls: List[str] = []

    def usage(self) -> Any:
        """模擬 api.usage()。"""
        return SimpleNamespace(bytes=self.used_bytes, limit_bytes=self.limit_bytes)

    def ticks(self, contract: Any, date: str, timeout: int = 0) -> Any:
        """模擬 api.ticks()，並記錄呼叫日期。"""
        self.ticks_calls.append(date)
        rows = self.day_ticks.get(date, [])
        return SimpleNamespace(
            ts=[_ts(r[0]) for r in rows],
            close=[float(r[1]) for r in rows],
            volume=[int(r[2]) for r in rows],
        )


class TestHistoryBackfill(unittest.TestCase):
    """HistoricalBackfillService 行為測試。"""

    def setUp(self) -> None:
        """建立隔離之暫存資料庫、快取目錄與狀態檔，並預先寫入一筆既有實體合約資料作為邊界。"""
        self.tmp = tempfile.TemporaryDirectory()
        base = self.tmp.name
        self.db_path = os.path.join(base, "m.db")
        self.cache_dir = os.path.join(base, "cache")
        self.state_path = os.path.join(base, "state.json")

        SQLiteManager(self.db_path).insert_second_bars(
            pd.DataFrame({
                "時間": ["2026-09-15 15:00:00", "2026-09-15 15:00:01"],
                "開盤價": [100.0, 101.0], "最高價": [100.0, 101.0],
                "最低價": [100.0, 101.0], "收盤價": [100.0, 101.0], "成交量": [1, 1],
            }),
            symbol="TMFJ6",
        )
        self.svc = HistoricalBackfillService(
            db_path=self.db_path, cache_dir=self.cache_dir, state_path=self.state_path, reserve_mb=90.0
        )

    def tearDown(self) -> None:
        """清除暫存目錄。"""
        self.tmp.cleanup()

    def _count(self, symbol: str) -> int:
        """回傳資料庫內指定 symbol 之列數。"""
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute("SELECT COUNT(*) FROM tmf10_second_bars WHERE symbol = ?", (symbol,)).fetchone()[0]
        finally:
            conn.close()

    def test_weekday_dates_desc_excludes_weekends(self) -> None:
        """2026-09-11 (週五) ~ 2026-09-16 (週三)：不含 12、13 日週末，且由新到舊。"""
        dates = HistoricalBackfillService.weekday_dates_desc("2026-09-11", "2026-09-16")
        self.assertEqual(dates, ["2026-09-16", "2026-09-15", "2026-09-14", "2026-09-11"])
        self.assertEqual(HistoricalBackfillService.weekday_dates_desc("2026-09-16", "2026-09-11"), [])

    def test_boundary_time_ignores_history_rows(self) -> None:
        """邊界時間僅取非 HISTORY_SYMBOL 之最早時間。"""
        self.assertEqual(self.svc.get_boundary_time(), "2026-09-15 15:00:00")

    def test_process_day_inserts_with_history_symbol_and_caches(self) -> None:
        """單日處理：寫入 HISTORY_SYMBOL、落地快取、第二次改讀快取不再呼叫 API。"""
        api = FakeApi({"2026-09-14": [
            ("2026-09-13 15:00:00.100", 50, 2), ("2026-09-13 15:00:00.700", 52, 3),
            ("2026-09-14 09:00:01.200", 51, 1),
        ]})
        res = self.svc.process_day(api, object(), "2026-09-14", "2026-09-15 15:00:00")
        self.assertEqual(res["status"], "OK")
        self.assertTrue(res["downloaded"])
        self.assertEqual(res["bars"], 2)
        self.assertEqual(self._count(HISTORY_SYMBOL), 2)
        self.assertTrue(os.path.exists(self.svc._cache_path("2026-09-14")))

        res2 = self.svc.process_day(api, object(), "2026-09-14", "2026-09-15 15:00:00")
        self.assertFalse(res2["downloaded"])
        self.assertEqual(api.ticks_calls, ["2026-09-14"])
        self.assertEqual(self._count(HISTORY_SYMBOL), 2, "重複處理應為冪等，不得產生重複列")

    def test_boundary_filter_never_overwrites_existing_contract_rows(self) -> None:
        """與既有資料同秒或更晚之回補秒 K 必須被捨棄，既有 TMFJ6 列保持原值。"""
        api = FakeApi({"2026-09-15": [
            ("2026-09-15 13:00:00.100", 10, 1),
            ("2026-09-15 15:00:00.500", 999, 9),   # 與既有 15:00:00 同秒 → 必須捨棄
        ]})
        res = self.svc.process_day(api, object(), "2026-09-15", self.svc.get_boundary_time())
        self.assertEqual(res["inserted"], 1)
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT close, symbol FROM tmf10_second_bars WHERE time = '2026-09-15 15:00:00'"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row, (100.0, "TMFJ6"))

    def test_run_quota_guard_pauses_without_requests(self) -> None:
        """剩餘額度低於 (預估單日成本 + reserve) 時，必須暫停且不得發出任何 ticks 請求。"""
        limit = 500 * 1024 * 1024
        api = FakeApi({"2026-09-14": [("2026-09-14 09:00:01", 51, 1)]}, used_bytes=limit - 50 * 1024 * 1024)
        rep = self.svc.run(api, object(), start_date="2026-09-14", end_date="2026-09-14")
        self.assertEqual(rep["stop_reason"], "QUOTA_PAUSED")
        self.assertEqual(api.ticks_calls, [])
        self.assertEqual(rep["pending_after"], 1)

    def test_run_resume_skips_ok_and_empty_days(self) -> None:
        """續跑：OK 與 EMPTY 日期不重抓；retry_empty=True 才會重試 EMPTY。"""
        api = FakeApi({"2026-09-14": [("2026-09-14 09:00:01", 51, 1)]})   # 09-11 無資料 → EMPTY
        rep1 = self.svc.run(api, object(), start_date="2026-09-11", end_date="2026-09-14")
        self.assertEqual(rep1["stop_reason"], "COMPLETED")
        self.assertEqual(sorted(api.ticks_calls), ["2026-09-11", "2026-09-14"])
        self.assertEqual(self.svc.state.status_of("2026-09-11"), "EMPTY")
        self.assertEqual(self.svc.state.status_of("2026-09-14"), "OK")

        api.ticks_calls.clear()
        rep2 = self.svc.run(api, object(), start_date="2026-09-11", end_date="2026-09-14")
        self.assertEqual(api.ticks_calls, [])
        self.assertEqual(rep2["processed"], 0)

        rep3 = self.svc.run(api, object(), start_date="2026-09-11", end_date="2026-09-14", retry_empty=True)
        self.assertEqual(api.ticks_calls, ["2026-09-11"])
        self.assertEqual(rep3["processed"], 1)

    def test_run_max_days_limits_downloads(self) -> None:
        """max_days 僅限制「需下載」之日數。"""
        api = FakeApi({
            "2026-09-14": [("2026-09-14 09:00:01", 51, 1)],
            "2026-09-11": [("2026-09-11 09:00:01", 49, 1)],
        })
        rep = self.svc.run(api, object(), start_date="2026-09-11", end_date="2026-09-14", max_days=1)
        self.assertEqual(rep["stop_reason"], "MAX_DAYS")
        self.assertEqual(rep["downloaded_days"], 1)
        self.assertEqual(api.ticks_calls, ["2026-09-14"])

    def test_quota_accounts_for_usage_lag(self) -> None:
        """usage() 反映延遲時，以「連線起點用量 + 本地累計成本」為準，不得低估已用量。"""
        api = FakeApi({}, used_bytes=100)
        self.assertEqual(self.svc.remaining_bytes(api), api.limit_bytes - 100)   # 建立連線基準
        self.svc._session_cost_total = 10 * TICK_BYTES * 1_000_000               # 本地已下載 400 MB，伺服器尚未回報
        self.assertEqual(self.svc.remaining_bytes(api), api.limit_bytes - (100 + 400_000_000))

    def test_quota_does_not_double_count_when_usage_already_reflected(self) -> None:
        """usage() 已即時反映時，取較大值而非相加，避免重複計算而過早暫停。"""
        api = FakeApi({}, used_bytes=0)
        self.svc.remaining_bytes(api)                      # 基準 = 0
        self.svc._session_cost_total = 8_000_000           # 本地累計 8 MB
        api.used_bytes = 8_000_000                         # 伺服器已同步反映同一筆消耗
        self.assertEqual(self.svc.remaining_bytes(api), api.limit_bytes - 8_000_000)

    def test_quota_includes_consumption_by_other_processes(self) -> None:
        """其他程序 (如排程增量同步) 造成之用量由伺服器回報值涵蓋，且大於本地估算時以回報值為準。"""
        api = FakeApi({}, used_bytes=0)
        self.svc.remaining_bytes(api)
        self.svc._session_cost_total = 8_000_000
        api.used_bytes = 30_000_000
        self.assertEqual(self.svc.remaining_bytes(api), api.limit_bytes - 30_000_000)

    def test_state_store_roundtrip_and_corruption_tolerance(self) -> None:
        """狀態檔可往返讀寫；檔案損毀時以空狀態啟動而非中斷。"""
        store = BackfillStateStore(self.state_path)
        store.record("2026-09-14", "OK", ticks=3)
        self.assertEqual(BackfillStateStore(self.state_path).status_of("2026-09-14"), "OK")
        with open(self.state_path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertIsNone(BackfillStateStore(self.state_path).status_of("2026-09-14"))


if __name__ == "__main__":
    unittest.main()
