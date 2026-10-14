# TMF_strategy

微型台指期 (TMF) 動能布林突破量化回測與高頻時序交易系統

## 專案概述 (Overview)
本專案採用永豐證券 Shioaji API 與 SQLite 高頻時序資料庫，針對台灣期交所微型台指期 (TMFJ6) 進行逐秒/五分 K 棒的事件驅動回測，並以 TradingView 專業規格建構多週期互動式 K 線與累計損益儀表板。

## 策略核心邏輯 (Core Strategy)
- **週期**: 5 分鐘 K 線 (5m)
- **動能突破**: 收盤突破布林通道上/下緣 (\( \text{Basis} \pm 1.75\sigma \), 週期 30)
- **拉回確認**: 第二根 K 棒於突破棒頂部/底部 1/4 區間進行支撐/壓力確認
- **風控部位管理**: 動態計算安全下單口數 (Safe Qty)，嚴格控管 7.0 倍槓桿與保證金邊界
- **出場機制**: 盤中觸及前根極值停損，收盤跌破 ATR 動能訊號停利

## 專案核心分層架構 (Architecture Layout)
- `strategies/`: **【決策人物策略庫】**，供決策者專注構思、撰寫與微調策略（包含依特徵命名的 `bollinger_pullback_strategy.py`，通用風控與指標抽離至 `strategies/common/`）
- `data/`: **【數據中心】**，包含集中存放的 Excel 歷史檔（`data/excel/`）、Parquet 高速快取（`data/parquet/`）與 SQLite 資料庫（`data/market_data.db`）
- `services/`: **【底層基礎服務層】**，負責高頻時序存取、即時行情聚合、WebSocket 廣播與增量同步管線
- `backend/`: **【API 路由層】**，FastAPI 與 WebSocket 即時決策推播
- `archive/`: **【封存歸檔區】**，集中收納歷史報表、早期測試腳本與診斷小工具
- `daily_sync_tmf10.py`: 每日 06:00 與 14:00 自動增量同步微台秒 K 數據之主程式
- `backfill_history_tmf.py`: 歷史 Tick 回補 CLI（Shioaji `TMFR1` 近月連續合約 → SQLite + 月份分頁 Excel），可中斷、可續跑
- `generate_kline_dashboard.py`: 編譯生成多週期 K 線與回測淨值儀表板之主程式
- `kline_dashboard.html`: 核心互動式 TradingView 視覺化儀表板
- `微台10_每秒逐筆歷史紀錄.xlsx - 捷徑.lnk`: 根目錄快速開啟 Excel 歷史紀錄之捷徑入口

## 歷史資料回補 (History Backfill)
- **資料來源與範圍**: Shioaji `api.ticks()` 對 `TMFR1`（近月連續）可取得微型臺指期貨自上市日 `2024-07-29` 起之逐筆 Tick（更早日期回傳 0 筆）。
- **儲存位置**: SQLite `tmf10_second_bars`，回補列之 `symbol = 'TMFR1'`，與每日增量同步之實體合約（如 `TMFJ6`）區隔；寫入前以邊界時間過濾，不會覆蓋既有實體合約資料。
- **Excel 版型**: `data/excel/微台10_每秒逐筆歷史紀錄.xlsx` 改為「一月一工作表」，工作表以日曆月份命名（`26-09` 代表 2026/09；Excel 禁止工作表名稱含 `/`，故以 `-` 取代）。單月超過 Excel 單表上限（1,048,575 列）時以 `26-03 (2)` 接續。舊版單一工作表檔案於首次改版前備份為 `*.xlsx.legacy.bak`。
- **每日流量配額**: Shioaji 預設每日 500 MB（開盤日 08:00 重置），每筆 Tick 計 40 Bytes。回補程式內建配額守門並保留 90 MB 給每日同步；額度用盡時自動暫停，次日再次執行即可續跑（進度記錄於 `data/backfill_state.json`）。
- **下游範圍隔離**: `KlineService(include_history=False)` 為預設，儀表板與回測不納入歷史回補區段；需要長期回測時以 `KlineService(db_path, include_history=True)` 載入（資料量達千萬筆，需預留記憶體）。

```bash
python backfill_history_tmf.py                 # 回補全部缺漏區間（額度用盡自動暫停）
python backfill_history_tmf.py --max-days 3    # 試跑
python backfill_history_tmf.py --excel-only    # 不連線，僅由 SQLite 重建 Excel
```

## 環境依賴與安裝 (Dependencies)
詳見 [DEPENDENCIES.md](DEPENDENCIES.md)。
```bash
pip install -r requirements.txt
```
