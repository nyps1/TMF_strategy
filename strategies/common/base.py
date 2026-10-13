"""
策略基底介面抽象類別 (strategies/common/base.py)

提供所有量化策略之核心生命週期介面：
1. on_bar_close: 當 5 分 K 棒收盤時觸發，供決策者評估形態突破、拉回確認與動能停利。
2. on_tick: 當最新市場 Tick 到達時觸發，供盤中即時觸價停損監控。
3. reset: 重置內部狀態機指標快取。

遵循 PEP 8 與 Python Type Hints 規範。
"""

from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any
from services.live.bar_aggregator import BarData
from .models import StrategyConfig, SignalEvent, PositionSide


class BaseStrategy(ABC):
    """
    量化策略抽象基底類別 (Strategy Pattern)。
    決策者若欲構思全新策略，僅需繼承此類別並實作 on_bar_close 與 on_tick。
    """

    def __init__(self, config: Optional[StrategyConfig] = None) -> None:
        """
        初始化策略。

        參數:
            config (Optional[StrategyConfig]): 策略配置實例。若未提供則使用預設配置。
        """
        self.config: StrategyConfig = config or StrategyConfig()

    @abstractmethod
    def reset(self) -> None:
        """重置策略內部狀態機與指標記憶體快取。"""
        pass

    @abstractmethod
    def on_bar_close(
        self,
        bar: BarData,
        history_bars: List[BarData],
        current_side: PositionSide,
    ) -> Optional[SignalEvent]:
        """
        K 棒收盤事件處理常式 (Bar-Close Event Handler)。

        參數:
            bar (BarData): 剛收盤之 K 棒數據。
            history_bars (List[BarData]): 歷史 K 棒序列 (含當前 bar，按時間升冪排序)。
            current_side (PositionSide): 當前持倉方向 (NONE / LONG / SHORT)。

        返回:
            Optional[SignalEvent]: 若產生交易決策訊號則回傳 SignalEvent，否則回傳 None。
        """
        pass

    @abstractmethod
    def on_tick(
        self,
        timestamp: int,
        price: float,
        current_side: PositionSide,
        entry_price: float,
        stop_loss_price: float,
    ) -> Optional[SignalEvent]:
        """
        即時 Tick 盤中觸價事件處理常式 (Intra-bar Tick Handler)。

        參數:
            timestamp (int): 當前成交時間戳 (秒)。
            price (float): 當前市場最新成交價。
            current_side (PositionSide): 當前持倉方向。
            entry_price (float): 進場成本價。
            stop_loss_price (float): 當前設定之停損觸發價格。

        返回:
            Optional[SignalEvent]: 若盤中價格擊穿停損價則立即發出 STOP_LOSS 訊號，否則回傳 None。
        """
        pass

    def get_strategy_info(self) -> Dict[str, Any]:
        """
        取得策略中繼資訊與配置摘要。

        返回:
            Dict[str, Any]: 策略基本資訊字典。
        """
        return {
            "name": self.config.strategy_name,
            "symbol": self.config.symbol,
            "interval": self.config.interval,
            "config": self.config.model_dump(),
        }
