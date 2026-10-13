"""
量化指標共用計算模組 (strategies/common/indicators.py)

提供決策者常用的技術指標計算工具（純函數式設計，高執行效能與無狀態）：
1. calculate_bollinger_bands: 滾動布林通道 (中軌, 上軌, 下軌, 標準差)
2. calculate_atr: 真實波動區間 (Average True Range)
3. calculate_ema: 指數移動平均線 (Exponential Moving Average)

遵循 PEP 8 與 Python Type Hints 規範。
"""

import math
from typing import List, Dict, Tuple, Any
from services.live.bar_aggregator import BarData


def calculate_bollinger_bands(
    bars: List[BarData],
    length: int = 20,
    num_std: float = 2.0
) -> Dict[str, float]:
    """
    計算布林通道 (Bollinger Bands)。

    參數:
        bars (List[BarData]): K 棒序列。
        length (int): 計算週期長度，預設 20。
        num_std (float): 標準差倍數，預設 2.0。

    返回:
        Dict[str, float]: 包含 mid_band, upper_band, lower_band, std 之指標字典。
    """
    if len(bars) < length:
        return {"mid_band": 0.0, "upper_band": 0.0, "lower_band": 0.0, "std": 0.0}

    recent_closes = [b.close for b in bars[-length:]]
    mean_c = sum(recent_closes) / length
    variance = sum((c - mean_c) ** 2 for c in recent_closes) / length
    std_c = math.sqrt(variance)

    upper_band = mean_c + num_std * std_c
    lower_band = mean_c - num_std * std_c

    return {
        "mid_band": mean_c,
        "upper_band": upper_band,
        "lower_band": lower_band,
        "std": std_c,
    }


def calculate_atr(
    bars: List[BarData],
    length: int = 14
) -> float:
    """
    計算真實波動區間 (Average True Range, ATR)。

    參數:
        bars (List[BarData]): K 棒序列。
        length (int): 計算週期長度，預設 14。

    返回:
        float: 當前 ATR 波動值。若數據不足回傳 0.0。
    """
    if len(bars) < length + 1:
        return 0.0

    tr_list: List[float] = []
    for i in range(len(bars) - length, len(bars)):
        curr = bars[i]
        prev = bars[i - 1]
        tr = max(
            curr.high - curr.low,
            abs(curr.high - prev.close),
            abs(curr.low - prev.close),
        )
        tr_list.append(tr)

    return sum(tr_list) / len(tr_list)


def calculate_ema(
    prices: List[float],
    length: int = 200
) -> float:
    """
    計算指數移動平均線 (Exponential Moving Average, EMA)。

    參數:
        prices (List[float]): 價格數列 (按時間升冪排列)。
        length (int): EMA 週期長度。

    返回:
        float: 當前 EMA 數值。若數據不足回傳 0.0。
    """
    if len(prices) < length:
        return 0.0

    multiplier = 2.0 / (length + 1)
    ema = sum(prices[:length]) / length  # 初始以 SMA 作為種子

    for p in prices[length:]:
        ema = (p - ema) * multiplier + ema

    return ema


def calculate_donchian_channel(
    bars: List[BarData],
    length: int = 4,
    exclude_current: bool = True
) -> Dict[str, float]:
    """
    計算唐奇安通道 (Donchian Channel)。

    參數:
        bars (List[BarData]): K 棒序列。
        length (int): 追蹤週期根數，預設 4。
        exclude_current (bool): 是否排除當前正在評估的這根 K 棒 (出場線通常以前 N 根已確認棒為準)。

    返回:
        Dict[str, float]: 包含 upper (最高點), lower (最低點), mid (中軸) 之字典。
    """
    sample = bars[:-1] if exclude_current and len(bars) > 1 else bars
    if len(sample) < length:
        return {"upper": 0.0, "lower": 0.0, "mid": 0.0}

    recent_bars = sample[-length:]
    upper = max(b.high for b in recent_bars)
    lower = min(b.low for b in recent_bars)
    mid = (upper + lower) / 2.0

    return {
        "upper": upper,
        "lower": lower,
        "mid": mid,
    }


def calculate_supertrend(
    bars: List[BarData],
    length: int = 10,
    multiplier: float = 3.0
) -> Dict[str, Any]:
    """
    計算超級趨勢線 (SuperTrend Indicator)。
    具備單向不可逆防護 (Ratchet) 之趨勢與移動停利停損軌道。

    參數:
        bars (List[BarData]): 歷史 K 棒序列。
        length (int): ATR 計算週期，預設 10。
        multiplier (float): ATR 乘數，預設 3.0。

    返回:
        Dict[str, Any]: 包含:
            - supertrend (float): 當前超級趨勢防守數值
            - direction (int): 當前趨勢方向 (1: 多頭, -1: 空頭)
            - upper_band (float): 基礎上軌
            - lower_band (float): 基礎下軌
    """
    if len(bars) < length + 2:
        return {"supertrend": 0.0, "direction": 1, "upper_band": 0.0, "lower_band": 0.0}

    # 1. 逐步計算 ATR 與 SuperTrend 數列
    tr_list: List[float] = [0.0]
    for i in range(1, len(bars)):
        c = bars[i]
        p = bars[i - 1]
        tr = max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
        tr_list.append(tr)

    # 初始 ATR 以 SMA 初始化
    st_val = 0.0
    direction = 1  # 1 為多頭, -1 為空頭
    prev_upper = 0.0
    prev_lower = 0.0

    for i in range(length, len(bars)):
        curr = bars[i]
        prev = bars[i - 1]
        atr_val = sum(tr_list[i - length + 1 : i + 1]) / length

        median = (curr.high + curr.low) / 2.0
        basic_upper = median + (multiplier * atr_val)
        basic_lower = median - (multiplier * atr_val)

        # 單向不可逆收緊機制 (Ratchet)
        if i == length:
            final_upper = basic_upper
            final_lower = basic_lower
        else:
            final_upper = basic_upper if basic_upper < prev_upper or prev.close > prev_upper else prev_upper
            final_lower = basic_lower if basic_lower > prev_lower or prev.close < prev_lower else prev_lower

        # 判斷多空方向轉折
        if direction == 1:
            if curr.close < final_lower:
                direction = -1
                st_val = final_upper
            else:
                st_val = final_lower
        else:
            if curr.close > final_upper:
                direction = 1
                st_val = final_lower
            else:
                st_val = final_upper

        prev_upper = final_upper
        prev_lower = final_lower

    return {
        "supertrend": st_val,
        "direction": direction,
        "upper_band": prev_upper,
        "lower_band": prev_lower,
    }

