"""
微台10 歷史 Tick 回補服務 (services/tick_export/history_backfill.py)

職責：
1. 以 Shioaji 近月連續合約 (TMFR1) 逐交易日回補歷史 Tick，聚合為 1 秒 OHLCV 後寫入 SQLite
   (symbol 標記為 HISTORY_SYMBOL)，並於 Parquet 快取原始 Tick 以利重跑與稽核。
2. 內建「每日流量配額守門」(Quota Guard)：Shioaji 每日流量上限預設 500 MB，每筆 Tick 計 40 Bytes，
   且 api.usage() 的回報值有延遲；本服務取「伺服器回報值」與「連線起點回報值 + 本地累計成本」之較大者為已用量，
   並保留 reserve 額度給每日 07:00 / 14:00 增量同步，額度不足時優雅暫停 (QUOTA_PAUSED)。
3. 以 JSON 狀態檔 (data/backfill_state.json) 記錄每個交易日之處理結果，達成可中斷、可續跑、冪等。
4. 由新到舊回補，確保已完成區段永遠與既有資料連續；寫入前以邊界時間過濾，絕不覆蓋既有實體合約資料。

遵循 PEP 8、Python Type Hints 與專案工程規範。
"""

import os
import json
import time
import sqlite3
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from services.storage.sqlite_manager import SQLiteManager, HISTORY_SYMBOL
from services.tick_export.exporter import TickExporter
from services.tick_export.models import ExportConfig

logger = logging.getLogger(__name__)

TICK_BYTES: int = 40                       # 實測：每筆 Tick 流量計費 40 Bytes
DEFAULT_START_DATE: str = "2024-07-29"     # 微型臺指期貨上市日 (實測更早日期 Shioaji 回傳 0 筆)
DEFAULT_NEXT_DAY_COST_BYTES: int = 14 * 1024 * 1024   # 尚無樣本時，單日成本保守估計 14 MB (約 35 萬筆 Tick)


class BackfillStateStore:
    """
    回補進度狀態檔存取器 (JSON，原子寫入)。
    """

    def __init__(self, state_path: str) -> None:
        """
        初始化狀態存取器並載入既有進度。

        參數:
            state_path (str): JSON 狀態檔路徑。
        """
        self.state_path = state_path
        self.days: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        """自磁碟載入狀態；檔案不存在或損毀時以空狀態啟動並記錄警告。"""
        if not os.path.exists(self.state_path):
            return
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            self.days = dict(payload.get("days", {}))
        except (OSError, ValueError) as e:
            logger.warning(f"回補狀態檔讀取失敗 ({e})，將以空狀態啟動 (已快取之 Tick 仍會被沿用)。")
            self.days = {}

    def save(self) -> None:
        """以暫存檔 + os.replace 原子寫入狀態檔。"""
        os.makedirs(os.path.dirname(os.path.abspath(self.state_path)), exist_ok=True)
        tmp = f"{self.state_path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"symbol": HISTORY_SYMBOL, "days": self.days}, f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, self.state_path)

    def status_of(self, date_str: str) -> Optional[str]:
        """
        查詢單日處理狀態。

        參數:
            date_str (str): 交易日 YYYY-MM-DD。

        返回:
            Optional[str]: OK / EMPTY / FAILED；尚未處理回傳 None。
        """
        entry = self.days.get(date_str)
        return entry.get("status") if entry else None

    def record(self, date_str: str, status: str, **detail: Any) -> None:
        """
        記錄單日處理結果並立即持久化。

        參數:
            date_str (str): 交易日 YYYY-MM-DD。
            status (str): OK / EMPTY / FAILED。
            **detail: 其他統計欄位 (ticks、bars、inserted、first、last、error 等)。
        """
        self.days[date_str] = {
            "status": status,
            "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **detail
        }
        self.save()


