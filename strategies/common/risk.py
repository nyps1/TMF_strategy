"""
量化風控與出場共用模組 (strategies/common/risk.py)

提供決策者通用的風險控制與出場判定元件：
1. check_intra_bar_stop_loss: 盤中即時 Tick 觸價停損檢測
2. check_trailing_take_profit: 收盤動態 ATR 追蹤停利判定
3. is_third_wednesday_settlement: 台指期每月第 3 個星期三結算日強制平倉判定

遵循 PEP 8 與 Python Type Hints 規範。
"""

from datetime import datetime
from typing import Optional, Tuple
from .models import PositionSide, SignalType, SignalEvent


def is_third_wednesday_settlement(timestamp: int) -> bool:
    """
    檢核時間戳是否為台指期結算日 (每月第 3 個星期三) 13:00。

    參數:
        timestamp (int): Unix Epoch 時間戳 (秒)。

    返回:
        bool: 若當前時間為結算時段則回傳 True。
    """
    dt = datetime.fromtimestamp(timestamp)
    if dt.weekday() == 2 and dt.hour == 13 and dt.minute == 0:
        day = dt.day
        if 15 <= day <= 21:
            return True
    return False


def check_intra_bar_stop_loss(
    timestamp: int,
    price: float,
    current_side: PositionSide,
    stop_loss_price: float,
) -> Optional[SignalEvent]:
    """
    盤中 Tick 觸價即時停損檢核 (Intra-bar Tick Hard Stop)。

    參數:
        timestamp (int): 成交時間戳。
        price (float): 當前市場成交價。
        current_side (PositionSide): 當前持倉方向。
        stop_loss_price (float): 設定之停損價格。

    返回:
        Optional[SignalEvent]: 若擊穿停損價回傳 STOP_LOSS 訊號，否則回傳 None。
    """
    if current_side == PositionSide.NONE or stop_loss_price <= 0:
        return None

    dt_str = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

    # 多頭停損檢核
    if current_side == PositionSide.LONG and price <= stop_loss_price:
        return SignalEvent(
            timestamp=timestamp,
            time_str=dt_str,
            signal_type=SignalType.STOP_LOSS,
            side=PositionSide.LONG,
            price=price,
            reason="盤中觸價停損 (Intra-bar Stop Loss)",
        )

    # 空頭停損檢核
    if current_side == PositionSide.SHORT and price >= stop_loss_price:
        return SignalEvent(
            timestamp=timestamp,
            time_str=dt_str,
            signal_type=SignalType.STOP_LOSS,
            side=PositionSide.SHORT,
            price=price,
            reason="盤中觸價停損 (Intra-bar Stop Loss)",
        )

    return None


def check_trailing_take_profit(
    timestamp: int,
    close_price: float,
    current_side: PositionSide,
    highest_since_entry: float,
    lowest_since_entry: float,
    atr: float,
    atr_multiplier: float = 2.0,
) -> Optional[SignalEvent]:
    """
    K 棒收盤動態 ATR 追蹤停利判定 (Bar-Close Dynamic Trailing Exit)。

    參數:
        timestamp (int): 當前 K 棒收盤時間戳。
        close_price (float): 當前 K 棒收盤價。
        current_side (PositionSide): 當前持倉方向。
        highest_since_entry (float): 進場以來的最高價。
        lowest_since_entry (float): 進場以來的最低價。
        atr (float): 當前 ATR 波動度。
        atr_multiplier (float): ATR 追蹤停利倍數。

    返回:
        Optional[SignalEvent]: 若跌破/漲破動態追蹤線回傳 TAKE_PROFIT 訊號，否則回傳 None。
    """
    if current_side == PositionSide.NONE or atr <= 0:
        return None

    dt_str = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

    if current_side == PositionSide.LONG:
        tp_trigger = highest_since_entry - (atr_multiplier * atr)
        if close_price < tp_trigger:
            return SignalEvent(
                timestamp=timestamp,
                time_str=dt_str,
                signal_type=SignalType.TAKE_PROFIT,
                side=PositionSide.LONG,
                price=close_price,
                reason=f"收盤動態 ATR 停利 (Close {close_price:.1f} < TP {tp_trigger:.1f})",
                extra={"tp_trigger": tp_trigger, "atr": atr},
            )

    elif current_side == PositionSide.SHORT:
        tp_trigger = lowest_since_entry + (atr_multiplier * atr)
        if close_price > tp_trigger:
            return SignalEvent(
                timestamp=timestamp,
                time_str=dt_str,
                signal_type=SignalType.TAKE_PROFIT,
                side=PositionSide.SHORT,
                price=close_price,
                reason=f"收盤動態 ATR 停利 (Close {close_price:.1f} > TP {tp_trigger:.1f})",
                extra={"tp_trigger": tp_trigger, "atr": atr},
            )

    return None
