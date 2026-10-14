"""
K 線重採樣與時序資料服務 (services/storage/kline_service.py)

從 SQLite 資料庫 (tmf10_second_bars) 讀取微台10逐秒 K 線，
透過記憶體快取與多級時間分桶 (Multi-level Resampling) 演算法，
極速聚合為 1m, 2m, 3m, 5m, 15m, 30m, 45m, 1h, 2h, 4h, 1D 等多維度 K 線。
"""

import os
import sqlite3
import logging
from typing import Dict, List, Any, Optional
import pandas as pd
import numpy as np

from .sqlite_manager import HISTORY_SYMBOL

logger = logging.getLogger("KlineService")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


class KlineService:
    """
    提供高頻秒 K 線重採樣為多週期 K 線之高效能服務。
    採用記憶體快取與階層式聚合，將次級時間聚合延遲控制在毫秒級。
    """

    SUPPORTED_INTERVALS: Dict[str, str] = {
        "1m": "1min",
        "2m": "2min",
        "3m": "3min",
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "45m": "45min",
        "1h": "1h",
        "2h": "2h",
        "4h": "4h",
        "1d": "1D",
        "1D": "1D"
    }

    INTERVAL_LABELS: Dict[str, str] = {
        "1m": "1 分",
        "2m": "2 分",
        "3m": "3 分",
        "5m": "5 分",
        "15m": "15 分",
        "30m": "30 分",
        "45m": "45 分",
        "1h": "1 小時",
        "2h": "2 小時",
        "4h": "4 小時",
        "1d": "1 日",
        "1D": "1 日"
    }

    def __init__(
        self,
        db_path: str = "data/market_data.db",
        include_history: bool = False,
        since: Optional[str] = None
    ) -> None:
        """
        初始化 K 線服務。

        參數:
            db_path (str): SQLite 資料庫檔案路徑。
            include_history (bool): 是否納入歷史回補區段 (symbol == HISTORY_SYMBOL，Shioaji 近月連續合約)。
                預設 False，僅載入每日增量同步之實體合約資料，使儀表板與回測範圍不因歷史回補而膨脹；
                設為 True 則載入全部歷史 (資料量可達千萬筆秒 K，需預留足夠記憶體)。
            since (Optional[str]): 時間下界 (含)，格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS。
                僅載入此時間之後的秒 K；回補持續往 2024 年推進時，可用來固定儀表板與回測的時間窗。
                預設 None 表示不設下界。
        """
        self.db_path = os.path.abspath(db_path)
        self.include_history = include_history
        self.since = since
        self._cache_1m: Optional[pd.DataFrame] = None
        self._cache_intervals: Dict[str, List[Dict[str, Any]]] = {}

    def _get_connection(self) -> sqlite3.Connection:
        """建立具備 WAL 與記憶體快取配置之 SQLite 連線。"""
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"Status: ERROR | Root Cause: 資料庫檔案不存在: {self.db_path} | Suggested Fix: 請先執行匯入作業。")
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA cache_size = -64000;")
        return conn

    def _load_and_resample_1m(self) -> pd.DataFrame:
        """
        從 SQLite 資料庫載入全部秒 K 線，並重採樣為 1 分鐘基準 K 線。
        後續所有週期均可基於 1 分鐘基準快速派生。

        返回:
            pd.DataFrame: 具備 DatetimeIndex 之 1 分鐘 OHLCV DataFrame。
        """
        if self._cache_1m is not None:
            return self._cache_1m

        scope = "含歷史回補" if self.include_history else "不含歷史回補 (僅實體合約增量資料)"
        if self.since:
            scope += f"、自 {self.since} 起"
        logger.info(f"從 SQLite 資料庫載入秒 K 線 ({scope}): {self.db_path}...")

        predicates: List[str] = []
        params_list: List[Any] = []
        if not self.include_history:
            predicates.append("symbol <> ?")
            params_list.append(HISTORY_SYMBOL)
        if self.since:
            predicates.append("time >= ?")
            params_list.append(self.since)
        where_clause = f"WHERE {' AND '.join(predicates)}" if predicates else ""
        query = (
            "SELECT time, open, high, low, close, volume FROM tmf10_second_bars "
            f"{where_clause} ORDER BY time ASC;"
        )
        with self._get_connection() as conn:
            df = pd.read_sql_query(query, conn, params=tuple(params_list))

        if df.empty:
            raise ValueError("Status: ERROR | Root Cause: tmf10_second_bars 資料表為空 | Suggested Fix: 確認資料庫寫入成功。")

        df["time"] = pd.to_datetime(df["time"])
        df.set_index("time", inplace=True)

        logger.info(f"已讀取 {len(df):,} 筆秒 K 線，執行 1 分鐘時間分桶...")
        k1m = df.resample("1min", closed="left", label="left").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum"
        }).dropna()

        # 確保型別正確
        k1m["open"] = k1m["open"].astype(float)
        k1m["high"] = k1m["high"].astype(float)
        k1m["low"] = k1m["low"].astype(float)
        k1m["close"] = k1m["close"].astype(float)
        k1m["volume"] = k1m["volume"].astype(int)

        self._cache_1m = k1m
        logger.info(f"1 分鐘基準 K 線構建完成，共計 {len(k1m):,} 根 K 棒。")
        return self._cache_1m

    def get_kbars(self, interval: str = "5m", limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        獲取指定週期的 K 線資料，格式相容於 TradingView Lightweight Charts。

        參數:
            interval (str): 週期代碼 ('1m', '2m', '3m', '5m', '15m', '30m', '45m', '1h', '2h', '4h', '1d', '1D')。
            limit (Optional[int]): 最大回傳筆數 (預設 None 全數回傳)。

        返回:
            List[Dict[str, Any]]: K 線字典列表，各元素格式為:
                {
                    "time": UNIX_TIMESTAMP (int),
                    "datetime": "YYYY-MM-DD HH:MM:SS",
                    "open": float,
                    "high": float,
                    "low": float,
                    "close": float,
                    "volume": int
                }
        """
        norm_interval = interval.lower()
        if norm_interval not in self.SUPPORTED_INTERVALS:
            raise ValueError(
                f"Status: ERROR | Root Cause: 不支援的週期 '{interval}' | "
                f"Suggested Fix: 請使用下列支援之週期: {list(self.SUPPORTED_INTERVALS.keys())}。"
            )

        # 檢查快取
        cache_key = norm_interval
        if cache_key in self._cache_intervals:
            data = self._cache_intervals[cache_key]
            return data[-limit:] if limit else data

        base_1m = self._load_and_resample_1m()
        rule = self.SUPPORTED_INTERVALS[norm_interval]

        if norm_interval == "1m":
            resampled = base_1m
        else:
            resampled = base_1m.resample(rule, closed="left", label="left").agg({
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum"
            }).dropna()

        # 轉換為 Lightweight Charts 格式
        # Lightweight Charts 接受 UNIX Timestamp (秒)
        records: List[Dict[str, Any]] = []
        timestamps = resampled.index.astype(np.int64) // 10**9
        datetimes = resampled.index.strftime("%Y-%m-%d %H:%M:%S")

        opens = resampled["open"].values
        highs = resampled["high"].values
        lows = resampled["low"].values
        closes = resampled["close"].values
        volumes = resampled["volume"].values

        for ts, dt, o, h, l, c, v in zip(timestamps, datetimes, opens, highs, lows, closes, volumes):
            records.append({
                "time": int(ts),
                "datetime": str(dt),
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": int(v)
            })

        self._cache_intervals[cache_key] = records
        logger.info(f"週期 {interval} ({rule}) 聚合完成，共計 {len(records):,} 根 K 棒。")

        return records[-limit:] if limit else records

    def preload_all_intervals(self) -> Dict[str, int]:
        """
        預熱並計算所有 11 種支援週期之 K 線並存入記憶體快取。

        返回:
            Dict[str, int]: 各週期之 K 棒總筆數字典。
        """
        counts = {}
        for key in ["1m", "2m", "3m", "5m", "15m", "30m", "45m", "1h", "2h", "4h", "1d"]:
            bars = self.get_kbars(key)
            counts[key] = len(bars)
        return counts