class HistoricalBackfillService:
    """
    歷史 Tick 回補服務：配額守門 + 可續跑 + 冪等寫入。
    """

    def __init__(
        self,
        db_path: str = "data/market_data.db",
        cache_dir: str = "data/ticks_cache",
        state_path: str = "data/backfill_state.json",
        reserve_mb: float = 40.0
    ) -> None:
        """
        初始化回補服務。

        參數:
            db_path (str): SQLite 資料庫路徑。
            cache_dir (str): 原始 Tick Parquet 快取目錄。
            state_path (str): 回補進度 JSON 路徑。
            reserve_mb (float): 每日須保留給增量同步排程之流量額度 (MB)，預設 40.0 MB。
        """
        self.db_path = db_path
        self.cache_dir = cache_dir
        self.reserve_bytes = int(reserve_mb * 1024 * 1024)
        self.state = BackfillStateStore(state_path)
        self.sqlite_manager = SQLiteManager(db_path=db_path)
        self.tick_exporter = TickExporter(
            config=ExportConfig(symbol=HISTORY_SYMBOL, cache_dir=cache_dir, only_active_seconds=True)
        )
        self._recent_costs: List[int] = []                 # 最近數日實測流量成本 (Bytes)
        self._session_base_bytes: Optional[int] = None     # 本次連線首次查詢時之伺服器回報用量 (Bytes)
        self._session_cost_total: int = 0                  # 本次連線已下載之本地累計成本 (Bytes)

    # ------------------------------------------------------------------ #
    # 日期規劃
    # ------------------------------------------------------------------ #
    @staticmethod
    def weekday_dates_desc(start_date: str, end_date: str) -> List[str]:
        """
        產生區間內所有平日 (週一至週五) 之候選交易日，由新到舊排序。
        週末不列入：週五夜盤歸屬次一交易日，已涵蓋於週一查詢結果；國定假日由 API 回傳空資料自動判定。

        參數:
            start_date (str): 起日 (含) YYYY-MM-DD。
            end_date (str): 迄日 (含) YYYY-MM-DD。

        返回:
            List[str]: 由新到舊之日期字串清單；若 start > end 回傳空清單。
        """
        d_start = datetime.strptime(start_date, "%Y-%m-%d").date()
        d_end = datetime.strptime(end_date, "%Y-%m-%d").date()
        dates: List[str] = []
        cur = d_end
        while cur >= d_start:
            if cur.weekday() < 5:
                dates.append(cur.strftime("%Y-%m-%d"))
            cur -= timedelta(days=1)
        return dates

    def get_boundary_time(self) -> Optional[str]:
        """
        取得「非歷史回補」資料之最早時間，作為回補寫入的上界 (不含)。
        回補資料一律需早於此時間，以免覆蓋每日增量同步寫入之實體合約資料。

        返回:
            Optional[str]: 邊界時間字串；資料庫內尚無實體合約資料時回傳 None。
        """
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        try:
            row = conn.execute(
                "SELECT MIN(time) FROM tmf10_second_bars WHERE symbol <> ?;", (HISTORY_SYMBOL,)
            ).fetchone()
            return row[0] if row and row[0] else None
        finally:
            conn.close()

    # ------------------------------------------------------------------ #
    # 配額守門
    # ------------------------------------------------------------------ #
    def remaining_bytes(self, api: Any) -> int:
        """
        推算今日剩餘流量 (Bytes)。

        實測 api.usage() 的 bytes 反映有延遲 (剛完成之請求可能數秒後才計入)，且延遲時間不固定。
        因此取兩種估計中較大者作為「已用量」，兩種延遲情形下皆不低估、亦不重複計算：
        1. 伺服器回報值 usage.bytes (涵蓋其他程序，如排程增量同步之消耗)。
        2. 本次連線起點之回報值 + 本次已下載之本地累計成本 (每筆 Tick 40 Bytes，精確)。

        參數:
            api: 已登入之 Shioaji 實例。

        返回:
            int: 估計剩餘 Bytes (可能為負值，代表已超額)。
        """
        usage = api.usage()
        if self._session_base_bytes is None:
            self._session_base_bytes = int(usage.bytes)
        used_est = max(int(usage.bytes), self._session_base_bytes + self._session_cost_total)
        return int(usage.limit_bytes - used_est)

    def _estimated_next_cost(self) -> int:
        """
        估計下一個交易日之流量成本：取最近 5 日平均成本 × 1.15；尚無樣本時採保守預設值。

        返回:
            int: 預估 Bytes。
        """
        if not self._recent_costs:
            return DEFAULT_NEXT_DAY_COST_BYTES
        recent = self._recent_costs[-5:]
        avg_cost = sum(recent) / len(recent)
        return int(avg_cost * 1.15)

    def has_quota_for_next_day(self, api: Any) -> bool:
        """
        判斷剩餘額度是否足以再抓一日並仍保有 reserve。

        參數:
            api: 已登入之 Shioaji 實例。

        返回:
            bool: True 代表可繼續；False 代表應暫停等待次日重置。
        """
        remaining = self.remaining_bytes(api)
        need = self._estimated_next_cost() + self.reserve_bytes
        ok = remaining >= need
        logger.info(
            f"配額檢查: 剩餘約 {remaining / 1048576:.1f} MB，下一日預估 {self._estimated_next_cost() / 1048576:.1f} MB "
            f"+ 保留 {self.reserve_bytes / 1048576:.0f} MB → {'繼續' if ok else '暫停'}"
        )
        return ok

    # ------------------------------------------------------------------ #
    # 單日處理
    # ------------------------------------------------------------------ #
    def _cache_path(self, date_str: str) -> str:
        """
        取得單日原始 Tick 快取路徑。

        參數:
            date_str (str): 交易日 YYYY-MM-DD。

        返回:
            str: Parquet 快取檔路徑。
        """
        return os.path.join(self.cache_dir, f"{HISTORY_SYMBOL}_{date_str}_ticks.parquet")

    def _load_cached_ticks(self, date_str: str) -> Optional[pd.DataFrame]:
        """
        讀取單日 Tick 快取。歷史交易日之內容不會再變動，因此直接信任快取 (不套用盤中不完整判斷)。

        參數:
            date_str (str): 交易日 YYYY-MM-DD。

        返回:
            Optional[pd.DataFrame]: 快取 DataFrame；不存在或損毀時回傳 None。
        """
        path = self._cache_path(date_str)
        if not os.path.exists(path):
            return None
        try:
            df = pd.read_parquet(path)
            return df if not df.empty else None
        except Exception as e:  # noqa: BLE001
            logger.warning(f"{date_str} 快取檔損毀 ({e})，將重新向 API 下載。")
            return None

    def _download_ticks(self, api: Any, contract: Any, date_str: str, max_retries: int = 3) -> pd.DataFrame:
        """
        向 Shioaji 下載單日 Tick (含重試)，成功後以原子方式寫入 Parquet 快取。

        參數:
            api: 已登入之 Shioaji 實例。
            contract: TMFR1 合約物件。
            date_str (str): 交易日 YYYY-MM-DD。
            max_retries (int): 最大重試次數。

        返回:
            pd.DataFrame: 包含 ts、close、volume 之 Tick；無資料回傳空 DataFrame。

        例外:
            RuntimeError: 重試耗盡仍失敗時拋出，由呼叫端標記為 FAILED。
        """
        last_err: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            try:
                ticks = api.ticks(contract, date=date_str, timeout=60000)
                n = len(ticks.close) if hasattr(ticks, "close") else 0
                if n == 0:
                    return pd.DataFrame(columns=["ts", "close", "volume"])
                df = pd.DataFrame({"ts": ticks.ts, "close": ticks.close, "volume": ticks.volume})
                os.makedirs(self.cache_dir, exist_ok=True)
                tmp = f"{self._cache_path(date_str)}.tmp"
                df.to_parquet(tmp, index=False)
                os.replace(tmp, self._cache_path(date_str))
                return df
            except Exception as e:  # noqa: BLE001
                last_err = e
                logger.warning(f"{date_str} 下載失敗 (第 {attempt}/{max_retries} 次): {e}")
                time.sleep(2.0 * attempt)
        raise RuntimeError(f"{date_str} 下載重試 {max_retries} 次仍失敗: {last_err}")

    def process_day(self, api: Any, contract: Any, date_str: str, boundary_time: Optional[str]) -> Dict[str, Any]:
        """
        處理單一交易日：取得 Tick (快取優先) → 聚合秒 K → 邊界過濾 → 寫入 SQLite → 記錄狀態。

        參數:
            api: 已登入之 Shioaji 實例。
            contract: TMFR1 合約物件。
            date_str (str): 交易日 YYYY-MM-DD。
            boundary_time (Optional[str]): 寫入上界 (不含)，晚於等於此時間之秒 K 將被捨棄。

        返回:
            Dict[str, Any]: 單日結果，包含 status、ticks、bars、inserted、downloaded (是否實際耗用流量)。
        """
        cached = self._load_cached_ticks(date_str)
        downloaded = False
        if cached is not None:
            ticks_df = cached
        else:
            try:
                ticks_df = self._download_ticks(api, contract, date_str)
            except RuntimeError as e:
                self.state.record(date_str, "FAILED", error=str(e))
                return {"date": date_str, "status": "FAILED", "error": str(e), "downloaded": False}
            downloaded = True
            cost = len(ticks_df) * TICK_BYTES
            self._session_cost_total += cost
            if cost > 0:
                self._recent_costs.append(cost)

        if ticks_df.empty:
            self.state.record(date_str, "EMPTY", ticks=0)
            return {"date": date_str, "status": "EMPTY", "ticks": 0, "bars": 0, "inserted": 0, "downloaded": downloaded}

        sec_df = self.tick_exporter.resample_to_second_bars(ticks_df.copy())
        raw_bars = len(sec_df)
        if boundary_time:
            sec_df = sec_df[sec_df["時間"] < boundary_time].copy()

        inserted = self.sqlite_manager.insert_second_bars(sec_df, symbol=HISTORY_SYMBOL) if not sec_df.empty else 0
        detail = {
            "ticks": int(len(ticks_df)),
            "bars": int(raw_bars),
            "inserted": int(inserted),
            "first": sec_df["時間"].min() if not sec_df.empty else None,
            "last": sec_df["時間"].max() if not sec_df.empty else None,
        }
        self.state.record(date_str, "OK", **detail)
        return {"date": date_str, "status": "OK", "downloaded": downloaded, **detail}

    # ------------------------------------------------------------------ #
    # 主流程
    # ------------------------------------------------------------------ #
    def pending_dates(
        self,
        start_date: str,
        end_date: str,
        retry_empty: bool = False
    ) -> List[str]:
        """
        計算尚待處理之交易日 (由新到舊)。已 OK 者一律跳過；EMPTY 預設跳過；FAILED 與未處理者納入。

        參數:
            start_date (str): 起日 (含)。
            end_date (str): 迄日 (含)。
            retry_empty (bool): 是否重試先前判定為 EMPTY 之日期。

        返回:
            List[str]: 待處理日期清單。
        """
        pending: List[str] = []
        for d in self.weekday_dates_desc(start_date, end_date):
            st = self.state.status_of(d)
            if st == "OK" or (st == "EMPTY" and not retry_empty):
                continue
            pending.append(d)
        return pending

    def run(
        self,
        api: Any,
        contract: Any,
        start_date: str = DEFAULT_START_DATE,
        end_date: Optional[str] = None,
        max_days: Optional[int] = None,
        retry_empty: bool = False
    ) -> Dict[str, Any]:
        """
        執行回補主流程。額度不足時優雅暫停，下次執行自動續跑。

        參數:
            api: 已登入之 Shioaji 實例。
            contract: TMFR1 合約物件。
            start_date (str): 回補起日 (含)，預設為微台上市日。
            end_date (Optional[str]): 回補迄日 (含)；預設為邊界時間所在日期。
            max_days (Optional[int]): 本次最多處理之「需下載」交易日數 (測試/試跑用)。
            retry_empty (bool): 是否重試先前判定為 EMPTY 之日期。

        返回:
            Dict[str, Any]: 執行報告，包含 stop_reason、processed、downloaded_days、inserted_rows、
                pending_after、quota_remaining_mb、boundary_time 與逐日結果。
        """
        t0 = time.time()
        boundary = self.get_boundary_time()
        eff_end = end_date or (boundary[:10] if boundary else date.today().strftime("%Y-%m-%d"))
        pending = self.pending_dates(start_date, eff_end, retry_empty=retry_empty)
        logger.info(
            f"回補區間 {start_date} ~ {eff_end}，邊界時間 {boundary}，待處理交易日 {len(pending)} 天 (由新到舊)。"
        )

        results: List[Dict[str, Any]] = []
        stop_reason = "COMPLETED"
        downloaded_days = 0
        inserted_rows = 0

        for idx, d_str in enumerate(pending):
            has_cache = os.path.exists(self._cache_path(d_str))
            if not has_cache:
                if max_days is not None and downloaded_days >= max_days:
                    stop_reason = "MAX_DAYS"
                    break
                if not self.has_quota_for_next_day(api):
                    stop_reason = "QUOTA_PAUSED"
                    break
                time.sleep(0.3)  # 行情查詢頻率限制 (5 秒 50 次) 防護

            res = self.process_day(api, contract, d_str, boundary)
            results.append(res)
            if res.get("downloaded"):
                downloaded_days += 1
            inserted_rows += int(res.get("inserted", 0))
            logger.info(
                f"[{idx + 1}/{len(pending)}] {d_str}: {res['status']} | ticks={res.get('ticks', 0):,} "
                f"bars={res.get('bars', 0):,} inserted={res.get('inserted', 0):,}"
                f"{' | 本次下載' if res.get('downloaded') else ''}"
            )

        pending_after = len(self.pending_dates(start_date, eff_end, retry_empty=retry_empty))
        try:
            quota_mb = round(self.remaining_bytes(api) / 1048576, 1)
        except Exception:  # noqa: BLE001
            quota_mb = None

        report = {
            "stop_reason": stop_reason,
            "range": [start_date, eff_end],
            "boundary_time": boundary,
            "processed": len(results),
            "downloaded_days": downloaded_days,
            "inserted_rows": inserted_rows,
            "failed_days": [r["date"] for r in results if r["status"] == "FAILED"],
            "pending_after": pending_after,
            "quota_remaining_mb": quota_mb,
            "duration_seconds": round(time.time() - t0, 1),
            "results": results
        }
        logger.info(
            f"回補結束: {stop_reason} | 本次處理 {len(results)} 天 (下載 {downloaded_days} 天)、"
            f"寫入 {inserted_rows:,} 列、剩餘待處理 {pending_after} 天、剩餘額度約 {quota_mb} MB"
        )
        return report
