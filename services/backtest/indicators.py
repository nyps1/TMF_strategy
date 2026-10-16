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


def calculate_donchian(
    df: pd.DataFrame,
    length: int = 4
) -> pd.DataFrame:
    """
    計算唐奇安通道 (前 N 根已收盤棒之極端值)。
    以 shift(1) 排除當前評估棒，避免 Look-ahead Bias。

    參數:
        df (pd.DataFrame): 包含 'high', 'low' 欄位之 DataFrame。
        length (int): 追蹤長度，預設 4。

    返回:
        pd.DataFrame: 包含 'donchian_high', 'donchian_low' 之 DataFrame。
    """
    df["donchian_high"] = df["high"].shift(1).rolling(window=length).max()
    df["donchian_low"] = df["low"].shift(1).rolling(window=length).min()
    return df


def calculate_supertrend_series(
    df: pd.DataFrame,
    length: int = 10,
    multiplier: float = 3.0
) -> pd.DataFrame:
    """
    向量化與滾動計算超級趨勢線 (SuperTrend)。

    參數:
        df (pd.DataFrame): 包含 'high', 'low', 'close', 'atr_val' 欄位之 DataFrame。
        length (int): ATR 計算長度。
        multiplier (float): 乘數。

    返回:
        pd.DataFrame: 包含 'supertrend_val', 'supertrend_dir' 欄位之 DataFrame。
    """
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()
    
    # 確保有 atr_val
    if "atr_val" not in df:
        df = calculate_atr(df, length=length, sig_length=5)
    atr = df["atr_val"].to_numpy()

    n = len(df)
    st_val = np.zeros(n)
    st_dir = np.ones(n, dtype=int)  # 1: 多頭, -1: 空頭

    prev_upper = 0.0
    prev_lower = 0.0
    direction = 1

    for i in range(1, n):
        if np.isnan(atr[i]) or atr[i] <= 0:
            continue

        median = (high[i] + low[i]) / 2.0
        basic_upper = median + (multiplier * atr[i])
        basic_lower = median - (multiplier * atr[i])

        if i == 1:
            final_upper = basic_upper
            final_lower = basic_lower
        else:
            final_upper = basic_upper if basic_upper < prev_upper or close[i - 1] > prev_upper else prev_upper
            final_lower = basic_lower if basic_lower > prev_lower or close[i - 1] < prev_lower else prev_lower

        if direction == 1:
            if close[i] < final_lower:
                direction = -1
                current_st = final_upper
            else:
                current_st = final_lower
        else:
            if close[i] > final_upper:
                direction = 1
                current_st = final_lower
            else:
                current_st = final_upper

        st_val[i] = current_st
        st_dir[i] = direction
        prev_upper = final_upper
        prev_lower = final_lower

    df["supertrend_val"] = st_val
    df["supertrend_dir"] = st_dir
    return df


