from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import asyncpg
import httpx
import os
import asyncio

app = FastAPI(title="Quant Dashboard API")

# Allow CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_HOST = os.environ.get("TIMESCALE_HOST", "timescaledb")
DB_PORT = os.environ.get("TIMESCALE_PORT", "5432")
DB_USER = os.environ.get("TIMESCALE_USER", "admin")
DB_PASS = os.environ.get("TIMESCALE_PASSWORD", "admin_password")
DB_NAME = os.environ.get("TIMESCALE_DB", "spread_db")
TRACKER_URL = os.environ.get("TRACKER_URL", "http://tracker:8080")

pool = None

@app.on_event("startup")
async def startup():
    global pool
    # Try connecting with retries
    for i in range(5):
        try:
            pool = await asyncpg.create_pool(
                host=DB_HOST,
                port=DB_PORT,
                user=DB_USER,
                password=DB_PASS,
                database=DB_NAME,
                min_size=1,
                max_size=10
            )
            print("Successfully connected to TimescaleDB")
            break
        except Exception as e:
            print(f"Failed to connect to TimescaleDB (attempt {i+1}/5): {e}")
            await asyncio.sleep(2)

@app.on_event("shutdown")
async def shutdown():
    global pool
    if pool:
        await pool.close()

@app.get("/api/assets")
async def get_assets():
    """取得所有被記錄的資產"""
    if not pool:
        raise HTTPException(status_code=503, detail="Database connection not available")
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT symbol, asset_type, description FROM assets")
        return [dict(row) for row in rows]

@app.get("/api/history/{symbol}")
async def get_history(symbol: str, interval: str = "1 minute", limit: int = 500):
    """
    獲取指定資產的降採樣 K 線資料 (OHLCV)
    預設回傳 1 分鐘 K 線，最多 500 筆。
    """
    if not pool:
        raise HTTPException(status_code=503, detail="Database connection not available")
    
    # TimescaleDB time_bucket OHLCV query
    query = f"""
        SELECT 
            time_bucket($1::interval, time) AS bucket,
            first(price, time) AS open,
            max(price) AS high,
            min(price) AS low,
            last(price, time) AS close,
            sum(volume) AS volume
        FROM market_prices
        WHERE symbol = $2
        GROUP BY bucket
        ORDER BY bucket DESC
        LIMIT $3
    """
    
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, interval, symbol, limit)
        
        # Format for lightweight-charts: { time: UNIX_TIMESTAMP, open, high, low, close }
        formatted_data = []
        # lightweight-charts expects data in chronological order (oldest first)
        for row in reversed(rows):
            formatted_data.append({
                "time": int(row['bucket'].timestamp()),
                "open": float(row['open']),
                "high": float(row['high']),
                "low": float(row['low']),
                "close": float(row['close']),
                "value": float(row['close']) # For line chart compatibility
            })
        return formatted_data

@app.get("/api/status")
async def get_status():
    """取得 tracker 目前正在訂閱的標的"""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{TRACKER_URL}/subscriptions", timeout=2.0)
            if resp.status_code == 200:
                return resp.json()
            else:
                raise HTTPException(status_code=resp.status_code, detail="Tracker returned error")
        except httpx.RequestError as e:
            raise HTTPException(status_code=503, detail=f"Failed to communicate with tracker: {e}")

# 匯入 services 回測業務邏輯
import sys
from pathlib import Path
root_path = Path(__file__).resolve().parent.parent
if str(root_path) not in sys.path:
    sys.path.append(str(root_path))

from services.backtest import BacktestConfig, BacktestEngine, DataLoader, BacktestMetrics
from services.storage import SQLiteManager, KlineService

kline_service_instance = KlineService(db_path=str(root_path / "data" / "market_data.db"))

@app.post("/api/backtest/run")
async def run_backtest_endpoint(
    start_date: str = "2024-01-01",
    end_date: str = "2024-06-30",
    capital: float = 105150.0,
    leverage: float = 7.0,
    margin_req: float = 35050.0
) -> dict:
    """
    執行微型台指期動態布林突破策略回測 API 路由端點。
    負責接收參數並分發至 services/backtest 執行業務邏輯，回傳標準化 JSON。

    參數:
        start_date (str): 回測起始日期 (YYYY-MM-DD)。
        end_date (str): 回測結束日期 (YYYY-MM-DD)。
        capital (float): 期初資金。
        leverage (float): 最大槓桿倍數。
        margin_req (float): 單口保證金。

    返回:
        dict: 包含 status, data, message 之標準化 JSON 回應。
    """
    try:
        config = BacktestConfig(
            initial_capital=capital,
            max_leverage=leverage,
            margin_req=margin_req
        )
        loader = DataLoader(cache_dir="data/cache")
        df = loader.generate_simulated_kbars(start_date=start_date, end_date=end_date)
        engine = BacktestEngine(config=config)
        metrics: BacktestMetrics = engine.run(df)
        return {
            "status": "SUCCESS",
            "message": "Backtest completed successfully.",
            "data": metrics.model_dump()
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "message": f"Backtest execution failed: {str(e)}",
            "root_cause": str(type(e).__name__),
            "suggested_fix": "Please verify backtest parameter constraints and data consistency."
        }


