"""
策略數據模型與參數定義 (strategies/common/models.py)

提供決策者配置策略參數、訊號事件與倉位狀態之標準資料模型。
遵循 PEP 8 與 Python Type Hints 規範。
"""

from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class PositionSide(str, Enum):
    """持倉方向枚舉"""
    NONE = "NONE"
    LONG = "LONG"
    SHORT = "SHORT"


class SignalType(str, Enum):
    """交易訊號類型枚舉"""
    NONE = "NONE"
    BUY_OPEN = "BUY_OPEN"        # 做多進場
    SELL_OPEN = "SELL_OPEN"      # 放空進場
    TAKE_PROFIT = "TAKE_PROFIT"  # 停利平倉
    STOP_LOSS = "STOP_LOSS"      # 停損平倉
    SETTLEMENT = "SETTLEMENT"    # 到期結算平倉


class SignalEvent(BaseModel):
    """
    交易訊號事件結構。

    屬性:
        timestamp (int): 訊號產生之 Unix 時間戳 (秒)。
        time_str (str): 可讀時間字串 (YYYY-MM-DD HH:MM:SS)。
        signal_type (SignalType): 訊號類型。
        side (PositionSide): 操作方向。
        price (float): 觸發價格。
        stop_loss_price (float): 初始停損價格。
        reason (str): 觸發原因說明。
        extra (Dict[str, Any]): 附加分析指標數據 (例如 ATR, 布林帶上下軌等)。
    """
    timestamp: int
    time_str: str = ""
    signal_type: SignalType
    side: PositionSide
    price: float
    stop_loss_price: float = 0.0
    reason: str = ""
    extra: Dict[str, Any] = Field(default_factory=dict)


class StrategyConfig(BaseModel):
    """
    量化策略配置模型 (決策者調控面板)。

    此模型為決策者構思回測與實盤交易之核心控制介面。
    所有回測假設、指標長度、停利停損倍數與風控參數皆在此統一設定。
    """
    strategy_name: str = "布林動能突破"
    symbol: str = "TMFJ6"
    interval: str = "5m"

    # 指標與形態參數 (決策者可自由調整)
    bb_length: int = Field(default=20, description="布林通道週期長度 (根數)")
    bb_std: float = Field(default=2.0, description="布林通道標準差倍數")
    atr_length: int = Field(default=14, description="真實波動區間 (ATR) 計算週期")
    atr_tp_multiplier: float = Field(default=2.0, description="動態 ATR 停利倍數")
    pullback_ratio: float = Field(default=0.25, description="嚴格拉回確認閥值 (如頂部/底部 1/4 區域)")

    # 資金管理與風控參數 (決策者風控配置)
    initial_capital: float = Field(default=105150.0, description="初始本金 (TWD)")
    max_leverage: float = Field(default=7.0, description="最大槓桿上限倍數")
    margin_req: float = Field(default=35050.0, description="微型台指期單口保證金 (TWD)")
    point_value: float = Field(default=10.0, description="每點價值 (TWD)")

    # 交易摩擦成本 (實證設定)
    fee_per_contract: float = Field(default=12.5, description="單邊手續費 (TWD)")
    tax_rate: float = Field(default=0.00002, description="期交稅率 (十萬分之二)")
    slippage_points: float = Field(default=1.0, description="單邊估計滑價點數")
