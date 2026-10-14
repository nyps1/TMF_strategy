"""
SQLite 資料庫管理模組 (services/storage/sqlite_manager.py)

負責管理高頻秒 K 線資料的 SQLite 儲存庫：
1. 資料表 Schema 定義與 B-Tree 索引建置。
2. PRAGMA 底層參數最佳化（WAL 模式、常數記憶體快取分頁、synchronous=NORMAL）。
3. 大容量批次高速寫入（executemany 交易控制）。
4. 時序區間查詢與統計摘要獲取。

遵循 PEP 8、Python Type Hints 與專案工程規範。
"""

import os
import sqlite3
import logging
from typing import List, Dict, Any, Optional, Tuple
import pandas as pd

logger = logging.getLogger(__name__)

# 歷史回補資料之 symbol 標記：Shioaji 近月連續合約 (TMFR1)。
# 以獨立 symbol 與每日增量同步寫入之實體合約代碼 (如 TMFJ6) 區隔，
# 讓下游 KlineService 可依 include_history 旗標決定是否納入歷史回補區段。
HISTORY_SYMBOL: str = "TMFR1"


class SQLiteManager:
    """
    高頻金融時序 SQLite 資料庫管理員。
    """
    def __init__(self, db_path: str = "data/market_data.db"):
        """
        初始化 SQLiteManager。

        參數:
            db_path (str): SQLite 資料庫檔案路徑。
        """
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self.init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """
        建立並設定經效能最佳化的 SQLite 連線。

        返回:
            sqlite3.Connection: 具備 WAL 模式與大快取的資料庫連線實體。
        """
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        # 底層 PRAGMA 最佳化參數
        conn.execute("PRAGMA journal_mode = WAL;")        # 支援讀寫高並發
        conn.execute("PRAGMA synchronous = NORMAL;")      # 兼顧資料安全與磁碟 I/O 吞吐
        conn.execute("PRAGMA cache_size = -64000;")       # 分配 64 MB 記憶體分頁快取
        conn.execute("PRAGMA temp_store = MEMORY;")       # 暫存表放於記憶體
        return conn

    def init_db(self) -> None:
        """
        初始化資料庫結構，建立秒 K 線資料表。
        包含唯一時間約束以確保冪等性。
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tmf10_second_bars (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    time TEXT NOT NULL UNIQUE,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume INTEGER NOT NULL,
                    symbol TEXT NOT NULL DEFAULT 'TMFJ6'
                );
            """)
            conn.commit()
            logger.info(f"SQLite 資料庫初始化完成，目標表: tmf10_second_bars ({self.db_path})")

    def insert_second_bars(
        self,
        df: pd.DataFrame,
        symbol: str = "TMFJ6",
        batch_size: int = 50000
    ) -> int:
        """
        高效批次寫入秒 K 線 DataFrame 至 SQLite 資料庫。
        採用 INSERT OR REPLACE 策略確保重複執行不會引發主鍵約束衝突。

        參數:
            df (pd.DataFrame): 包含「時間、開盤價、最高價、最低價、收盤價、成交量」六大欄位之 DataFrame。
            symbol (str): 商品合約代碼，預設 'TMFJ6'。
            batch_size (int): 單一交易批次筆數，預設 50,000 筆。

        返回:
            int: 成功寫入之總列數。
        """
        if df.empty:
            logger.warning("輸入 DataFrame 為空，略過寫入作業。")
            return 0

        # 標準化欄位對齊
        col_mapping = {
            "時間": "time",
            "開盤價": "open",
            "最高價": "high",
            "最低價": "low",
            "收盤價": "close",
            "成交量": "volume"
        }
        df_norm = df.rename(columns=col_mapping).copy()

        required_cols = ["time", "open", "high", "low", "close", "volume"]
        for col in required_cols:
            if col not in df_norm.columns:
                raise ValueError(f"Status: ERROR | Root Cause: 缺少必要欄位 {col} | Suggested Fix: 確認輸入 DataFrame 結構。")

        # 準備資料元組 (使用向量化 zip 達到極速轉換)
        records = list(
            zip(
                df_norm["time"].astype(str),
                df_norm["open"].astype(float),
                df_norm["high"].astype(float),
                df_norm["low"].astype(float),
                df_norm["close"].astype(float),
                df_norm["volume"].astype(int),
                [symbol] * len(df_norm)
            )
        )

        total_inserted = 0
        insert_sql = """
            INSERT OR REPLACE INTO tmf10_second_bars (time, open, high, low, close, volume, symbol)
            VALUES (?, ?, ?, ?, ?, ?, ?);
        """

        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            # 分批執行以避免單次鎖定過長
            for i in range(0, len(records), batch_size):
                batch = records[i:i + batch_size]
                cursor.executemany(insert_sql, batch)
                conn.commit()
                total_inserted += len(batch)
                logger.info(f"已寫入 SQLite 進度: {total_inserted:,} / {len(records):,} 筆...")

            logger.info(f"全數寫入完成！總計: {total_inserted:,} 筆。")
        finally:
            conn.close()

        # 寫入完畢後建立或重建索引以達成最佳讀取效能
        self.create_indices()
        return total_inserted

    def create_indices(self) -> None:
        """
        在資料寫入完畢後建立 B-Tree 索引，
        避免在批次插入過程中頻繁重平衡 B-Tree 造成的磁碟 I/O 開銷。
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tmf10_time ON tmf10_second_bars(time);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tmf10_symbol ON tmf10_second_bars(symbol);")
            conn.commit()
            logger.info("已建立 B-Tree 索引: idx_tmf10_time, idx_tmf10_symbol。")

    def query_bars_by_time_range(
        self,
        start_time: str,
        end_time: str,
        symbol: str = HISTORY_SYMBOL
    ) -> pd.DataFrame:
        """
        透過 B-Tree 索引高效檢索指定時間區間之秒 K 線。

        參數:
            start_time (str): 起始時間 (格式: YYYY-MM-DD HH:MM:SS)。
            end_time (str): 結束時間 (格式: YYYY-MM-DD HH:MM:SS)。
            symbol (str): 商品代碼，預設 HISTORY_SYMBOL ('TMFR1')。
                TMFR1 代表近月連續合約，即「全部資料」(歷史回補列與各實體合約依時間串接)，不加 symbol 過濾，
                因此跨越到期日換月後仍可取得連續資料；指定實體合約代碼 (如 'TMFJ6') 則僅回傳該合約之列。

        返回:
            pd.DataFrame: 檢索所得之秒 K 線 DataFrame。
        """
        select_sql = (
            "SELECT time AS 時間, open AS 開盤價, high AS 最高價, low AS 最低價, "
            "close AS 收盤價, volume AS 成交量 FROM tmf10_second_bars "
        )
        if symbol == HISTORY_SYMBOL:
            query_sql = select_sql + "WHERE time >= ? AND time <= ? ORDER BY time ASC;"
            params: tuple = (start_time, end_time)
        else:
            query_sql = select_sql + "WHERE symbol = ? AND time >= ? AND time <= ? ORDER BY time ASC;"
            params = (symbol, start_time, end_time)
        with self._get_connection() as conn:
            df = pd.read_sql_query(query_sql, conn, params=params)
            return df

    def get_summary(self) -> Dict[str, Any]:
        """
        獲取資料庫內秒 K 線總體統計資訊。

        返回:
            Dict[str, Any]: 包含總列數、首尾時間、累積成交量與資料庫大小。
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    COUNT(*), 
                    MIN(time), 
                    MAX(time), 
                    SUM(volume),
                    COUNT(DISTINCT symbol)
                FROM tmf10_second_bars;
            """)
            row = cursor.fetchone()
            total_count, min_time, max_time, total_volume, symbol_count = row

        file_size_mb = os.path.getsize(self.db_path) / (1024.0 * 1024.0) if os.path.exists(self.db_path) else 0.0

        return {
            "db_path": os.path.abspath(self.db_path),
            "table_name": "tmf10_second_bars",
            "total_rows": total_count or 0,
            "earliest_time": min_time or "",
            "latest_time": max_time or "",
            "total_volume": int(total_volume or 0),
            "symbol_count": symbol_count or 0,
            "file_size_mb": round(file_size_mb, 2)
        }
