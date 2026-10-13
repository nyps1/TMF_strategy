"""
微台10秒 K 線資料寫入 SQLite 資料庫主程式 (import_to_sqlite.py)

執行流程：
1. 載入微台10全時序秒 K 線資料（優先讀取高效能 Parquet 檔案，或自 Excel 解析）。
2. 初始化 SQLite 資料庫並啟用 PRAGMA 效能調校（WAL 模式、64MB 分頁快取、NORMAL 同步）。
3. 批次交易串流寫入 746,744 筆秒 K 線記錄至 tmf10_second_bars 資料表。
4. 建立時間戳與商品代碼之 B-Tree 索引。
5. 執行效能驗證查詢並產出資料庫統計報表。

遵循 PEP 8、Python Type Hints 與專案工程規範。
"""

import os
import sys
import time
import logging
from typing import Optional
import pandas as pd

from services.storage import SQLiteManager

# 配置日誌
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


def main() -> None:
    """執行將微台10秒 K 線寫入 SQLite 資料庫的主程式。"""
    parquet_path = "微台10_每秒逐筆歷史紀錄.parquet"
    excel_path = "微台10_每秒逐筆歷史紀錄.xlsx"
    db_path = "data/market_data.db"

    print("\n" + "=" * 65)
    print("      微台10 (TMF202610) 高頻秒 K 線 - SQLite 資料庫寫入系統")
    print("=" * 65)

    # 1. 載入來源資料
    df: Optional[pd.DataFrame] = None
    if os.path.exists(parquet_path):
        logger.info(f"偵測到高效能 Parquet 檔案: {parquet_path}，正在快速載入...")
        t0 = time.time()
        df = pd.read_parquet(parquet_path)
        logger.info(f"Parquet 載入完成！耗時: {time.time() - t0:.2f} 秒，共 {len(df):,} 筆。")
    elif os.path.exists(excel_path):
        logger.info(f"未找到 Parquet 檔，改由 Excel 檔案載入: {excel_path} (資料量較大請稍候)...")
        t0 = time.time()
        df = pd.read_excel(excel_path, sheet_name="秒K歷史紀錄")
        logger.info(f"Excel 載入完成！耗時: {time.time() - t0:.2f} 秒，共 {len(df):,} 筆。")
    else:
        print(f"Status: ERROR | Root Cause: 找不到來源資料檔 ({parquet_path} 或 {excel_path}) | Suggested Fix: 請先執行 export_micro_taiwan_10.py 產出資料。")
        sys.exit(1)

    print(f"  來源資料筆數 : {len(df):,} 筆")
    print(f"  目標資料庫   : {os.path.abspath(db_path)}")
    print("-----------------------------------------------------------------")

    # 2. 初始化 SQLite 管理器並執行批次寫入
    manager = SQLiteManager(db_path=db_path)
    
    print("  正在執行 SQLite 批次高速寫入（每批次 50,000 筆，開啟 WAL 模式）...")
    t_start = time.time()
    inserted_rows = manager.insert_second_bars(df=df, symbol="TMFJ6", batch_size=50000)
    write_duration = time.time() - t_start

    print(f"  寫入作業耗時 : {write_duration:.2f} 秒 (每秒吞吐量: {inserted_rows / max(write_duration, 0.001):,.0f} 列/秒)")
    print("-----------------------------------------------------------------")

    # 3. 執行檢索驗證與基準測試
    print("  正在執行 B-Tree 索引查詢驗證測試...")
    t_q = time.time()
    # 測試查詢 2026-10-08 當日夜盤開盤前 10 分鐘 (600 秒)
    test_query_df = manager.query_bars_by_time_range(
        start_time="2026-10-08 15:00:00",
        end_time="2026-10-08 15:10:00",
        symbol="TMFJ6"
    )
    query_duration = (time.time() - t_q) * 1000.0  # 毫秒

    print(f"  索引檢索耗時 : {query_duration:.2f} 毫秒 (檢索出 {len(test_query_df)} 筆秒 K 線)")
    print("-----------------------------------------------------------------")

    # 4. 產出資料庫統計報表
    summary = manager.get_summary()

    print("\n" + "=" * 65)
    print("                     SQLite 寫入與建庫成功！")
    print("=" * 65)
    print(f"  資料庫檔案路徑       : {summary['db_path']}")
    print(f"  目標資料表名稱       : {summary['table_name']}")
    print(f"  成功寫入總筆數       : {summary['total_rows']:>12,} 筆")
    print(f"  最早秒 K 時間戳      : {summary['earliest_time']}")
    print(f"  最新秒 K 時間戳      : {summary['latest_time']}")
    print(f"  總累積成交口數       : {summary['total_volume']:>12,} 口")
    print(f"  資料庫檔案大小       : {summary['file_size_mb']:.2f} MB")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
