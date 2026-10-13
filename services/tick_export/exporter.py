"""
秒 K 線資料處理與 Excel 匯出核心模組 (services/tick_export/exporter.py)

負責透過永豐 Shioaji API 逐日擷取微台10逐筆成交 Tick 資料、建立 Parquet 本地快取、
並聚合為 1 秒 OHLCV（開盤價、最高價、最低價、收盤價、時間、成交量），
最後以 xlsxwriter 串流模式（constant_memory）安全高效地輸出為標準 Excel 檔案。

遵循 PEP 8、Python Type Hints 與專案工程規範。
"""

import os
import time
import logging
from typing import List, Dict, Any, Optional, Tuple
import pandas as pd
import xlsxwriter

from .models import ExportConfig, ExportSummary

logger = logging.getLogger(__name__)


class TickExporter:
    """
    高頻秒 K 線擷取、重採樣與 Excel 匯出引擎。
    """
    def __init__(self, config: Optional[ExportConfig] = None):
        """
        初始化 TickExporter。

        參數:
            config (Optional[ExportConfig]): 匯出任務組態設定模型。若無指定則採用預設微台10參數。
        """
        self.config = config or ExportConfig()
        os.makedirs(self.config.cache_dir, exist_ok=True)

    def fetch_and_cache_ticks(
        self,
        api,
        contract,
        date_str: str,
        timeout: int = 30000,
        max_retries: int = 3,
        force_refresh: bool = False
    ) -> pd.DataFrame:
        """
        向 Shioaji API 下載指定交易日之逐筆成交 Tick 資料，並自動進行本機 Parquet 快取。
        具備智慧快取失效機制：若快取僅包含前半段夜盤而當前已過日盤收盤（13:45），自動重新拉取全日數據。

        參數:
            api: 已連線之 shioaji.Shioaji 物件實例。
            contract: Shioaji 期貨合約物件實體。
            date_str (str): 交易日字串 (格式: YYYY-MM-DD)。
            timeout (int): API 請求超時毫秒數，預設 30,000 毫秒。
            max_retries (int): 網路異常重試次數，預設 3 次。
            force_refresh (bool): 是否強制忽略本機快取重新向 API 下載，預設 False。

        返回:
            pd.DataFrame: 當日逐筆成交 DataFrame（包含 ts, close, volume 欄位）。
        """
        cache_file = os.path.join(
            self.config.cache_dir,
            f"{contract.code}_{date_str}_ticks.parquet"
        )
        if os.path.exists(cache_file) and not force_refresh:
            cached_df = pd.read_parquet(cache_file)
            if not cached_df.empty and "ts" in cached_df.columns:
                max_dt = pd.to_datetime(cached_df["ts"].max())
                # 若快取僅至清晨 (<= 05:05)，且目前本機時間已超過下午 13:45，判定快取不完整需更新全日數據
                now = pd.Timestamp.now()
                is_today = (date_str == now.strftime("%Y-%m-%d"))
                if is_today and max_dt.hour < 8 and (now.hour > 13 or (now.hour == 13 and now.minute >= 45)):
                    logger.info(f"偵測到 {date_str} 快取僅包含夜盤數據 (最新時間: {max_dt})，已過日盤收盤，自動重新拉取全日數據...")
                else:
                    logger.info(f"載入本機快取 Tick 資料: {cache_file} (共 {len(cached_df):,} 筆)")
                    return cached_df
            else:
                return cached_df


        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"向 Shioaji 請求 {contract.code} 在 {date_str} 的 Tick 資料 (第 {attempt}/{max_retries} 次)...")
                ticks = api.ticks(contract, date=date_str, timeout=timeout)
                if hasattr(ticks, "close") and len(ticks.close) > 0:
                    df = pd.DataFrame({
                        "ts": ticks.ts,
                        "close": ticks.close,
                        "volume": ticks.volume
                    })
                    # 儲存至本機 Parquet 快取
                    df.to_parquet(cache_file, index=False)
                    logger.info(f"成功下載並快取 {date_str} 逐筆成交資料，共 {len(df):,} 筆。")
                    return df
                else:
                    logger.warning(f"Shioaji 回傳 {date_str} 無逐筆成交資料。")
                    return pd.DataFrame()
            except Exception as e:
                logger.warning(f"下載 {date_str} 失敗 (嘗試 {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    time.sleep(2.0 * attempt)
                else:
                    logger.error(f"Status: ERROR | Root Cause: 下載 {date_str} 超過最大重試次數 | Suggested Fix: 檢查網路連線或該日是否為休市日。")
                    return pd.DataFrame()
        return pd.DataFrame()

    def resample_to_second_bars(self, ticks_df: pd.DataFrame) -> pd.DataFrame:
        """
        將毫秒/微秒高頻逐筆成交資料下採樣聚合為 1 秒 OHLCV 秒 K 線。

        參數:
            ticks_df (pd.DataFrame): 包含 ts, close, volume 之原始 Tick 資料。

        返回:
            pd.DataFrame: 包含「時間、開盤價、最高價、最低價、收盤價、成交量」六大標準欄位之 DataFrame。
        """
        if ticks_df.empty:
            return pd.DataFrame(columns=["時間", "開盤價", "最高價", "最低價", "收盤價", "成交量"])

        # 轉換奈秒 Unix 時間戳為 pandas datetime
        ticks_df["datetime"] = pd.to_datetime(ticks_df["ts"])
        
        # 依 1 秒頻率進行重採樣聚合
        sec_df = ticks_df.set_index("datetime").resample("1s").agg({
            "close": ["first", "max", "min", "last"],
            "volume": "sum"
        })

        # 依使用者指定排除無成交之秒數 (避免產生大量無效空列塞爆 Excel)
        if self.config.only_active_seconds:
            sec_df = sec_df.dropna(subset=[("close", "first")])

        sec_df.columns = ["開盤價", "最高價", "最低價", "收盤價", "成交量"]
        sec_df = sec_df.reset_index()
        sec_df["時間"] = sec_df["datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")

        # 重新排序欄位以完全符合使用者需求：時間、開盤價、最高價、最低價、收盤價、成交量
        ordered_df = sec_df[["時間", "開盤價", "最高價", "最低價", "收盤價", "成交量"]].copy()
        ordered_df["成交量"] = ordered_df["成交量"].astype(int)
        return ordered_df

    def export_to_excel_streaming(
        self,
        daily_second_dfs: List[Tuple[str, pd.DataFrame]],
        contract_info: Dict[str, Any],
        output_file: str
    ) -> ExportSummary:
        """
        使用 xlsxwriter 以 constant_memory 常數記憶體流式寫入超大型 Excel 檔案，
        確保在數十萬筆秒 K 數據下絕不發生記憶體耗盡 (OOM)。

        參數:
            daily_second_dfs (List[Tuple[str, pd.DataFrame]]): 每日日期與其對應 1 秒 K 線之列表。
            contract_info (Dict[str, Any]): 期貨合約詳細屬性資訊。
            output_file (str): 目標 Excel 檔案輸出路徑。

        返回:
            ExportSummary: 匯出統計摘要模型。
        """
        logger.info(f"開始流式寫入 Excel 檔案: {output_file} ...")
        
        workbook = xlsxwriter.Workbook(
            output_file,
            {
                "constant_memory": True,  # 啟用串流低記憶體最佳化模式
                "default_date_format": "yyyy-mm-dd hh:mm:ss"
            }
        )

        # 定義格式樣式
        header_format = workbook.add_format({
            "bold": True,
            "align": "center",
            "valign": "vcenter",
            "fg_color": "#2c3e50",
            "font_color": "#ffffff",
            "border": 1
        })
        time_format = workbook.add_format({"align": "center"})
        price_format = workbook.add_format({"num_format": "#,##0.0", "align": "right"})
        volume_format = workbook.add_format({"num_format": "#,##0", "align": "right"})
        
        # 1. 建立「秒K歷史紀錄」主工作表
        ws_data = workbook.add_worksheet("秒K歷史紀錄")
        
        headers = ["時間", "開盤價", "最高價", "最低價", "收盤價", "成交量"]
        for col_idx, h in enumerate(headers):
            ws_data.write(0, col_idx, h, header_format)

        # 設定欄寬
        ws_data.set_column(0, 0, 22)  # 時間
        ws_data.set_column(1, 4, 12)  # 開高低收
        ws_data.set_column(5, 5, 12)  # 成交量

        current_row = 1
        total_seconds = 0
        total_vol = 0

        for date_str, df in daily_second_dfs:
            if df.empty:
                continue
            logger.info(f"正在寫入 {date_str} 之秒 K 線 ({len(df):,} 筆)...")
            
            times = df["時間"].tolist()
            opens = df["開盤價"].tolist()
            highs = df["最高價"].tolist()
            lows = df["最低價"].tolist()
            closes = df["收盤價"].tolist()
            volumes = df["成交量"].tolist()

            for i in range(len(df)):
                ws_data.write(current_row, 0, times[i], time_format)
                ws_data.write(current_row, 1, opens[i], price_format)
                ws_data.write(current_row, 2, highs[i], price_format)
                ws_data.write(current_row, 3, lows[i], price_format)
                ws_data.write(current_row, 4, closes[i], price_format)
                ws_data.write(current_row, 5, volumes[i], volume_format)
                current_row += 1

            total_seconds += len(df)
            total_vol += int(df["成交量"].sum())

        workbook.close()
        
        file_size_mb = os.path.getsize(output_file) / (1024.0 * 1024.0)
        logger.info(f"Excel 檔案寫入完成！總秒數: {total_seconds:,} 筆，檔案大小: {file_size_mb:.2f} MB。")

        return ExportSummary(
            symbol=contract_info.get("code", "TMFJ6"),
            contract_name=contract_info.get("name", "微台10"),
            start_date=daily_second_dfs[0][0] if daily_second_dfs else "",
            end_date=daily_second_dfs[-1][0] if daily_second_dfs else "",
            total_trading_days=len(daily_second_dfs),
            total_ticks_processed=0,  # 後續由調用層總結注入
            total_second_bars=total_seconds,
            total_volume=total_vol,
            output_file_path=os.path.abspath(output_file),
            file_size_mb=round(file_size_mb, 2)
        )