def calculate_htf_ma_slopes(
    df: pd.DataFrame,
    ma_length: int = 20
) -> pd.DataFrame:
    """
    計算大週期（30 分 K 與 1 小時 K）之移動平均線（SMA）與無偷看未來（No Look-Ahead Bias）之斜率序列。
    - 30 分 K（標籤為 T）涵蓋 [T, T+29m59s]，其完整收盤對應之 5 分 K 時間戳為 T + 25m。
    - 1 小時 K（標籤為 T）涵蓋 [T, T+59m59s]，其完整收盤對應之 5 分 K 時間戳為 T + 55m。
    透過 pd.merge_asof(..., direction='backward') 將已完整收盤之大週期均線斜率對齊回 5 分 K。

    參數:
        df (pd.DataFrame): 包含 'ts' (或 'datetime') 與 'close' 欄位之 5 分 K DataFrame。
        ma_length (int): 大週期 SMA 均線週期長度，預設 20。

    返回:
        pd.DataFrame: 附加 'htf_30m_ma', 'htf_30m_slope', 'htf_1h_ma', 'htf_1h_slope' 欄位之 DataFrame。
    """
    ts_col = "ts" if "ts" in df.columns else ("datetime" if "datetime" in df.columns else None)
    if ts_col is None or df.empty or ma_length <= 0:
        df["htf_30m_ma"] = np.nan
        df["htf_30m_slope"] = 0.0
        df["htf_1h_ma"] = np.nan
        df["htf_1h_slope"] = 0.0
        return df

    temp = pd.DataFrame({
        "_ts": pd.to_datetime(df[ts_col]),
        "_close": df["close"].astype(float).to_numpy(),
        "_orig_idx": np.arange(len(df))
    }).sort_values("_ts")

    indexed = temp.set_index("_ts")["_close"]

    # 1. 聚合 30 分 K 並計算 SMA 與斜率 (標籤 T 於 T + 25 分鐘收盤)
    c_30m = indexed.resample("30min", closed="left", label="left").last().dropna()
    df_30m = pd.DataFrame({"htf_30m_ma": c_30m.rolling(window=ma_length).mean()})
    df_30m["htf_30m_slope"] = df_30m["htf_30m_ma"] - df_30m["htf_30m_ma"].shift(1)
    df_30m["_close_ts_30m"] = df_30m.index + pd.Timedelta(minutes=25)
    df_30m = df_30m.reset_index(drop=True).sort_values("_close_ts_30m")

    # 2. 聚合 1 小時 K 並計算 SMA 與斜率 (標籤 T 於 T + 55 分鐘收盤)
    c_1h = indexed.resample("1h", closed="left", label="left").last().dropna()
    df_1h = pd.DataFrame({"htf_1h_ma": c_1h.rolling(window=ma_length).mean()})
    df_1h["htf_1h_slope"] = df_1h["htf_1h_ma"] - df_1h["htf_1h_ma"].shift(1)
    df_1h["_close_ts_1h"] = df_1h.index + pd.Timedelta(minutes=55)
    df_1h = df_1h.reset_index(drop=True).sort_values("_close_ts_1h")

    # 3. 以 backward merge_asof 嚴格對齊已收盤之大週期指標 (杜絕偷看未來)
    merged = pd.merge_asof(
        temp,
        df_30m[["_close_ts_30m", "htf_30m_ma", "htf_30m_slope"]],
        left_on="_ts",
        right_on="_close_ts_30m",
        direction="backward"
    )
    merged = pd.merge_asof(
        merged,
        df_1h[["_close_ts_1h", "htf_1h_ma", "htf_1h_slope"]],
        left_on="_ts",
        right_on="_close_ts_1h",
        direction="backward"
    ).sort_values("_orig_idx")

    df["htf_30m_ma"] = merged["htf_30m_ma"].to_numpy()
    df["htf_30m_slope"] = merged["htf_30m_slope"].fillna(0.0).to_numpy()
    df["htf_1h_ma"] = merged["htf_1h_ma"].to_numpy()
    df["htf_1h_slope"] = merged["htf_1h_slope"].fillna(0.0).to_numpy()
    return df


def populate_all_indicators(
    df: pd.DataFrame,
    bb_length: int = 20,
    bb_mult: float = 2.0,
    atr_length: int = 14,
    atr_sig_length: int = 9,
    donchian_length: int = 4,
    supertrend_length: int = 10,
    supertrend_multiplier: float = 3.0,
    htf_ma_length: int = 20,
) -> pd.DataFrame:
    """
    批次計算策略所需之所有技術指標並附加至 DataFrame。
    """
    df = calculate_bollinger_bands(df, length=bb_length, mult=bb_mult)
    df = calculate_atr(df, length=atr_length, sig_length=atr_sig_length)
    df = calculate_donchian(df, length=donchian_length)
    df = calculate_supertrend_series(df, length=supertrend_length, multiplier=supertrend_multiplier)
    df = calculate_htf_ma_slopes(df, ma_length=htf_ma_length)
    return df

