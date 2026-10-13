import os
import sys
import io
import json
import asyncio
import logging
import shioaji as sj
from typing import Optional, Set, Dict
from pydantic import ValidationError
from aiohttp import web
from database import DatabaseManager, QuoteData

# 修復 Windows 環境下 cp950 的編碼輸出錯誤
if sys.platform.startswith("win"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

logger = logging.getLogger(__name__)

class SpreadTracker:
    """
    負責與永豐 Shioaji API 進行通訊的核心模組。
    支援雙軌資料庫寫入、動態訂閱/退訂多商品（股票與期貨），並提供 HTTP 控制介面。
    """
    def __init__(self, db_manager: DatabaseManager, targets_file: str = "targets.json"):
        self.api = sj.Shioaji()
        self.db_manager = db_manager
        self.targets_file = targets_file
        
        # 佇列大小防護：避免 OOM，設為 100
        self.quote_queue: asyncio.Queue[QuoteData] = asyncio.Queue(maxsize=100)
        self._is_running: bool = False
        self._writer_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        
        # 訂閱狀態管理
        self.subscribed_targets: Set[str] = set()
        self.active_contracts: Dict[str, sj.contracts.Contract] = {}
        
        # HTTP 伺服器
        self._http_runner: Optional[web.AppRunner] = None
        self._http_site: Optional[web.TCPSite] = None

    def _stock_quote_callback(self, exchange, tick) -> None:
        """股票逐筆成交回呼"""
        self._process_tick(tick, asset_type="SPOT")

    def _futures_quote_callback(self, exchange, tick) -> None:
        """期貨逐筆成交回呼"""
        self._process_tick(tick, asset_type="FUTURES")

    def _process_tick(self, tick, asset_type: str) -> None:
        """
        處理 Tick 資料。此函數由 Shioaji 底層執行緒觸發，
        因此需使用 loop.call_soon_threadsafe 執行緒安全地將資料推回非同步 Event Loop 的 Queue。
        """
        try:
            # 轉換為 Unix 時間戳 (秒)
            ts = int(tick.datetime.timestamp())
            q_data = QuoteData(
                symbol=tick.code,
                timestamp=ts,
                close=float(tick.close),
                volume=int(tick.volume),
                asset_type=asset_type
            )
            
            if self._loop and self._loop.is_running():
                # 安全地在非同步執行緒呼叫 Queue.put_nowait
                self._loop.call_soon_threadsafe(self._safe_enqueue, q_data)
        except ValidationError as e:
            logger.error(f"Status: ERROR | Root Cause: Quote validation failed | Suggested Fix: Check API schema changes. Detail: {e}")
        except Exception as e:
            logger.error(f"Status: ERROR | Root Cause: Unexpected error in callback | Suggested Fix: Review callback logic. Detail: {e}")

    def _safe_enqueue(self, q_data: QuoteData) -> None:
        """將資料推入 Queue，若滿載則執行丟棄策略 (Backpressure)"""
        try:
            self.quote_queue.put_nowait(q_data)
        except asyncio.QueueFull:
            logger.warning(f"Queue is full (maxsize={self.quote_queue.maxsize}). Dropping quote for {q_data.symbol}")

    async def _db_writer_worker(self) -> None:
        """
        背景寫入工作迴圈 (Consumer)。
        批次從 Queue 中取出資料，滿 50 筆或逾時 1 秒後寫入資料庫，以最佳化寫入效能。
        """
        batch: list[QuoteData] = []
        while self._is_running or not self.quote_queue.empty():
            try:
                # 逾時 1 秒取出一筆
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

    def _find_contract(self, symbol: str, asset_type: str) -> sj.contracts.Contract:
        """尋找商品合約，支援 Stock 與 Futures 的相容性尋找"""
        asset_type_upper = asset_type.upper()
        if asset_type_upper in ["STOCK", "SPOT"]:
            try:
                return self.api.Contracts.Stocks[symbol]
            except KeyError:
                for market in [self.api.Contracts.Stocks.TSE, self.api.Contracts.Stocks.OTC]:
                    try:
                        return market[symbol]
                    except KeyError:
                        pass
                raise ValueError(f"Stock contract not found for symbol: {symbol}")
                
        elif asset_type_upper == "FUTURES":
            try:
                return self.api.Contracts.Futures[symbol]
            except KeyError:
                for group in self.api.Contracts.Futures:
                    for c in group:
                        if c.code == symbol or c.symbol == symbol:
                            return c
                raise ValueError(f"Futures contract not found for symbol: {symbol}")
        else:
            raise ValueError(f"Unsupported asset_type: {asset_type}")

    async def subscribe(self, symbol: str, asset_type: str) -> bool:
        """動態訂閱商品"""
        if symbol in self.subscribed_targets:
            logger.warning(f"Already subscribed to {symbol}")
            return False

        try:
            # 尋找合約
            contract = self._find_contract(symbol, asset_type)
            
            # 呼叫 Shioaji 訂閱
            # 注意：Shioaji 1.3.1 的 subscribe 會進行網路呼叫，放進 executor 以免卡死
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, 
                self.api.quote.subscribe, 
                contract, 
                sj.constant.QuoteType.Tick
            )
            
            self.subscribed_targets.add(symbol)
            self.active_contracts[symbol] = contract
            logger.info(f"Successfully subscribed to {symbol} ({asset_type})")
            return True
        except Exception as e:
            logger.error(f"Failed to subscribe to {symbol}: {e}")
            return False

    async def unsubscribe(self, symbol: str) -> bool:
        """動態退訂商品"""
        if symbol not in self.subscribed_targets:
            logger.warning(f"Not subscribed to {symbol}")
            return False

        try:
            contract = self.active_contracts.get(symbol)
            if not contract:
                raise ValueError(f"Active contract object not found for {symbol}")

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, 
                self.api.quote.unsubscribe, 
                contract, 
                sj.constant.QuoteType.Tick
            )
            
            self.subscribed_targets.remove(symbol)
            self.active_contracts.pop(symbol, None)
            logger.info(f"Successfully unsubscribed from {symbol}")
            return True
        except Exception as e:
            logger.error(f"Failed to unsubscribe from {symbol}: {e}")
            return False

    async def start_http_server(self, host: str = "0.0.0.0", port: int = 8080) -> None:
        """啟動輕量 HTTP 控制介面"""
        app = web.Application()
        app.router.add_get('/subscriptions', self._http_get_subscriptions)
        app.router.add_post('/subscribe', self._http_post_subscribe)
        app.router.add_post('/unsubscribe', self._http_post_unsubscribe)
        app.router.add_get('/health', self._http_get_health)
        
        self._http_runner = web.AppRunner(app)
        await self._http_runner.setup()
        self._http_site = web.TCPSite(self._http_runner, host, port)
        await self._http_site.start()
        logger.info(f"HTTP Control Server started on http://{host}:{port}")

    async def _http_get_subscriptions(self, request) -> web.Response:
        return web.json_response(list(self.subscribed_targets))

    async def _http_post_subscribe(self, request) -> web.Response:
        try:
            data = await request.json()
            symbol = data.get("symbol")
            asset_type = data.get("asset_type")
            if not symbol or not asset_type:
                return web.json_response({"error": "Missing symbol or asset_type"}, status=400)
            
            success = await self.subscribe(symbol, asset_type)
            if success:
                return web.json_response({"status": "success", "message": f"Subscribed to {symbol}"})
            else:
                return web.json_response({"status": "warning", "message": f"Already subscribed or subscription failed"}, status=400)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _http_post_unsubscribe(self, request) -> web.Response:
        try:
            data = await request.json()
            symbol = data.get("symbol")
            if not symbol:
                return web.json_response({"error": "Missing symbol"}, status=400)
            
            success = await self.unsubscribe(symbol)
            if success:
                return web.json_response({"status": "success", "message": f"Unsubscribed from {symbol}"})
            else:
                return web.json_response({"status": "warning", "message": f"Not subscribed or unsubscription failed"}, status=400)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _http_get_health(self, request) -> web.Response:
        return web.json_response({
            "status": "healthy",
            "queue_size": self.quote_queue.qsize(),
            "backend": os.environ.get("DB_BACKEND", "sqlite")
        })

    async def start(self) -> None:
        """啟動 API 連線與背景服務"""
        api_key = os.environ.get("SHIOAJI_API_KEY")
        secret_key = os.environ.get("SHIOAJI_SECRET_KEY")

        if not api_key or not secret_key:
            raise ValueError("Status: ERROR | Root Cause: Missing Credentials | Suggested Fix: Export SHIOAJI_API_KEY and SHIOAJI_SECRET_KEY.")

        self._loop = asyncio.get_running_loop()
        logger.info("Connecting to Shioaji API...")
        
        # 網路登入放入 executor
        await self._loop.run_in_executor(None, self.api.login, api_key, secret_key)
        logger.info("Successfully connected to Shioaji API.")

        # 同時設定股票與期貨的 Tick 回呼
        self.api.quote.set_on_tick_stk_v1_callback(self._stock_quote_callback)
        self.api.quote.set_on_tick_fop_v1_callback(self._futures_quote_callback)

        self._is_running = True
        self._writer_task = asyncio.create_task(self._db_writer_worker())
        
        # 啟動 HTTP 伺服器
        await self.start_http_server()
        
        # 讀取 targets.json 進行初始訂閱
        if os.path.exists(self.targets_file):
            try:
                with open(self.targets_file, "r", encoding="utf-8") as f:
                    targets = json.load(f)
                
                for s in targets.get("stocks", []):
                    await self.subscribe(s, asset_type="STOCK")
                for f_code in targets.get("futures", []):
                    await self.subscribe(f_code, asset_type="FUTURES")
            except Exception as e:
                logger.error(f"Failed to load targets.json: {e}")

        logger.info("System started and initial subscriptions processed.")

    async def stop(self) -> None:
        """優雅關閉系統"""
        logger.info("Stopping SpreadTracker...")
        self._is_running = False
        
        # 清除 HTTP 伺服器
        if self._http_runner:
            await self._http_runner.cleanup()
            logger.info("HTTP Control Server stopped.")

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
        # 保持運行
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        logger.info("Shutdown signal received.")
    finally:
        await tracker.stop()
        await db_manager.close()

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    asyncio.run(main())
