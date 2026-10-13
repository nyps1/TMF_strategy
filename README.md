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
- `generate_kline_dashboard.py`: 編譯生成多週期 K 線與回測淨值儀表板之主程式
- `kline_dashboard.html`: 核心互動式 TradingView 視覺化儀表板
- `微台10_每秒逐筆歷史紀錄.xlsx - 捷徑.lnk`: 根目錄快速開啟 Excel 歷史紀錄之捷徑入口

## 環境依賴與安裝 (Dependencies)
詳見 [DEPENDENCIES.md](DEPENDENCIES.md)。
```bash
pip install -r requirements.txt
```
