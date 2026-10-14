# 環境依賴 (Dependencies)

本專案之 Python 核心連線模組與服務依賴以下外部套件，所有版本號皆已經過固定以確保系統在重製或部署時的一致性與穩定性。

| Package Name | Version | Purpose |
|--------------|---------|---------|
| shioaji      | == 1.3.1| 永豐 API，負責建立 TCP/WebSocket 連線與接收即時報價資料。 |
| aiosqlite    | == 0.20.0| 非同步 SQLite 驅動，確保資料寫入不阻塞 Event Loop，提供更佳的高頻寫入效能。 |
| pydantic     | == 2.7.1| 資料驗證與強型別控制，確保從外部 API 收到的報價結構完整性，避免後續處理崩潰。 |
| asyncpg      | == 0.29.0| 高效能非同步 PostgreSQL/TimescaleDB 驅動，支援連線池管理以進行高頻時序寫入。 |
| aiohttp      | == 3.9.5 | 非同步 HTTP 框架，用於提供即時訂閱控制 API 及健康檢查端點。 |
| python-dotenv| == 1.0.1 | 載入環境變數設定檔 `.env`。 |
| pandas       | == 2.2.1 | 時間序列 K 線資料整理、近月拼接與資料框操作。 |
| numpy        | == 1.26.4| 高效能向量化指標數值計算。 |
| plotly       | == 5.20.0| 繪製互動式 HTML 回測績效曲線與進出場標記圖表。 |
| pyarrow      | == 25.0.1| 支援高效能 Parquet 行情快取序列化儲存與檢索。 |
| openpyxl     | == 3.1.5 | 處理與匯出 Excel (.xlsx) 檔案格式標準函式庫。 |
| xlsxwriter   | == 3.2.9 | 高效能流式寫入超大數據量 Excel 檔案（支援常數記憶體模式）。 |
| httpx        | == 0.28.1| 非同步 HTTP 測試客戶端，支援 FastAPI 單元測試。 |
| fastapi      | == 0.110.0| 高效能非同步 Web 框架，提供策略配置 API 與 WebSocket 全雙工即時串流通道。 |
| uvicorn      | == 0.29.0 | ASGI 伺服器，負責驅動非同步 FastAPI 與 WebSocket 高頻事件廣播。 |
| websockets   | == 13.1  | 全雙工 WebSocket 協定核心函式庫，支撐即時行情與策略訊號推播。 |
| lightweight-charts | == 5.2.0 | 前端專業級 TradingView 高頻 K 線與 Baseline 走勢圖 Canvas 渲染引擎。 |

## 歷史回補功能之依賴說明
歷史 Tick 回補（`backfill_history_tmf.py`、`services/tick_export/history_backfill.py`、`services/tick_export/monthly_excel.py`）與換月日曆（`services/tick_export/contract_calendar.py`，僅使用 Python 標準函式庫 `datetime`、`re`、`functools`）**未新增任何外部套件**，僅重用上表既有依賴：

| Package Name | Version | Purpose（於回補功能中的用途） |
|--------------|---------|---------|
| shioaji      | == 1.3.1 | `api.ticks()` 取得 `TMFR1` 歷史 Tick；`api.usage()` 查詢每日流量；`login(fetch_contract=False)` 輕量登入。 |
| pandas       | == 2.2.1 | Tick 重採樣為 1 秒 OHLCV、邊界時間過濾。 |
| pyarrow      | == 25.0.1 | 每日原始 Tick Parquet 快取（`data/ticks_cache/TMFR1_YYYY-MM-DD_ticks.parquet`）。 |
| xlsxwriter   | == 3.2.9 | 常數記憶體串流寫入「一月一工作表」Excel。 |
| openpyxl     | == 3.1.5 | 偵測舊版 Excel 版型（僅讀取工作表名稱）；單元測試驗證輸出內容。 |
| python-dotenv| == 1.0.1 | 載入 `.env` 之 Shioaji 金鑰。 |

系統層級條件：
- **Shioaji 每日流量**：預設 500 MB（近 30 日 API 成交為 0 之帳戶），開盤日 08:00 重置；每筆 Tick 計 40 Bytes。全量回補約需 5 ~ 7 個開盤日分批完成。
- **磁碟空間**：SQLite 全量歷史約 2 GB、Tick 快取約 1 GB、Excel 約 0.4 GB，建議預留 5 GB 以上。
- **記憶體**：回補與 Excel 匯出皆為串流/分塊處理，峰值與總筆數無關（僅取決於 `chunk_size`）；載入全歷史做長期回測（`include_history=True`）則需 4 GB 以上可用記憶體。

## 系統與容器環境依賴
- **Python 版本**: 推薦使用 Python 3.8 ~ 3.12
- **Node.js 版本**: 推薦使用 Node.js 18.0 + (用於 npm 安裝前端靜態依賴)
- **Git 版本**: 推薦使用 Git 2.35 + (用於 `services/automation/github_pages_publisher.py` 自動推送靜態 HTML 至 GitHub Pages)

## GitHub Pages 自動發布模組之依賴說明
靜態儀表板自動發布服務（`services/automation/github_pages_publisher.py`）僅使用 Python 標準函式庫（`urllib.request`、`subprocess`、`shutil`、`json`）與系統層級之 `git` CLI，**未新增任何外部第三方套件**：

| Package Name | Version | Purpose（於自動發布模組中的用途） |
|--------------|---------|---------|
| lightweight-charts | == 5.2.0 | 內嵌於 `kline_dashboard.html` 之獨立前端 K 線與指標圖表渲染核心。 |
| pandas       | == 2.2.1 | 產出 11 種時間週期 K 棒與「布林_ATR基準」策略回測時序。 |
| numpy        | == 1.26.4 | 向量化布林通道與 ATR 指標計算。 |

## 安裝指令

請於 Python 環境中執行以下指令以安裝依賴：

```bash
pip install -r requirements.txt
```

前端依賴安裝指令（選用，已內建 Standalone 獨立封裝檔）：

```bash
cd frontend && npm install lightweight-charts
```
