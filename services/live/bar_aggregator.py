"""
即時動態 K 棒聚合器模組 (services/live/bar_aggregator.py)

負責將 Shioaji 送入之逐筆 Tick（時間戳、成交價、成交量）即時動態聚合為指定週期（如 5 分鐘）之 OHLCV 棒體：
1. 實時維護未收盤浮動棒（In-flight Bar），供前端 WebSocket 高頻更新。
2. 跨越週期邊界時自動封閉 K 棒並觸發收盤事件（Bar-close Event），供策略狀態機運算指標。
3. 嚴密處理盤別邊界、時間戳亂序與跳空邊界情況。

遵循 PEP 8 與 Python Type Hints 規範。
"""

import math
from dataclasses import dataclass
from typing import Optional, Callable, Dict, Any


@dataclass
class BarData:
    """
    即時 K 棒資料結構。

    屬性:
        timestamp (int): K 棒起始時間戳 (Unix Epoch 秒數)。
        open (float): 開盤價。
        high (float): 最高價。
        low (float): 最低價。
        close (float): 最新成交價 (收盤價)。
        volume (int): 累積成交量。
        is_closed (bool): 該根 K 棒是否已收盤封閉。
    """
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: int
    is_closed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """
        轉換為相容於 TradingView Lightweight Charts 之字典格式。

        返回:
            Dict[str, Any]: 包含 time, open, high, low, close, volume, is_closed 之字典。
        """
        return {
            "time": self.timestamp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "is_closed": self.is_closed,
        }


class RealTimeBarAggregator:
    """
    即時 K 棒動態聚合器。
    以常數時間複雜度 O(1) 處理傳入之 Tick，維護當前浮動棒並在週期切換時產出收盤棒。
    """

    def __init__(
        self,
        interval_seconds: int = 300,
        on_bar_update: Optional[Callable[[BarData], None]] = None,
        on_bar_close: Optional[Callable[[BarData], None]] = None,
    ) -> None:
        """
        初始化聚合器。

        參數:
            interval_seconds (int): K 棒週期秒數 (預設 300 秒，即 5 分鐘)。
            on_bar_update (Optional[Callable[[BarData], None]]): 當前浮動棒更新時之回呼函式 (供 WebSocket 推播)。
            on_bar_close (Optional[Callable[[BarData], None]]): K 棒收盤時之回呼函式 (供策略狀態機觸發)。
        """
        self.interval_seconds: int = interval_seconds
        self.on_bar_update: Optional[Callable[[BarData], None]] = on_bar_update
        self.on_bar_close: Optional[Callable[[BarData], None]] = on_bar_close

        self.current_bar: Optional[BarData] = None
        self.last_tick_time: int = 0

    def _get_bar_bucket(self, timestamp: int) -> int:
        """
        計算時間戳所屬之週期起始邊界點 (Bucket)。

        公式:
            bucket = timestamp - (timestamp % interval_seconds)

        參數:
            timestamp (int): Unix Epoch 時間戳 (秒)。

        返回:
            int: 週期起始秒數。
        """
        return timestamp - (timestamp % self.interval_seconds)

    def feed_tick(self, timestamp: int, price: float, volume: int) -> Optional[BarData]:
        """
        接收並處理單筆 Tick 數據。

        參數:
            timestamp (int): 成交時間戳 (Unix Epoch 秒數)。
            price (float): 成交價。
            volume (int): 單筆成交口數。

        返回:
            Optional[BarData]: 若此 Tick 導致前一根 K 棒收盤，則回傳剛收盤之 BarData；否則回傳 None。
        """
        if price <= 0 or volume < 0:
            return None

        # 防禦：過濾時間戳嚴重回溯之過期 Tick (> 60 秒)
        if self.last_tick_time > 0 and timestamp < self.last_tick_time - 60:
            return None

        if timestamp > self.last_tick_time:
            self.last_tick_time = timestamp

        bucket = self._get_bar_bucket(timestamp)
        closed_bar: Optional[BarData] = None

        if self.current_bar is None:
            # 系統啟動後首筆 Tick，初始化當前浮動棒
            self.current_bar = BarData(
                timestamp=bucket,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=volume,
                is_closed=False,
            )
        elif bucket > self.current_bar.timestamp:
            # 跨越週期邊界：原浮動棒封閉收盤
            self.current_bar.is_closed = True
            closed_bar = self.current_bar

            # 觸發收盤事件
            if self.on_bar_close:
                try:
                    self.on_bar_close(closed_bar)
                except Exception as e:
                    pass

            # 建立新一根浮動棒
            self.current_bar = BarData(
                timestamp=bucket,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=volume,
                is_closed=False,
            )
        else:
            # 仍處於當前週期內：增量更新 High, Low, Close, Volume
            bar = self.current_bar
            if price > bar.high:
                bar.high = price
            if price < bar.low:
                bar.low = price
            bar.close = price
            bar.volume += volume

        # 觸發即時浮動棒更新回呼
        if self.on_bar_update and self.current_bar:
            try:
                self.on_bar_update(self.current_bar)
            except Exception:
                pass

        return closed_bar

    def force_close_current_bar(self) -> Optional[BarData]:
        """
        強制封閉當前浮動棒 (用於盤別結束邊界，如 13:45 日盤收盤或 05:00 夜盤收盤)。

        返回:
            Optional[BarData]: 若存在未收盤之 K 棒則封閉並回傳，否則回傳 None。
        """
        if self.current_bar and not self.current_bar.is_closed:
            self.current_bar.is_closed = True
            closed_bar = self.current_bar
            self.current_bar = None
            if self.on_bar_close:
                self.on_bar_close(closed_bar)
            return closed_bar
        return None
