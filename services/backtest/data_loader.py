"""
歷史行情資料載入與換月拼接模組 (services/backtest/data_loader.py)

負責透過永豐 Shioaji API 下載歷史 K 線、管理本機快取、
並精確依照台灣期交所規則（每月第三個星期三 13:00 結算）合成連續近月 5 分 K 線時序。
遵循 PEP 8 與 Python Type Hints 規範。
"""

import os
import calendar
import datetime
import logging
from typing import Optional, List, Dict, Tuple
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


def get_third_wednesday(year: int, month: int) -> datetime.date:
    """
    計算指定年月份之第三個星期三（台指期法定結算日）。

    參數:
        year (int): 西元年。
        month (int): 月份 (1-12)。

    返回:
        datetime.date: 第三個星期三之公曆日期。
    """
    c = calendar.monthcalendar(year, month)
    # 星期三在 calendar 模組中為索引 2 (0=Mon, 1=Tue, 2=Wed)
    first_wed = c[0][calendar.WEDNESDAY]
    if first_wed != 0:
        third_wed_day = c[2][calendar.WEDNESDAY]
    else:
        third_wed_day = c[3][calendar.WEDNESDAY]
    return datetime.date(year, month, third_wed_day)


class DataLoader:
    """
    資料載入器，管理 Shioaji API 歷史行情擷取、檔案快取與連續近月拼接。
    """
    def __init__(self, cache_dir: str = "data/cache"):
        """
        初始化 DataLoader。

        參數:
            cache_dir (str): K 線資料快取檔案目錄路徑。
        """
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)

    def resample_to_5min(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        將 1 分鐘 K 線時序重新採樣聚合為 5 分鐘 K 線。

        參數:
            df (pd.DataFrame): 包含 ts, open, high, low, close, volume 之 1 分 K DataFrame。

        返回:
            pd.DataFrame: 5 分 K DataFrame。
        """
        if df.empty:
            return df
        
        df_indexed = df.set_index("ts")
        resampled = df_indexed.resample("5min").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum"
        }).dropna().reset_index()
        return resampled

    def fetch_from_shioaji(
        self,
        api,
        contract,
        start_date: str,
        end_date: str,
        resample_5min: bool = True
    ) -> pd.DataFrame:
        """
        使用 Shioaji API 下載特定合約之歷史 K 棒資料，並於本機建立快取。
        支援大於 30 天之長週期自動分塊請求（每塊 25 天）以克服 API 限制。

        參數:
            api: 已登入之 shioaji.Shioaji 物件實例。
            contract: Shioaji 期貨合約物件 (如 api.Contracts.Futures.MXF.MXFR1)。
            start_date (str): 起始日期字串 (YYYY-MM-DD)。
            end_date (str): 結束日期字串 (YYYY-MM-DD)。
            resample_5min (bool): 是否自動將 1 分 K 重新採樣為 5 分 K。

        返回:
            pd.DataFrame: 整理標準化後之 K 線 DataFrame。
        """
        cache_file = os.path.join(
            self.cache_dir,
            f"{contract.code}_{start_date}_{end_date}_{'5min' if resample_5min else '1min'}.parquet"
        )
        if os.path.exists(cache_file):
            logger.info(f"載入本機快取資料: {cache_file}")
            return pd.read_parquet(cache_file)

        # 將日期區間切分為小於等於 25 天之分塊以符合 API 限制
        start_dt = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
        end_dt = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()
        
        chunks = []
        cur_start = start_dt
        while cur_start <= end_dt:
            cur_end = min(cur_start + datetime.timedelta(days=25), end_dt)
            chunks.append((cur_start.strftime("%Y-%m-%d"), cur_end.strftime("%Y-%m-%d")))
            cur_start = cur_end + datetime.timedelta(days=1)

        all_dfs = []
        for c_start, c_end in chunks:
            try:
                logger.info(f"向 Shioaji 請求合約 {contract.code} 區間: {c_start} ~ {c_end}")
                kbars = api.kbars(contract, start=c_start, end=c_end)
                df_chunk = pd.DataFrame({**kbars})
                if not df_chunk.empty:
                    df_chunk["ts"] = pd.to_datetime(df_chunk["ts"])
                    df_chunk = df_chunk.rename(columns={
                        "Open": "open",
                        "High": "high",
                        "Low": "low",
                        "Close": "close",
                        "Volume": "volume"
                    })
                    all_dfs.append(df_chunk)
            except Exception as e:
                logger.warning(f"擷取分塊 {c_start} ~ {c_end} 失敗: {e}")

        if not all_dfs:
            logger.warning(f"合約 {contract.code} 於指定區間查無 K 線資料。")
            return pd.DataFrame()

        df = pd.concat(all_dfs, ignore_index=True)
        df = df.drop_duplicates(subset=["ts"]).sort_values(by="ts").reset_index(drop=True)

        if resample_5min:
            df = self.resample_to_5min(df)

        # 寫入本機 Parquet 快取
        try:
            df.to_parquet(cache_file, index=False)
            logger.info(f"K 線資料已快取至: {cache_file}")
        except Exception as e:
            logger.warning(f"寫入快取檔案失敗: {e}")

        return df


    def splice_continuous_front_month(
        self,
        monthly_dfs: Dict[str, pd.DataFrame]
    ) -> pd.DataFrame:
        """
        依照台指結算日規則（每月第三個星期三 13:00），精準拼接各合約之近月 K 線。

        參數:
            monthly_dfs (Dict[str, pd.DataFrame]): 鍵為合約年月代碼 (如 '202409')，值為該合約 K 線 DataFrame。

        返回:
            pd.DataFrame: 拼接完成且無跨月重疊之連續近月 5 分 K 線時序。
        """
        spliced_segments: List[pd.DataFrame] = []
        sorted_codes = sorted(monthly_dfs.keys())

        for idx, code in enumerate(sorted_codes):
            df = monthly_dfs[code].copy()
            if df.empty:
                continue

            year = int(code[:4])
            month = int(code[4:6])
            expiry_date = get_third_wednesday(year, month)
            expiry_cutoff = pd.Timestamp(
                year=expiry_date.year,
                month=expiry_date.month,
                day=expiry_date.day,
                hour=13,
                minute=0,
                second=0
            )

            # 起始邊界：上一合約之結算日 15:00
            if idx > 0:
                prev_code = sorted_codes[idx - 1]
                prev_y = int(prev_code[:4])
                prev_m = int(prev_code[4:6])
                prev_expiry_date = get_third_wednesday(prev_y, prev_m)
                start_cutoff = pd.Timestamp(
                    year=prev_expiry_date.year,
                    month=prev_expiry_date.month,
                    day=prev_expiry_date.day,
                    hour=15,
                    minute=0,
                    second=0
                )
                df = df[df["ts"] >= start_cutoff]

            # 結束邊界：當前合約結算日 13:00
            df = df[df["ts"] <= expiry_cutoff]
            df["contract"] = code
            spliced_segments.append(df)

        if not spliced_segments:
            return pd.DataFrame()

        continuous_df = pd.concat(spliced_segments, ignore_index=True)
        continuous_df = continuous_df.drop_duplicates(subset=["ts"]).sort_values(by="ts").reset_index(drop=True)
        return continuous_df

    def generate_simulated_kbars(
        self,
        start_date: str = "2024-01-01",
        end_date: str = "2024-03-31",
        initial_price: float = 20000.0,
        volatility: float = 0.0015
    ) -> pd.DataFrame:
        """
        生成符合台灣期交所全日盤交易時段（日盤 08:45-13:45、夜盤 15:00-05:00）的擬真 5 分 K 資料。
        用於線下測試、單元驗證以及無網路或 API 離線環境。

        參數:
            start_date (str): 起始日期 (YYYY-MM-DD)。
            end_date (str): 結束日期 (YYYY-MM-DD)。
            initial_price (float): 基準起始價格。
            volatility (float): 5 分鐘波動率係數。

        返回:
            pd.DataFrame: 完整擬真 5 分 K 線時序 DataFrame。
        """
        date_range = pd.date_range(start=start_date, end=end_date, freq="D")
        records = []
        current_price = initial_price
        np.random.seed(42)

        for d in date_range:
            # 排除週末 (週六、週日)
            if d.weekday() >= 5:
                continue

            # 日盤: 08:45 ~ 13:45 (每 5 分鐘一根，共 60 根)
            rth_times = pd.date_range(
                start=d.replace(hour=8, minute=45),
                end=d.replace(hour=13, minute=45),
                freq="5min"
            )
            # 夜盤: 15:00 ~ 隔日 05:00 (每 5 分鐘一根，共 168 根)
            eth_times = pd.date_range(
                start=d.replace(hour=15, minute=0),
                end=(d + datetime.timedelta(days=1)).replace(hour=5, minute=0),
                freq="5min"
            )

            all_bars = list(rth_times) + list(eth_times)
            for ts in all_bars:
                shock = np.random.normal(loc=0.00005, scale=volatility)
                open_p = current_price
                close_p = open_p * (1.0 + shock)
                high_p = max(open_p, close_p) * (1.0 + abs(np.random.normal(0, volatility * 0.5)))
                low_p = min(open_p, close_p) * (1.0 - abs(np.random.normal(0, volatility * 0.5)))
                volume = int(np.random.uniform(50, 500))

                records.append({
                    "ts": ts,
                    "open": round(open_p, 1),
                    "high": round(high_p, 1),
                    "low": round(low_p, 1),
                    "close": round(close_p, 1),
                    "volume": volume
                })
                current_price = close_p

        df = pd.DataFrame(records)
        df = df.drop_duplicates(subset=["ts"]).sort_values(by="ts").reset_index(drop=True)
        return df
