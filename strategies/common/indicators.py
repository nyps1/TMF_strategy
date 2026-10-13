"""
量化指標共用計算模組 (strategies/common/indicators.py)

提供決策者常用的技術指標計算工具（純函數式設計，高執行效能與無狀態）：
1. calculate_bollinger_bands: 滾動布林通道 (中軌, 上軌, 下軌, 標準差)
2. calculate_atr: 真實波動區間 (Average True Range)
3. calculate_ema: 指數移動平均線 (Exponential Moving Average)

遵循 PEP 8 與 Python Type Hints 規範。
"""

import math
from typing import List, Dict, Tuple
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
