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

## 系統與容器環境依賴
- **Python 版本**: 推薦使用 Python 3.8 ~ 3.12
- **Node.js 版本**: 推薦使用 Node.js 18.0 + (用於 npm 安裝前端靜態依賴)

## 安裝指令

請於 Python 環境中執行以下指令以安裝依賴：

```bash
pip install -r requirements.txt
```

前端依賴安裝指令（選用，已內建 Standalone 獨立封裝檔）：

```bash
cd frontend && npm install lightweight-charts
```


