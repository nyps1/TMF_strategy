"""
技術指標計算模組 (services/backtest/indicators.py)

負責計算布林通道 (Bollinger Bands) 與真實波動區間 (ATR / ATR Signal)。
演算法完全對齊 TradingView 內建指標核心邏輯（包含 Wilder's Smoothing RMA 與母體標準差 ddof=0）。
遵循 PEP 8 與 Python Type Hints 規範。
"""

import numpy as np
import pandas as pd
from typing import Tuple


def calculate_bollinger_bands(
    df: pd.DataFrame,
    length: int = 20,
    mult: float = 2.0,
    price_col: str = "close"
) -> pd.DataFrame:
    """
    計算布林通道中線、上軌與下軌。
    對齊 TradingView ta.sma 與 ta.stdev(close, length) 公式（採 ddof=0 母體標準差）。

    參數:
        df (pd.DataFrame): 包含價格資料之 DataFrame。
        length (int): SMA 週期，預設 20。
        mult (float): 標準差倍數，預設 2.0。
        price_col (str): 計算所依據的欄位名稱，預設 'close'。

    返回:
        pd.DataFrame: 包含 'bb_basis', 'bb_upper', 'bb_lower' 之 DataFrame。
    """
    basis = df[price_col].rolling(window=length).mean()
    # TradingView ta.stdev 預設使用母體標準差 (ddof=0)
    dev = df[price_col].rolling(window=length).std(ddof=0) * mult
    
    df["bb_basis"] = basis
    df["bb_upper"] = basis + dev
    df["bb_lower"] = basis - dev
    return df


def calculate_wilder_rma(series: pd.Series, length: int) -> pd.Series:
    """
    計算 Wilder's Smoothing Running Moving Average (RMA)。
    TradingView ta.rma 公式：alpha = 1 / length。
    首期採用前 length 筆的 SMA 作為初始種子值。

    參數:
        series (pd.Series): 輸入時間序列。
        length (int): 平滑週期長度。

    返回:
        pd.Series: 計算完成之 RMA 序列。
    """
    rma = pd.Series(index=series.index, dtype=float)
    if len(series) < length:
        return rma

    alpha = 1.0 / length
    # 首期以前 length 筆之 SMA 起算
    initial_sma = series.iloc[:length].mean()
    rma.iloc[length - 1] = initial_sma

    for i in range(length, len(series)):
        rma.iloc[i] = alpha * series.iloc[i] + (1.0 - alpha) * rma.iloc[i - 1]

    return rma


def calculate_atr(
    df: pd.DataFrame,
    length: int = 14,
    sig_length: int = 9
) -> pd.DataFrame:
    """
    計算真實波動區間 (ATR) 與 ATR 訊號線 (ATR Signal)。
    完全對齊 TradingView ta.atr(length) 與 ta.sma(atr_val, sig_length)。

    參數:
        df (pd.DataFrame): 包含 'high', 'low', 'close' 欄位之 DataFrame。
        length (int): ATR 週期，預設 14。
        sig_length (int): ATR Signal SMA 週期，預設 9。

    返回:
        pd.DataFrame: 包含 'atr_val', 'atr_sig' 欄位之 DataFrame。
    """
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["tr"] = true_range

    # TradingView ta.atr 使用 RMA 平滑 True Range
    df["atr_val"] = calculate_wilder_rma(true_range, length)
    # ATR Signal 為 ATR 之簡單移動平均 (SMA)
    df["atr_sig"] = df["atr_val"].rolling(window=sig_length).mean()
    
    return df


def populate_all_indicators(
    df: pd.DataFrame,
    bb_length: int = 20,
    bb_mult: float = 2.0,
    atr_length: int = 14,
    atr_sig_length: int = 9
) -> pd.DataFrame:
    """
    批次計算策略所需之所有技術指標並附加至 DataFrame。

    參數:
        df (pd.DataFrame): 輸入行情 DataFrame (需含 open, high, low, close)。
        bb_length (int): 布林通道長度。
        bb_mult (float): 布林通道標準差乘數。
        atr_length (int): ATR 長度。
        atr_sig_length (int): ATR Signal 均線長度。

    返回:
        pd.DataFrame: 附加完整技術指標數值之 DataFrame。
    """
    df = calculate_bollinger_bands(df, length=bb_length, mult=bb_mult)
    df = calculate_atr(df, length=atr_length, sig_length=atr_sig_length)
    return df
