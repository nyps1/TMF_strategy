"""
微台10期貨每日高頻秒 K 線自動增量同步主程式 (daily_sync_tmf10.py)

執行流程：
1. 解析命令列參數（支援 --date 指定交易日、--dry-run 預演模式）。
2. 載入本機環境變數並驗證 Shioaji API 憑證。
3. 初始化 Shioaji 模擬環境連線並登入。
4. 呼叫服務層 DailyDataSyncService 執行數據增量管線：
   - 比對 SQLite 最新時間戳
   - 下載當日夜盤與全日 Tick 資料
   - 聚合為 1 秒 OHLCV
   - 追加寫入 Excel 檔案（「微台10_每秒逐筆歷史紀錄.xlsx」）
   - 原子化更新 Parquet 本機快取（「微台10_每秒逐筆歷史紀錄.parquet」）
   - 批次寫入 SQLite 資料庫（「data/market_data.db」之 tmf10_second_bars）
5. 登出 Shioaji API 並輸出結構化同步執行報表。

遵循 PEP 8、Python Type Hints 與專案工程規範。
"""

import os
import sys
import argparse
import logging
from typing import Dict, Any
from dotenv import load_dotenv

from services.tick_export import DailyDataSyncService

# 配置全域日誌格式
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


def parse_arguments() -> argparse.Namespace:
    """
    解析 CLI 命令列引數。

    返回:
        argparse.Namespace: 解析完成之參數物件。
    """
    parser = argparse.ArgumentParser(
        description="微台10期貨每日高頻秒 K 線自動增量同步工具"
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="指定欲同步之交易日 (格式: YYYY-MM-DD)，預設為當日"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="預演模式，僅檢索並分析增量筆數，不實際寫入 Excel 與資料庫"
    )
    return parser.parse_args()


def run_daily_sync(target_date: str = None, dry_run: bool = False) -> Dict[str, Any]:
    """
    執行微台10每日增量同步主邏輯。

    參數:
        target_date (str): 指定同步之交易日 (格式: YYYY-MM-DD)。
        dry_run (bool): 是否為預演模式。

    返回:
        Dict[str, Any]: 執行成果資訊字典。
    """
    load_dotenv()

    api_key = os.environ.get("SHIOAJI_API_KEY")
    secret_key = os.environ.get("SHIOAJI_SECRET_KEY")

    if not api_key or not secret_key:
        error_msg = (
            "Status: ERROR | Root Cause: 缺少 SHIOAJI_API_KEY 或 SHIOAJI_SECRET_KEY | "
            "Suggested Fix: 請檢查 .env 檔案中是否已配置有效的永豐證券 API 金鑰。"
        )
        print(error_msg)
        return {"status": "FAILED", "error": error_msg}

    print("\n" + "=" * 65)
    print("      微台10 (TMF202610) 每日高頻秒 K 線自動增量同步系統")
    print("=" * 65)

    import shioaji as sj
    api = sj.Shioaji(simulation=True)

    try:
        logger.info("正在連線登入永豐金證券 Shioaji API (Simulation 模式)...")
        api.login(api_key, secret_key)
        logger.info("Shioaji 認證成功！")

        service = DailyDataSyncService(
            db_path="data/market_data.db",
            excel_path="微台10_每秒逐筆歷史紀錄.xlsx",
            parquet_path="微台10_每秒逐筆歷史紀錄.parquet",
            cache_dir="data/ticks_cache"
        )

        if dry_run:
            logger.info("【預演模式已啟動】將不異動任何既有檔案與資料庫記錄。")
            latest_time = service.get_latest_recorded_time()
            contract = api.Contracts.Futures.TMF.get("TMFJ6")
            from datetime import date
            sync_date = target_date or date.today().strftime("%Y-%m-%d")
            df_new = service.fetch_and_resample(
                api=api,
                contract=contract,
                target_date=sync_date,
                latest_time=latest_time
            )
            print(f"  目標交易日     : {sync_date}")
            print(f"  最新已收錄時間 : {latest_time}")
            print(f"  預估增量秒 K 線 : {len(df_new):,} 筆")
            if not df_new.empty:
                print(f"  增量時間範圍   : {df_new['時間'].min()} 至 {df_new['時間'].max()}")
            return {
                "status": "DRY_RUN_COMPLETED",
                "target_date": sync_date,
                "estimated_rows": len(df_new),
                "latest_time": latest_time
            }

        result = service.sync(api=api, target_date=target_date)

        print("\n" + "=" * 65)
        print("                     同步作業執行成果報表")
        print("=" * 65)
        print(f"  狀態               : {result.get('status')}")
        print(f"  目標交易日         : {result.get('date')}")
        if result.get("status") == "SUCCESS":
            print(f"  新增秒 K 筆數      : {result.get('new_bars_count', 0):>10,} 筆")
            print(f"  資料時序起點       : {result.get('new_range_start')}")
            print(f"  資料時序終點       : {result.get('new_range_end')}")
            print(f"  SQLite 寫入筆數    : {result.get('sqlite_inserted', 0):>10,} 筆")
            print(f"  Excel 追加列數     : {result.get('excel_appended', 0):>10,} 筆")
            print(f"  Parquet 總累積筆數 : {result.get('parquet_total_bars', 0):>10,} 筆")
            print(f"  執行耗時           : {result.get('duration_seconds', 0.0):.2f} 秒")
        elif result.get("status") == "UP_TO_DATE":
            print(f"  最新收錄時間       : {result.get('latest_time')}")
            print(f"  說明               : 資料庫已為最新狀態，無需重複同步。")
        print("=" * 65 + "\n")

        return result

    except Exception as e:
        error_msg = f"Status: ERROR | Root Cause: 執行每日同步程序時遭遇非預期例外 ({e}) | Suggested Fix: 檢查詳細日誌與網路連線。"
        logger.error(error_msg, exc_info=True)
        print(error_msg)
        return {"status": "FAILED", "error": str(e)}

    finally:
        try:
            api.logout()
            logger.info("已安全中斷並登出 Shioaji API。")
        except Exception:
            pass


def main() -> None:
    """程式進入點。"""
    args = parse_arguments()
    result = run_daily_sync(target_date=args.date, dry_run=args.dry_run)
    if result.get("status") == "FAILED":
        sys.exit(1)


if __name__ == "__main__":
    main()
