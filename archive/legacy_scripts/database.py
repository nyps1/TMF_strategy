import aiosqlite
import asyncpg
import asyncio
import logging
import os
import abc
from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class QuoteData(BaseModel):
    symbol: str
    timestamp: int  # Unix timestamp in seconds
    close: float
    volume: int
    asset_type: str  # 'SPOT' or 'FUTURES'

class BaseDatabase(abc.ABC):
    @abc.abstractmethod
    async def connect(self) -> None:
        """建立資料庫連線"""
        pass

    @abc.abstractmethod
    async def close(self) -> None:
        """關閉資料庫連線"""
        pass

    @abc.abstractmethod
    async def insert_quotes(self, quotes: List[QuoteData]) -> None:
        """寫入報價資料"""
        pass

class SQLiteDatabase(BaseDatabase):
    """
    SQLite 實作，採用 WAL 模式以支援併發讀寫。
    """
    def __init__(self, db_path: str = "quotes.db"):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
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
        logger.info("SQLite database connected and WAL mode enabled.")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            logger.info("SQLite database connection closed.")

    async def insert_quotes(self, quotes: List[QuoteData]) -> None:
        if not self._conn:
            raise RuntimeError("Status: ERROR | Root Cause: SQLite database connection not established | Suggested Fix: Call connect() before inserting.")
        
        async with self._lock:
            try:
                await self._conn.executemany(
                    "INSERT INTO quotes (symbol, timestamp, close, volume) VALUES (?, ?, ?, ?)",
                    [(q.symbol, q.timestamp, q.close, q.volume) for q in quotes]
                )
                await self._conn.commit()
            except Exception as e:
                logger.error(f"Failed to insert quotes to SQLite: {e}")
                await self._conn.rollback()
                raise

class TimescaleDatabase(BaseDatabase):
    """
    TimescaleDB 實作，使用 asyncpg 連線池進行高頻時序寫入。
    為了符合 FK 限制，寫入報價前會先確認 assets 資料表是否存在該商品。
    """
    def __init__(self):
        self.host = os.environ.get("TIMESCALE_HOST", "localhost")
        self.port = int(os.environ.get("TIMESCALE_PORT", "5432"))
        self.user = os.environ.get("TIMESCALE_USER", "admin")
        self.password = os.environ.get("TIMESCALE_PASSWORD", "admin_password")
        self.database = os.environ.get("TIMESCALE_DB", "spread_db")
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        try:
            self._pool = await asyncpg.create_pool(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database,
                min_size=5,
                max_size=20
            )
            logger.info("TimescaleDB connection pool created.")
        except Exception as e:
            logger.error(f"Failed to connect to TimescaleDB: {e}")
            raise

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            logger.info("TimescaleDB connection pool closed.")

    async def insert_quotes(self, quotes: List[QuoteData]) -> None:
        if not self._pool:
            raise RuntimeError("Status: ERROR | Root Cause: TimescaleDB pool not established | Suggested Fix: Call connect() before inserting.")

        # 整理出 unique assets 以寫入 assets 表 (符合 FK 外鍵約束)
        unique_assets = {}
        for q in quotes:
            unique_assets[q.symbol] = q.asset_type

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # 1. 寫入基本資產表 (ON CONFLICT DO NOTHING)
                if unique_assets:
                    asset_records = [
                        (sym, atype, f"Auto-created asset for {sym}")
                        for sym, atype in unique_assets.items()
                    ]
                    await conn.executemany(
                        """
                        INSERT INTO assets (symbol, asset_type, description)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (symbol) DO NOTHING
                        """,
                        asset_records
                    )

                # 2. 批次寫入市場報價 (market_prices)
                price_records = [
                    (
                        datetime.fromtimestamp(q.timestamp, tz=timezone.utc),
                        q.symbol,
                        q.close,
                        q.volume
                    )
                    for q in quotes
                ]
                await conn.executemany(
                    """
                    INSERT INTO market_prices (time, symbol, price, volume)
                    VALUES ($1, $2, $3, $4)
                    """,
                    price_records
                )

class DatabaseManager:
    """
    資料庫管理者代理 (Proxy)。
    依據環境變數 DB_BACKEND 決定底層使用 SQLite 或是 TimescaleDB。
    """
    def __init__(self, db_path: str = "quotes.db"):
        backend = os.environ.get("DB_BACKEND", "sqlite").lower()
        if backend == "timescaledb":
            self._db = TimescaleDatabase()
        else:
            self._db = SQLiteDatabase(db_path)

    async def connect(self) -> None:
        await self._db.connect()

    async def close(self) -> None:
        await self._db.close()

    async def insert_quotes(self, quotes: List[QuoteData]) -> None:
        await self._db.insert_quotes(quotes)
