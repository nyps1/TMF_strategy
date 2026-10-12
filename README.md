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

## 策略數據與檔案結構
- `services/`: 核心服務層 (行情載入、回測引擎、指標運算、SQLite 時序管理)
- `backend/`: FastAPI 即時訂閱與 API 路由分發層
- `frontend/`: Lightweight Charts v5.2.0 視覺化元件庫
- `trades.csv`: 369 筆真實交易紀錄明細
- `kline_dashboard.html`: 互動式雙圖儀表板 (包含建倉平倉 Markers 與 0 軸 Baseline 績效圖)
- `backtest_report.html`: 完整統計指標報告

## 環境依賴與安裝 (Dependencies)
詳見 [DEPENDENCIES.md](DEPENDENCIES.md)。
```bash
pip install -r requirements.txt
```
