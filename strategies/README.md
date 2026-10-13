# 決策人物策略庫 (Strategies Guide)

本資料夾為專案的**量化決策核心層**。您作為量化交易的決策者，可以在此專注於發想、撰寫、調優回測與實盤交易邏輯，無須關心底層的 WebSocket 串流、資料庫讀寫或繪圖渲染細節。

---

## 一、 目錄結構說明

```
strategies/
├── common/                               # 通用決策積木庫 (不可重造輪子)
│   ├── base.py                           # 基礎策略抽象類別 (BaseStrategy)
│   ├── models.py                         # 訊號事件、持倉方向與設定模型 (Pydantic)
│   ├── indicators.py                     # 通用技術指標 (布林通道, ATR, EMA 等)
│   └── risk.py                           # 通用風控模組 (觸價停損, 動態追蹤, 結算平倉)
│
├── bollinger_pullback_strategy.py        # 【核心策略】動態布林突破策略 (嚴格拉回確認版)
├── __init__.py                           # 模組統一匯出入口
└── README.md                             # 本指引文件
```

---

## 二、 如何新增一個全新策略？

您只需要在 `strategies/` 目錄下建立一個以策略特徵命名的 Python 檔案（例如：`macd_trend_strategy.py`），並繼承 `BaseStrategy`：

```python
from typing import Optional, List
from strategies.common.base import BaseStrategy
from strategies.common.models import StrategyConfig, SignalEvent, PositionSide
from strategies.common.risk import check_intra_bar_stop_loss
from services.live.bar_aggregator import BarData

class MyNewStrategy(BaseStrategy):
    """自訂新策略決策實體"""
    
    def reset(self) -> None:
        """重置指標快取與狀態機"""
        pass
        
    def on_tick(self, timestamp: int, price: float, current_side: PositionSide, 
                entry_price: float, stop_loss_price: float) -> Optional[SignalEvent]:
        """盤中即時 Tick 觸價停損 (每秒微秒級觸發)"""
        return check_intra_bar_stop_loss(timestamp, price, current_side, stop_loss_price)

    def on_bar_close(self, bar: BarData, history_bars: List[BarData], 
                     current_side: PositionSide) -> Optional[SignalEvent]:
        """5 分鐘 K 棒收盤決策 (判斷進出場訊號)"""
        # 在此編寫您的進出場邏輯
        return None
```

---

## 三、 常見通用積木調用方法

* **布林通道**：`from strategies.common.indicators import calculate_bollinger_bands`
* **真實波動區間 ATR**：`from strategies.common.indicators import calculate_atr`
* **指數移動平均 EMA**：`from strategies.common.indicators import calculate_ema`
* **動態追蹤停利**：`from strategies.common.risk import check_trailing_take_profit`
