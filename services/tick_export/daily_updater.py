"""
每日微台10秒 K 線資料增量同步服務 (services/tick_export/daily_updater.py)

負責執行每日自動化增量同步任務：
1. 查詢 SQLite 資料庫與 Parquet 既有資料之最新收錄時間戳。
2. 連線永豐金證券 Shioaji API，擷取指定交易日（或當日夜盤結算）之高頻逐筆 Tick 資料。
3. 下採樣聚合為 1 秒 OHLCV 秒 K 線，並過濾出尚未收錄之全新增量數據。
4. 以增量（Append）模式寫入既有 Excel 檔案（「微台10_每秒逐筆歷史紀錄.xlsx」），具備表列容量防溢機制。
5. 同步原子化更新本機 Parquet 快取資產（「微台10_每秒逐筆歷史紀錄.parquet」）。
6. 以批次交易串流寫入 SQLite 資料庫（「data/market_data.db」之 tmf10_second_bars 表），確保冪等無重複。

遵循 PEP 8、Python Type Hints 與專案工程規範。
"""

import os
import time
import logging
from typing import Optional, Dict, Any, Tuple, List
from datetime import datetime, date
import pandas as pd

from services.storage import SQLiteManager
from services.tick_export.exporter import TickExporter
from services.tick_export.models import ExportConfig
from services.tick_export.monthly_excel import MonthlyExcelExporter

logger = logging.getLogger(__name__)


