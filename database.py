import aiosqlite
import asyncio
import logging
from typing import Optional, List
from pydantic import BaseModel

logger = logging.getLogger(__name__)

class QuoteData(BaseModel):
    symbol: str
    timestamp: int
    close: float
    volume: int

class DatabaseManager:
    """
    負責管理 SQLite 資料庫連線與非同步寫入作業。
    採用 Repository Pattern 隔離資料庫實作細節。
    """
    def __init__(self, db_path: str = "quotes.db"):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """
        建立資料庫連線並啟用 WAL (Write-Ahead Logging) 模式。
        原理：WAL 模式允許讀取與寫入併發執行，避免傳統 Rollback Journal 導致的 Database Locked 錯誤。
        """
        self._conn = await aiosqlite.connect(self.db_path)
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA synchronous=NORMAL;")
        
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                close REAL NOT NULL,
                volume INTEGER NOT NULL
            );
        """)
        await self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_quotes_symbol_timestamp 
            ON quotes (symbol, timestamp);
        """)
        await self._conn.commit()
        logger.info("Database connected and WAL mode enabled.")

    async def close(self) -> None:
        """安全關閉資料庫連線"""
        if self._conn:
            await self._conn.close()
            logger.info("Database connection closed.")

    async def insert_quotes(self, quotes: List[QuoteData]) -> None:
        """
        非同步批次寫入報價資料。
        使用 asyncio.Lock() 確保同一時間只有一個 Task 進入寫入區塊，防止 SQLite 鎖定機制導致的併發衝突 (Race Condition)。
        """
        if not self._conn:
            # 防錯機制 (Fail fast)
            raise RuntimeError("Status: ERROR | Root Cause: Database connection not established | Suggested Fix: Call connect() before inserting.")
        
        async with self._lock:
            try:
                await self._conn.executemany(
                    "INSERT INTO quotes (symbol, timestamp, close, volume) VALUES (?, ?, ?, ?)",
                    [(q.symbol, q.timestamp, q.close, q.volume) for q in quotes]
                )
                await self._conn.commit()
            except Exception as e:
                logger.error(f"Failed to insert quotes: {e}")
                await self._conn.rollback()
                raise
