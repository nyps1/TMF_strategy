import os
import sys
import json
import asyncio
import logging
import unittest
from unittest.mock import MagicMock, patch
import aiosqlite
import asyncpg
from aiohttp import web, ClientSession
from datetime import datetime, timezone

# 載入模組
from database import DatabaseManager, SQLiteDatabase, TimescaleDatabase, QuoteData
from shioaji_client import SpreadTracker

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Verifier")

class TestSystem(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # 建立臨時 SQLite DB
        self.sqlite_db_path = "test_quotes.db"
        if os.path.exists(self.sqlite_db_path):
            os.remove(self.sqlite_db_path)
            
        # Mock Shioaji API
        self.mock_api_patcher = patch("shioaji_client.sj.Shioaji")
        self.MockShioaji = self.mock_api_patcher.start()
        self.mock_api_instance = self.MockShioaji.return_value
        
        # 設定 Mock 合約資料
        self.mock_api_instance.Contracts = MagicMock()
        self.mock_api_instance.quote = MagicMock()
        
        # 股票 Mock
        mock_stock = MagicMock()
        mock_stock.code = "2330"
        mock_stock.symbol = "2330"
        mock_stocks = MagicMock()
        mock_stocks.__getitem__.side_effect = lambda key: mock_stock if key == "2330" else KeyError(key)
        mock_stocks.TSE = {"2330": mock_stock}
        mock_stocks.OTC = {}
        self.mock_api_instance.Contracts.Stocks = mock_stocks
        
        # 期貨 Mock
        mock_future = MagicMock()
        mock_future.code = "TXFR1"
        mock_future.symbol = "TXFR1"
        self.mock_api_instance.Contracts.Futures = MagicMock()
        self.mock_api_instance.Contracts.Futures.__iter__.return_value = [[mock_future]]
        self.mock_api_instance.Contracts.Futures.TXF = [mock_future]

    async def asyncTearDown(self):
        self.mock_api_patcher.stop()
        if os.path.exists(self.sqlite_db_path):
            os.remove(self.sqlite_db_path)
        # 清理可能殘留的 test_quotes.db-wal 與 test_quotes.db-shm
        for ext in ["-wal", "-shm"]:
            path = self.sqlite_db_path + ext
            if os.path.exists(path):
                os.remove(path)

    async def test_ac1_sqlite_backend(self):
        logger.info("====== 開始驗證 AC1: SQLite 資料庫雙軌寫入 ======")
        db = SQLiteDatabase(db_path=self.sqlite_db_path)
        await db.connect()
        
        # 建立 100 筆測試資料
        quotes = [
            QuoteData(
                symbol="2330",
                timestamp=int(datetime.now(timezone.utc).timestamp()) - i,
                close=600.0 + i,
                volume=10 + i,
                asset_type="SPOT"
            )
            for i in range(100)
        ]
        
        # 寫入
        start_time = asyncio.get_event_loop().time()
        await db.insert_quotes(quotes)
        elapsed = asyncio.get_event_loop().time() - start_time
        logger.info(f"SQLite 100 筆寫入耗時: {elapsed:.4f} 秒")
        self.assertLess(elapsed, 0.1, "寫入操作阻塞 Event Loop 超過 100ms")
        
        # 讀出驗證
        async with aiosqlite.connect(self.sqlite_db_path) as conn:
            async with conn.execute("SELECT count(*), AVG(close) FROM quotes") as cursor:
                row = await cursor.fetchone()
                count, avg_close = row
                logger.info(f"SQLite 讀出資料筆數: {count}, 平均收盤價: {avg_close}")
                self.assertEqual(count, 100)
                self.assertAlmostEqual(avg_close, 649.5)
                
        await db.close()
        logger.info("====== AC1 (SQLite) 驗證通過 ======")

    async def test_ac1_timescaledb_backend(self):
        logger.info("====== 開始驗證 AC1: TimescaleDB 資料庫雙軌寫入 ======")
        # 測試 TimescaleDB 連線，若無環境變數則嘗試連線 localhost
        # 注意：此步驟假設 TimescaleDB Docker 容器已啟動
        db = TimescaleDatabase()
        try:
            await db.connect()
        except Exception as e:
            logger.warning(f"無法建立 TimescaleDB 連線 (請確保 docker-compose 容器已啟動): {e}")
            logger.warning("跳過 TimescaleDB 端對端寫入驗證")
            return
            
        # 建立 100 筆測試資料
        quotes = [
            QuoteData(
                symbol="2330",
                timestamp=int(datetime.now(timezone.utc).timestamp()) - i,
                close=600.0 + i,
                volume=10 + i,
                asset_type="SPOT"
            )
            for i in range(100)
        ]
        
        # 寫入
        start_time = asyncio.get_event_loop().time()
        await db.insert_quotes(quotes)
        elapsed = asyncio.get_event_loop().time() - start_time
        logger.info(f"TimescaleDB 100 筆寫入耗時: {elapsed:.4f} 秒")
        self.assertLess(elapsed, 0.5, "寫入操作回應過慢")
        
        # 從 PostgreSQL/TimescaleDB 讀出驗證
        pool = db._pool
        async with pool.acquire() as conn:
            count = await conn.fetchval("SELECT count(*) FROM market_prices WHERE symbol = '2330'")
            avg_price = await conn.fetchval("SELECT AVG(price) FROM market_prices WHERE symbol = '2330'")
            logger.info(f"TimescaleDB 讀出資料筆數: {count}, 平均收盤價: {float(avg_price):.2f}")
            self.assertGreaterEqual(count, 100)
            
        await db.close()
        logger.info("====== AC1 (TimescaleDB) 驗證通過 ======")

    async def test_ac2_dynamic_subscription(self):
        logger.info("====== 開始驗證 AC2: 動態訂閱與退訂管理 ======")
        db_manager = DatabaseManager(db_path=self.sqlite_db_path)
        await db_manager.connect()
        
        tracker = SpreadTracker(db_manager, targets_file="test_targets.json")
        tracker._loop = asyncio.get_event_loop()
        
        # 測試正常訂閱股票
        res = await tracker.subscribe("2330", "STOCK")
        self.assertTrue(res)
        self.assertIn("2330", tracker.subscribed_targets)
        
        # 測試重複訂閱
        res_dup = await tracker.subscribe("2330", "STOCK")
        self.assertFalse(res_dup, "重複訂閱應回傳 False")
        
        # 測試訂閱期貨
        res_fut = await tracker.subscribe("TXFR1", "FUTURES")
        self.assertTrue(res_fut)
        self.assertIn("TXFR1", tracker.subscribed_targets)
        
        # 測試正常退訂
        res_unsub = await tracker.unsubscribe("2330")
        self.assertTrue(res_unsub)
        self.assertNotIn("2330", tracker.subscribed_targets)
        
        # 測試退訂未訂閱商品
        res_unsub_none = await tracker.unsubscribe("2330")
        self.assertFalse(res_unsub_none, "退訂未訂閱商品應回傳 False")
        
        await db_manager.close()
        logger.info("====== AC2 驗證通過 ======")

    async def test_ac3_http_endpoints(self):
        logger.info("====== 開始驗證 AC3: HTTP 控制端點 ======")
        db_manager = DatabaseManager(db_path=self.sqlite_db_path)
        await db_manager.connect()
        
        tracker = SpreadTracker(db_manager, targets_file="test_targets.json")
        tracker._loop = asyncio.get_event_loop()
        
        # 啟動 HTTP 伺服器在隨機/測試連接埠
        port = 8888
        await tracker.start_http_server(host="127.0.0.1", port=port)
        
        async with ClientSession() as session:
            # 1. 測試 GET /subscriptions (目前應為空)
            async with session.get(f"http://127.0.0.1:{port}/subscriptions") as resp:
                self.assertEqual(resp.status, 200)
                subs = await resp.json()
                self.assertEqual(subs, [])
                
            # 2. 測試 POST /subscribe
            payload = {"symbol": "2330", "asset_type": "STOCK"}
            async with session.post(f"http://127.0.0.1:{port}/subscribe", json=payload) as resp:
                self.assertEqual(resp.status, 200)
                res = await resp.json()
                self.assertEqual(res["status"], "success")
                
            # 驗證 Tracker 的訂閱狀態
            self.assertIn("2330", tracker.subscribed_targets)
            
            # 3. 再次測試 GET /subscriptions
            async with session.get(f"http://127.0.0.1:{port}/subscriptions") as resp:
                self.assertEqual(resp.status, 200)
                subs = await resp.json()
                self.assertEqual(subs, ["2330"])
                
            # 4. 測試 POST /unsubscribe
            payload_unsub = {"symbol": "2330"}
            async with session.post(f"http://127.0.0.1:{port}/unsubscribe", json=payload_unsub) as resp:
                self.assertEqual(resp.status, 200)
                res = await resp.json()
                self.assertEqual(res["status"], "success")
                
            # 驗證 Tracker 退訂狀態
            self.assertNotIn("2330", tracker.subscribed_targets)
            
            # 5. 測試 GET /health
            async with session.get(f"http://127.0.0.1:{port}/health") as resp:
                self.assertEqual(resp.status, 200)
                res = await resp.json()
                self.assertEqual(res["status"], "healthy")
                
        await tracker.stop()
        await db_manager.close()
        logger.info("====== AC3 驗證通過 ======")

    async def test_ac4_queue_overflow_protection(self):
        logger.info("====== 開始驗證 AC4: Queue 容量防護與 Backpressure ======")
        db_manager = DatabaseManager(db_path=self.sqlite_db_path)
        await db_manager.connect()
        
        tracker = SpreadTracker(db_manager)
        tracker._loop = asyncio.get_event_loop()
        
        # 模擬 150 筆報價流入 (Queue maxsize 為 100)
        # 使用 Queue.put_nowait 灌滿 Queue
        for i in range(150):
            q = QuoteData(
                symbol="2330",
                timestamp=1234567,
                close=600.0,
                volume=10,
                asset_type="SPOT"
            )
            tracker._safe_enqueue(q)
            
        # 驗證 Queue 大小不超過 100，且沒有崩潰 (丟棄策略生效)
        qsize = tracker.quote_queue.qsize()
        logger.info(f"流入 150 筆資料後，Queue 的實際大小: {qsize}")
        self.assertEqual(qsize, 100)
        
        await db_manager.close()
        logger.info("====== AC4 驗證通過 ======")

if __name__ == "__main__":
    unittest.main()
