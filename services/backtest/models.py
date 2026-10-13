"""
回測系統資料模型與設定模組 (services/backtest/models.py)

定義交易方向、出場原因、回測參數設定與交易紀錄之強型別資料結構。
遵循 PEP 8 與 Python Type Hints 規範。
"""

from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class PositionSide(str, Enum):
    """持倉方向列舉"""
    LONG = "LONG"
    SHORT = "SHORT"
    NONE = "NONE"


class ExitReason(str, Enum):
    """出場原因列舉"""
    STOP_LOSS = "STOP_LOSS"                    # 盤中觸碰停損點
    ATR_TAKE_PROFIT = "ATR_TAKE_PROFIT"        # ATR 動能指標下穿均線收盤平倉
    DONCHIAN_EXIT = "DONCHIAN_EXIT"            # 唐奇安通道收盤平倉
    SUPERTREND_EXIT = "SUPERTREND_EXIT"        # 超級趨勢線收盤平倉
    ROLLOVER_SETTLEMENT = "ROLLOVER_SETTLEMENT"  # 結算日強制平倉結算


class BacktestConfig(BaseModel):
    """
    回測全域參數設定模型。
    包含資金部位控管、指標計算參數以及交易摩擦成本。
    """
    strategy_name: str = Field(default="布林動能突破", description="策略名稱標籤")
    initial_capital: float = Field(default=105150.0, description="期初總資金 (新台幣)")
    point_value: float = Field(default=10.0, description="每一跳動點數價值 (微台指=10元)")
    margin_req: float = Field(default=35050.0, description="單口所需原始保證金 (新台幣)")
    max_leverage: float = Field(default=7.0, description="帳戶最高允許總槓桿倍數")
    
    # 布林通道設定 (真實成本版)
    bb_length: int = Field(default=30, description="布林通道 SMA 週期長度")
    bb_mult: float = Field(default=1.75, description="布林通道標準差乘數")
    
    # 出場模式設定 (atr / donchian / supertrend)
    exit_mode: str = Field(default="atr", description="出場模式: atr / donchian / supertrend")
    
    # ATR 動能設定
    atr_length: int = Field(default=14, description="ATR 計算週期長度")
    atr_sig_length: int = Field(default=5, description="ATR Signal SMA 週期長度")
    
    # 唐奇安通道設定
    donchian_length: int = Field(default=4, description="唐奇安通道追蹤前 N 根極值")

    # 超級趨勢線設定
    supertrend_length: int = Field(default=10, description="超級趨勢線 ATR 長度")
    supertrend_multiplier: float = Field(default=3.0, description="超級趨勢線 ATR 乘數")

    # 摩擦成本設定 (真實成本版)
    commission_per_order: float = Field(default=18.0, description="單邊單口手續費 (新台幣，預設 18 元)")
    tax_rate: float = Field(default=0.00002, description="期貨交易稅率 (十萬分之二)")
    slippage_points: float = Field(default=0.0, description="單邊進出場滑價點數 (點)")


class ActivePosition(BaseModel):
    """
    當前未平倉在手部位狀態模型。
    """
    side: PositionSide = Field(default=PositionSide.NONE, description="持倉方向")
    qty: int = Field(default=0, description="在手口數")
    entry_price: float = Field(default=0.0, description="進場成交價格")
    entry_time: str = Field(default="", description="進場 K 棒開盤時間")
    sl_price: float = Field(default=0.0, description="停損價格 (Bar 1 之極端價)")
    entry_bar_index: int = Field(default=0, description="進場 K 棒索引位置")


class TradeRecord(BaseModel):
    """
    單筆已完成交易稽核紀錄模型。
    詳細記錄進出場時間、價格、口數、原因與損益明細。
    """
    trade_id: int = Field(..., description="交易編號")
    side: PositionSide = Field(..., description="交易方向 (LONG/SHORT)")
    entry_time: str = Field(..., description="進場時間")
    entry_price: float = Field(..., description="進場成交價 (已計入滑價)")
    exit_time: str = Field(..., description="出場時間")
    exit_price: float = Field(..., description="出場成交價 (已計入滑價)")
    qty: int = Field(..., description="成交口數")
    exit_reason: ExitReason = Field(..., description="出場原因")
    
    gross_pnl: float = Field(..., description="毛損益 (新台幣)")
    fee_total: float = Field(..., description="雙邊手續費總額 (新台幣)")
    tax_total: float = Field(..., description="雙邊期交稅總額 (新台幣)")
    slippage_loss: float = Field(..., description="雙邊滑價損耗總額 (新台幣)")
    net_pnl: float = Field(..., description="淨損益 (扣除所有手續費、稅金與滑價)")
    return_pct: float = Field(..., description="單筆交易報酬率 (%)")
    equity_after: float = Field(..., description="交易完成後帳戶總權益淨值")


class BacktestMetrics(BaseModel):
    """
    回測量化綜合績效評估指標模型。
    """
    initial_capital: float = Field(..., description="期初資金")
    final_equity: float = Field(..., description="期末總權益")
    total_gross_profit: float = Field(default=0.0, description="策略毛利 (未扣手續費與期交稅，新台幣)")
    total_trading_costs: float = Field(default=0.0, description="總手續費與稅金 (新台幣)")
    total_net_profit: float = Field(..., description="真實淨損益 (新台幣)")
    total_return_pct: float = Field(..., description="總報酬率 (%)")
    annualized_return_pct: float = Field(..., description="年化報酬率 (%)")
    total_trades: int = Field(..., description="總交易次數")
    winning_trades: int = Field(..., description="獲利交易次數")
    losing_trades: int = Field(..., description="虧損交易次數")
    win_rate_pct: float = Field(..., description="勝率 (%)")
    profit_factor: float = Field(..., description="賺賠比 (總獲利/總虧損)")
    max_drawdown: float = Field(..., description="最大權益回落金額 (MDD)")
    max_drawdown_pct: float = Field(..., description="最大權益回落幅度 (%)")
    sharpe_ratio: float = Field(..., description="夏普比率 (以日收益率標準差換算)")
    
    trades: List[TradeRecord] = Field(default_factory=list, description="每筆交易明細列表")
    equity_curve: List[Dict[str, Any]] = Field(default_factory=list, description="權益時序曲線數據")