class DailyDataSyncService:
    """
    微台10期貨每日高頻時序數據增量同步服務引擎。
    結合 Facade Pattern 與 Pipeline Pattern，確保跨儲存體的一致性與冪等性。
    """

    def __init__(
        self,
        db_path: str = "data/market_data.db",
        excel_path: str = "data/excel/微台10_每秒逐筆歷史紀錄.xlsx",
        parquet_path: str = "data/parquet/微台10_每秒逐筆歷史紀錄.parquet",
        cache_dir: str = "data/ticks_cache"
    ) -> None:
        """
        初始化每日數據同步服務實體。

        參數:
            db_path (str): SQLite 資料庫目標路徑。
            excel_path (str): 歷史秒 K 線 Excel 檔案路徑。
            parquet_path (str): 高效能 Parquet 快取檔案路徑。
            cache_dir (str): 原始 Tick 快取資料夾目錄。
        """
        self.db_path = db_path
        self.excel_path = excel_path
        self.parquet_path = parquet_path
        self.cache_dir = cache_dir

        self.sqlite_manager = SQLiteManager(db_path=self.db_path)
        self.tick_exporter = TickExporter(
            config=ExportConfig(
                symbol="TMFJ6",
                cache_dir=self.cache_dir,
                only_active_seconds=True
            )
        )

    def get_latest_recorded_time(self) -> Optional[str]:
        """
        查詢系統儲存庫中最新已收錄的秒 K 線時間戳。
        優先自 SQLite 資料庫檢索，若 SQLite 為空則自 Parquet 資產讀取。

        返回:
            Optional[str]: 最新秒 K 線時間字串 (格式: YYYY-MM-DD HH:MM:SS)；若皆無數據則回傳 None。
        """
        try:
            summary = self.sqlite_manager.get_summary()
            if summary.get("latest_time"):
                return str(summary["latest_time"])
        except Exception as e:
            logger.warning(f"自 SQLite 查詢最新時間失敗: {e}，嘗試由 Parquet 檢索...")

        if os.path.exists(self.parquet_path):
            try:
                df = pd.read_parquet(self.parquet_path, columns=["時間"])
                if not df.empty:
                    return str(df["時間"].max())
            except Exception as e:
                logger.warning(f"自 Parquet 讀取最新時間戳失敗: {e}")

        return None

    def fetch_and_resample(
        self,
        api,
        contract,
        target_date: str,
        latest_time: Optional[str] = None,
        force_refresh: bool = False
    ) -> pd.DataFrame:
        """
        向 Shioaji 擷取指定交易日之逐筆成交 Tick，聚合為秒 K 線並執行增量過濾。

        參數:
            api: 已連線認證之 Shioaji API 實例。
            contract: Shioaji 微台指期貨合約物件。
            target_date (str): 目標交易日 (格式: YYYY-MM-DD)。
            latest_time (Optional[str]): 系統目前收錄之最新時間戳，用於增量過濾。
            force_refresh (bool): 是否強制刷新本機快取。

        返回:
            pd.DataFrame: 尚未收錄之全新增量秒 K 線 DataFrame。
        """
        logger.info(f"正在擷取交易日 {target_date} 之逐筆 Tick 成交數據...")
        ticks_df = self.tick_exporter.fetch_and_cache_ticks(
            api=api,
            contract=contract,
            date_str=target_date,
            force_refresh=force_refresh
        )

        if ticks_df.empty:
            logger.info(f"交易日 {target_date} 無任何成交數據（可能為休市日或無流動性）。")
            return pd.DataFrame(columns=["時間", "開盤價", "最高價", "最低價", "收盤價", "成交量"])

        # 聚合為 1 秒 OHLCV
        sec_df = self.tick_exporter.resample_to_second_bars(ticks_df)
        if sec_df.empty:
            return sec_df

        # 增量過濾：僅保留嚴格大於最新已收錄時間之秒 K
        if latest_time:
            sec_df = sec_df[sec_df["時間"] > latest_time].copy()
            logger.info(f"時間過濾基準 ({latest_time}) 篩選後，增量秒 K 筆數: {len(sec_df):,} 筆。")
        else:
            logger.info(f"無歷史比對基準，全量秒 K 筆數: {len(sec_df):,} 筆。")

        return sec_df

    def append_to_excel(self, df_new: pd.DataFrame) -> int:
        """
        將新增秒 K 反映至 Excel 活頁簿（一月一工作表版型）。

        設計說明：xlsx 為 zip 封裝之 XML，無法就地追加列；歷史回補後活頁簿達數百 MB，
        以 openpyxl 載入既有檔案追加會耗盡記憶體。因此改以 SQLite（資料已於前一步寫入）為唯一資料來源，
        委由 MonthlyExcelExporter 以 constant_memory 串流全量重建並原子替換，結果冪等且記憶體用量恆定。

        參數:
            df_new (pd.DataFrame): 本次新增之秒 K 線 DataFrame（僅用於判斷是否有新增資料與回報筆數）。

        返回:
            int: 本次新增並已反映於 Excel 之秒 K 列數。

        例外:
            PermissionError: Excel 檔案被其他程式開啟鎖定時拋出（含 Status + Root Cause + Suggested Fix 說明）。
        """
        if df_new.empty:
            logger.info("無新增數據，略過 Excel 更新作業。")
            return 0

        logger.info(f"正在由 SQLite 重建月份分頁 Excel: {self.excel_path} ...")
        exporter = MonthlyExcelExporter(db_path=self.db_path, excel_path=self.excel_path)
        try:
            summary = exporter.export()
        except PermissionError as e:
            logger.error(str(e))
            raise
        logger.info(
            f"Excel 更新完成：{len(summary['sheets'])} 個月份工作表、累計 {summary['total_rows']:,} 列、"
            f"{summary['file_size_mb']:.1f} MB (耗時 {summary['duration_seconds']} 秒)。"
        )
        return len(df_new)

    def append_to_parquet(self, df_new: pd.DataFrame) -> int:
        """
        將增量秒 K 線原子化合併寫入本機高效能 Parquet 檔案。

        參數:
            df_new (pd.DataFrame): 欲追加之秒 K 線 DataFrame。

        返回:
            int: 合併後 Parquet 儲存庫之總筆數。
        """
        if df_new.empty:
            logger.info("無新增數據，略過 Parquet 更新作業。")
            if os.path.exists(self.parquet_path):
                return len(pd.read_parquet(self.parquet_path, columns=["時間"]))
            return 0

        if os.path.exists(self.parquet_path):
            existing_df = pd.read_parquet(self.parquet_path)
            combined_df = pd.concat([existing_df, df_new], ignore_index=True)
            # 以「時間」為唯一鍵去重並按時序升冪排序
            combined_df = combined_df.drop_duplicates(subset=["時間"]).sort_values(by="時間").reset_index(drop=True)
        else:
            combined_df = df_new.copy()

        # 寫入臨時檔案以達原子化替換 (Atomic Write)
        temp_parquet = f"{self.parquet_path}.tmp"
        combined_df.to_parquet(temp_parquet, index=False)
        if os.path.exists(self.parquet_path):
            os.remove(self.parquet_path)
        os.rename(temp_parquet, self.parquet_path)

        logger.info(f"Parquet 快取資產原子化更新完畢，目前總筆數: {len(combined_df):,} 筆。")
        return len(combined_df)

    def append_to_sqlite(self, df_new: pd.DataFrame, symbol: str = "TMFJ6") -> int:
        """
        呼叫 SQLiteManager 將增量秒 K 線批次寫入 SQLite 資料庫。

        參數:
            df_new (pd.DataFrame): 欲寫入之秒 K 線 DataFrame。
            symbol (str): 商品合約代碼。

        返回:
            int: 成功寫入之資料筆數。
        """
        if df_new.empty:
            logger.info("無新增數據，略過 SQLite 寫入作業。")
            return 0

        inserted_count = self.sqlite_manager.insert_second_bars(
            df=df_new,
            symbol=symbol,
            batch_size=50000
        )
        logger.info(f"SQLite 增量寫入完成，共計寫入 {inserted_count:,} 筆秒 K 線記錄。")
        return inserted_count

    @staticmethod
    def get_taifex_trading_date(dt: Optional[datetime] = None) -> str:
        """
        計算給定時間點所屬之台灣期交所 (TAIFEX) 期貨交易日 (格式: YYYY-MM-DD)。

        期交所盤別對應規則：
        1. 15:00 ~ 23:59:59：屬於次一交易日 (T+1) 之夜盤。
           - 若為週五 15:00 以後，次一交易日順延至下週一 (+3 天)。
           - 若為平日，次一交易日為隔日 (+1 天)。
        2. 00:00 ~ 06:00:00：週六清晨為週五夜盤延伸，交易日歸下週一。
        3. 00:00 ~ 14:59:59：平日正常交易日 (T 日盤與清晨夜盤)。

        參數:
            dt (Optional[datetime]): 指定時間物件，若為 None 則取系統當前時間。

        返回:
            str: 格式為 YYYY-MM-DD 之期貨交易日字串。
        """
        if dt is None:
            dt = datetime.now()

        from datetime import timedelta
        if dt.hour >= 15:
            next_date = dt.date() + timedelta(days=1)
            if next_date.weekday() == 5:  # 週六
                next_date += timedelta(days=2)
            elif next_date.weekday() == 6:  # 週日
                next_date += timedelta(days=1)
            return next_date.strftime("%Y-%m-%d")
        elif dt.weekday() == 5 and dt.hour < 6:
            # 週六清晨屬於週五夜盤，所屬交易日為下週一
            next_date = dt.date() + timedelta(days=2)
            return next_date.strftime("%Y-%m-%d")
        else:
            return dt.strftime("%Y-%m-%d")

    def sync(
        self,
        api,
        target_date: Optional[str] = None,
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """
        執行每日微台10數據增量同步完整流程（Pipeline 執行入口）。

        參數:
            api: 已登入驗證之 Shioaji API 實例。
            target_date (Optional[str]): 指定同步之交易日 (格式: YYYY-MM-DD)。若為 None 則自動採用期交所當前交易日。
            force_refresh (bool): 是否強制刷新本機快取重新自 API 下載。

        返回:
            Dict[str, Any]: 同步執行成果結構化報表資訊。
        """
        t0 = time.time()
        sync_date = target_date or self.get_taifex_trading_date()

        logger.info("=" * 60)
        logger.info(f"開始執行微台10每日數據增量同步任務 (目標交易日: {sync_date})")
        logger.info("=" * 60)

        # 1. 取得合約資訊
        target_code = "TMFJ6"
        contract = api.Contracts.Futures.TMF.get(target_code)
        if not contract:
            # 搜尋當月合約
            for c in api.Contracts.Futures.TMF:
                if c.symbol.startswith("TMF202610") or c.name == "微台10":
                    contract = c
                    break

        if not contract:
            err_msg = (
                f"Status: ERROR | Root Cause: 未在 Shioaji 期貨合約清單中找到微台10 ({target_code}) | "
                f"Suggested Fix: 確認商品代碼或檢查合約是否已到期換月。"
            )
            logger.error(err_msg)
            raise ValueError(err_msg)

        contract_code = contract.code

        # 2. 檢索當前已收錄最新時間戳
        latest_time = self.get_latest_recorded_time()
        logger.info(f"系統目前最新已收錄秒 K 時間戳: {latest_time}")

        # 3. 決定欲同步之交易日清單（若未指定單一 target_date 且存在跨日缺口，自動補齊區間內所有營業日）
        dates_to_sync: List[str] = [sync_date]
        if target_date is None and latest_time:
            try:
                latest_dt = pd.to_datetime(latest_time).to_pydatetime()
                start_trade_date = self.get_taifex_trading_date(latest_dt)
                if start_trade_date < sync_date:
                    b_dates = pd.bdate_range(start=start_trade_date, end=sync_date)
                    dates_to_sync = [d.strftime("%Y-%m-%d") for d in b_dates]
                    if sync_date not in dates_to_sync:
                        dates_to_sync.append(sync_date)
                    logger.info(f"偵測到跨交易日增量區間，將依序同步交易日: {dates_to_sync}")
            except Exception as e:
                logger.warning(f"計算跨日補齊區間時發生警告 ({e})，維持單日同步: {sync_date}")

        # 4. 下載、重採樣與增量篩選
        dfs_collected: List[pd.DataFrame] = []
        for d_str in dates_to_sync:
            df_part = self.fetch_and_resample(
                api=api,
                contract=contract,
                target_date=d_str,
                latest_time=latest_time,
                force_refresh=force_refresh if d_str == sync_date else False
            )
            if not df_part.empty:
                dfs_collected.append(df_part)

        if dfs_collected:
            df_new = pd.concat(dfs_collected, ignore_index=True)
            df_new = df_new.drop_duplicates(subset=["時間"]).sort_values(by="時間").reset_index(drop=True)
        else:
            df_new = pd.DataFrame(columns=["時間", "開盤價", "最高價", "最低價", "收盤價", "成交量"])

        delta_count = len(df_new)
        if delta_count == 0:
            logger.info(f"交易日 {sync_date} 無全新增量數據需同步（已為最新狀態或休市）。")
            return {
                "status": "UP_TO_DATE",
                "date": sync_date,
                "new_bars_count": 0,
                "latest_time": latest_time,
                "duration_seconds": round(time.time() - t0, 2)
            }

        new_min_time = df_new["時間"].min()
        new_max_time = df_new["時間"].max()
        logger.info(f"即將同步新增數據: 區間 [{new_min_time} 至 {new_max_time}]，共 {delta_count:,} 筆。")

        # 4. 追加寫入 SQLite 資料庫
        sqlite_inserted = self.append_to_sqlite(df_new=df_new, symbol=contract_code)

        # 5. 原子化更新本機 Parquet 快取
        parquet_total = self.append_to_parquet(df_new=df_new)

        # 6. 追加寫入 Excel 檔案
        excel_appended = self.append_to_excel(df_new=df_new)

        total_duration = time.time() - t0
        logger.info("=" * 60)
        logger.info(f"每日增量同步圓滿完成！耗時: {total_duration:.2f} 秒。")
        logger.info("=" * 60)

        return {
            "status": "SUCCESS",
            "date": sync_date,
            "contract": contract_code,
            "new_bars_count": delta_count,
            "new_range_start": new_min_time,
            "new_range_end": new_max_time,
            "sqlite_inserted": sqlite_inserted,
            "parquet_total_bars": parquet_total,
            "excel_appended": excel_appended,
            "duration_seconds": round(total_duration, 2)
        }
