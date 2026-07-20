import os
import asyncio
import logging
import shioaji as sj
from typing import Optional
from pydantic import ValidationError
from database import DatabaseManager, QuoteData

logger = logging.getLogger(__name__)

class SpreadTracker:
    """
    負責與永豐 Shioaji API 進行通訊的核心模組。
    使用 Dependency Injection 將 DatabaseManager 注入，降低模組間的耦合。
    """
    def __init__(self, db_manager: DatabaseManager):
        self.api = sj.Shioaji()
        self.db_manager = db_manager
        self.quote_queue: asyncio.Queue[QuoteData] = asyncio.Queue()
        self._is_running: bool = False
        self._writer_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _quote_callback(self, topic: str, quote: dict) -> None:
        """
        Shioaji 報價回呼函數。
        原理：此函數由 Shioaji 底層 C++ Thread Pool 觸發，並非於 main event loop 執行。
        因此嚴禁直接呼叫任何 await 操作，必須使用 asyncio.run_coroutine_threadsafe 將資料拋轉回 Event Loop。
        """
        try:
            # 嚴格依賴 Pydantic 進行資料驗證，過濾格式異常的報價 (如：空字串或型別錯誤)
            q_data = QuoteData(
                symbol=quote.get("Code", "UNKNOWN"),
                timestamp=quote.get("Time", 0),
                close=quote.get("Close", 0.0),
                volume=quote.get("Volume", 0)
            )
            
            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(self.quote_queue.put(q_data), self._loop)
        except ValidationError as e:
            logger.error(f"Status: ERROR | Root Cause: Quote validation failed for {topic} | Suggested Fix: Check API schema changes. Detail: {e}")
        except Exception as e:
            logger.error(f"Status: ERROR | Root Cause: Unexpected error in callback | Suggested Fix: Review callback logic. Detail: {e}")

    async def _db_writer_worker(self) -> None:
        """
        背景寫入工作迴圈 (Worker)。
        原理：Consumer Pattern。批次 (Batching) 從 Queue 中取出資料，
        滿 50 筆或逾時 1 秒後才進行資料庫 IO，大幅減少 SQLite Transaction 的開銷與鎖定頻率。
        """
        batch: list[QuoteData] = []
        while self._is_running or not self.quote_queue.empty():
            try:
                quote = await asyncio.wait_for(self.quote_queue.get(), timeout=1.0)
                batch.append(quote)
                self.quote_queue.task_done()
                
                if len(batch) >= 50:
                    await self.db_manager.insert_quotes(batch)
                    batch.clear()
            except asyncio.TimeoutError:
                if batch:
                    await self.db_manager.insert_quotes(batch)
                    batch.clear()
            except Exception as e:
                logger.error(f"Status: ERROR | Root Cause: DB Writer worker failed | Suggested Fix: Check DB connection or data format. Detail: {e}")
                await asyncio.sleep(1)

    async def start(self) -> None:
        """
        啟動服務與 API 連線。
        安全規範：所有機密資訊皆從 os.environ 讀取，嚴禁寫死。
        """
        api_key = os.environ.get("SHIOAJI_API_KEY")
        secret_key = os.environ.get("SHIOAJI_SECRET_KEY")

        if not api_key or not secret_key:
            raise ValueError("Status: ERROR | Root Cause: Missing Credentials | Suggested Fix: Export SHIOAJI_API_KEY and SHIOAJI_SECRET_KEY in environment variables.")

        self._loop = asyncio.get_running_loop()
        logger.info("Connecting to Shioaji API...")
        
        # 由於 api.login 涉及網路 IO，可能導致 Event Loop 阻塞，故放入 ThreadPoolExecutor 執行。
        await self._loop.run_in_executor(None, self.api.login, api_key, secret_key)
        logger.info("Successfully connected to Shioaji API.")

        self.api.quote.set_on_tick_fop_v1_callback(self._quote_callback)

        self._is_running = True
        self._writer_task = asyncio.create_task(self._db_writer_worker())
        
        logger.info("System started. Ready to subscribe quotes.")

    async def stop(self) -> None:
        """優雅關閉 (Graceful Shutdown) 確保資料不遺失且連線正確關閉"""
        logger.info("Stopping SpreadTracker...")
        self._is_running = False
        
        if self._writer_task:
            await self._writer_task
            
        if self._loop:
            await self._loop.run_in_executor(None, self.api.logout)
        logger.info("SpreadTracker stopped.")

async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    db_manager = DatabaseManager()
    await db_manager.connect()
    
    tracker = SpreadTracker(db_manager)
    try:
        await tracker.start()
        # 保持主程式執行，避免 Event Loop 過早結束
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        logger.info("Shutdown signal received.")
    finally:
        await tracker.stop()
        await db_manager.close()

if __name__ == "__main__":
    asyncio.run(main())