@app.get("/api/history/micro-taiwan-10/summary")
async def get_micro_taiwan_summary_endpoint() -> dict:
    """
    獲取微台10 SQLite 秒 K 線資料庫的整體統計資訊 API 路由端點。
    分發請求至 services/storage/sqlite_manager.py。

    返回:
        dict: 包含 status, data, message 之標準化 JSON 回應。
    """
    try:
        db_path = str(root_path / "data" / "market_data.db")
        manager = SQLiteManager(db_path=db_path)
        summary = manager.get_summary()
        return {
            "status": "SUCCESS",
            "message": "Market data summary retrieved successfully.",
            "data": summary
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "message": f"Failed to retrieve market data summary: {str(e)}",
            "root_cause": str(type(e).__name__),
            "suggested_fix": "Please check if data/market_data.db exists and has appropriate read permissions."
        }


@app.get("/api/history/micro-taiwan-10/second-bars")
async def get_micro_taiwan_second_bars_endpoint(
    start_time: str = "2026-09-16 08:45:00",
    end_time: str = "2026-09-16 09:00:00",
    symbol: str = "TMFJ6"
) -> dict:
    """
    查詢指定時間區間內微台10秒 K 線資料 API 路由端點。
    分發請求至 services/storage/sqlite_manager.py 透過 B-Tree 索引檢索。

    參數:
        start_time (str): 查詢起始時間字串 (格式: YYYY-MM-DD HH:MM:SS)。
        end_time (str): 查詢結束時間字串 (格式: YYYY-MM-DD HH:MM:SS)。
        symbol (str): 商品代碼，預設 'TMFJ6'。

    返回:
        dict: 包含 status, data (列數與紀錄陣列), message 之標準化 JSON 回應。
    """
    try:
        db_path = str(root_path / "data" / "market_data.db")
        manager = SQLiteManager(db_path=db_path)
        df = manager.query_bars_by_time_range(start_time=start_time, end_time=end_time, symbol=symbol)
        records = df.to_dict(orient="records")
        return {
            "status": "SUCCESS",
            "message": f"Successfully retrieved {len(records)} second bars.",
            "data": {
                "count": len(records),
                "bars": records
            }
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "message": f"Failed to query second bars: {str(e)}",
            "root_cause": str(type(e).__name__),
            "suggested_fix": "Please verify time format (YYYY-MM-DD HH:MM:SS) and database connectivity."
        }


@app.get("/api/kline/intervals")
async def get_kline_intervals_endpoint() -> dict:
    """
    獲取系統所支援之所有 K 線週期清單 API 路由端點。

    返回:
        dict: 包含 status, data, message 之標準化 JSON 回應。
    """
    try:
        intervals = [
            {"key": k, "label": v}
            for k, v in KlineService.INTERVAL_LABELS.items()
            if k != "1D"  # 排除重複的大寫標記
        ]
        return {
            "status": "SUCCESS",
            "message": "Supported kline intervals retrieved successfully.",
            "data": intervals
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "message": f"Failed to retrieve intervals: {str(e)}",
            "root_cause": str(type(e).__name__),
            "suggested_fix": "Please check backend service configuration."
        }


@app.get("/api/kline")
async def get_kline_endpoint(
    interval: str = "5m",
    limit: Optional[int] = None
) -> dict:
    """
    獲取指定時間週期之 K 線資料 (OHLCV) API 路由端點。
    分發請求至 services/storage/kline_service.py，相容於 TradingView Lightweight Charts。

    參數:
        interval (str): 週期代碼 ('1m', '2m', '3m', '5m', '15m', '30m', '45m', '1h', '2h', '4h', '1d')。
        limit (Optional[int]): 最大回傳筆數 (預設全數回傳)。

    返回:
        dict: 包含 status, data, message 之標準化 JSON 回應。
    """
    try:
        bars = kline_service_instance.get_kbars(interval=interval, limit=limit)
        return {
            "status": "SUCCESS",
            "message": f"Successfully retrieved {len(bars)} bars for interval '{interval}'.",
            "data": {
                "symbol": "TMFJ6",
                "symbol_name": "微台10",
                "interval": interval,
                "count": len(bars),
                "bars": bars
            }
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "message": f"Failed to retrieve kline data: {str(e)}",
            "root_cause": str(type(e).__name__),
            "suggested_fix": "Please verify if interval is supported (e.g., 1m, 2m, 3m, 5m, 15m, 30m, 45m, 1h, 2h, 4h, 1d)."
        }


@app.get("/kline", response_class=HTMLResponse)
async def serve_kline_page_endpoint():
    """
    提供多週期互動式 K 線圖網頁之 API 路由端點。
    若存在 kline_dashboard.html 則直接返回該檔案內容。

    返回:
        HTMLResponse: 網頁 HTML 回應。
    """
    html_file = root_path / "kline_dashboard.html"
    if html_file.exists():
        return FileResponse(str(html_file), media_type="text/html")
    return HTMLResponse("<h1>K 線圖網頁正在生成中，請稍後重新整理。</h1>", status_code=200)
