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


def check_donchian_exit(
    timestamp: int,
    close_price: float,
    current_side: PositionSide,
    entry_price: float,
    donchian_lower: float,
    donchian_upper: float,
) -> Optional[SignalEvent]:
    """
    唐奇安通道 (前 N 根低/高點) 收盤出場判定。

    參數:
        timestamp (int): 當前 K 棒收盤時間戳。
        close_price (float): 當前 K 棒收盤價。
        current_side (PositionSide): 當前持倉方向。
        entry_price (float): 進場成本價。
        donchian_lower (float): 前 N 根最低價 (多頭防守線)。
        donchian_upper (float): 前 N 根最高價 (空頭防守線)。

    返回:
        Optional[SignalEvent]: 若跌破前低或漲破前高則回傳平倉訊號。
    """
    if current_side == PositionSide.NONE:
        return None

    dt_str = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

    if current_side == PositionSide.LONG and donchian_lower > 0:
        if close_price < donchian_lower:
            # 判斷是獲利平倉還是停損平倉
            sig_type = SignalType.TAKE_PROFIT if close_price >= entry_price else SignalType.STOP_LOSS
            return SignalEvent(
                timestamp=timestamp,
                time_str=dt_str,
                signal_type=sig_type,
                side=PositionSide.LONG,
                price=close_price,
                reason=f"唐奇安通道平倉 (Close {close_price:.1f} < 前低 {donchian_lower:.1f})",
                extra={"donchian_lower": donchian_lower},
            )

    elif current_side == PositionSide.SHORT and donchian_upper > 0:
        if close_price > donchian_upper:
            sig_type = SignalType.TAKE_PROFIT if close_price <= entry_price else SignalType.STOP_LOSS
            return SignalEvent(
                timestamp=timestamp,
                time_str=dt_str,
                signal_type=sig_type,
                side=PositionSide.SHORT,
                price=close_price,
                reason=f"唐奇安通道平倉 (Close {close_price:.1f} > 前高 {donchian_upper:.1f})",
                extra={"donchian_upper": donchian_upper},
            )

    return None


def check_supertrend_exit(
    timestamp: int,
    close_price: float,
    current_side: PositionSide,
    entry_price: float,
    supertrend_val: float,
    direction: int,
) -> Optional[SignalEvent]:
    """
    超級趨勢線 (SuperTrend) 單向不可逆收盤出場判定。

    參數:
        timestamp (int): 當前 K 棒收盤時間戳。
        close_price (float): 當前 K 棒收盤價。
        current_side (PositionSide): 當前持倉方向。
        entry_price (float): 進場成本價。
        supertrend_val (float): 當前超級趨勢防守價位。
        direction (int): SuperTrend 趨勢方向 (1: 多頭, -1: 空頭)。

    返回:
        Optional[SignalEvent]: 若多頭跌破或空頭漲破 SuperTrend 則回傳平倉訊號。
    """
    if current_side == PositionSide.NONE or supertrend_val <= 0:
        return None

    dt_str = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

    # 多頭部位持倉中，若 SuperTrend 方向反轉為空頭 (-1) 或收盤跌破 SuperTrend 防守線
    if current_side == PositionSide.LONG and (direction == -1 or close_price < supertrend_val):
        sig_type = SignalType.TAKE_PROFIT if close_price >= entry_price else SignalType.STOP_LOSS
        return SignalEvent(
            timestamp=timestamp,
            time_str=dt_str,
            signal_type=sig_type,
            side=PositionSide.LONG,
            price=close_price,
            reason=f"超級趨勢線平倉 (Close {close_price:.1f} < ST {supertrend_val:.1f})",
            extra={"supertrend": supertrend_val, "direction": direction},
        )

    # 空頭部位持倉中，若 SuperTrend 方向反轉為多頭 (1) 或收盤漲破 SuperTrend 防守線
    elif current_side == PositionSide.SHORT and (direction == 1 or close_price > supertrend_val):
        sig_type = SignalType.TAKE_PROFIT if close_price <= entry_price else SignalType.STOP_LOSS
        return SignalEvent(
            timestamp=timestamp,
            time_str=dt_str,
            signal_type=sig_type,
            side=PositionSide.SHORT,
            price=close_price,
            reason=f"超級趨勢線平倉 (Close {close_price:.1f} > ST {supertrend_val:.1f})",
            extra={"supertrend": supertrend_val, "direction": direction},
        )

    return None

