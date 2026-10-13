"""
微台10期貨高頻逐秒歷史紀錄 Excel 匯出工具 (export_micro_taiwan_10.py)

執行流程：
1. 連線永豐金證券 Shioaji API 模擬環境。
2. 鎖定微台10合約 (TMFJ6 / TMF202610)。
3. 自動掃描自上架掛牌 (2026-09-16) 至目前最新交易日 (2026-10-12) 之所有交易日。
4. 逐日下載百萬筆高頻逐筆成交 Tick 資料並建立 Parquet 本機快取。
5. 將逐筆資料下採樣聚合為 1 秒 OHLCV（開盤價、最高價、最低價、收盤價、時間、成交量）。
6. 以 xlsxwriter 串流模式（constant_memory）安全高效匯出為標準 Excel 檔案。

遵循 PEP 8、Python Type Hints 與專案工程規範。
"""

import os
import sys
import logging
from typing import List, Dict, Any
from dotenv import load_dotenv
import pandas as pd

from services.tick_export import ExportConfig, TickExporter, ExportSummary

# 配置日誌
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


def main() -> None:
    """執行微台10高頻秒 K 線匯出任務主程式。"""
    load_dotenv()

    api_key = os.environ.get("SHIOAJI_API_KEY")
    secret_key = os.environ.get("SHIOAJI_SECRET_KEY")

    if not api_key or not secret_key:
        print("Status: ERROR | Root Cause: 缺少 SHIOAJI_API_KEY 或 SHIOAJI_SECRET_KEY | Suggested Fix: 請檢查 .env 檔案中的金鑰設定。")
        sys.exit(1)

    print("\n" + "=" * 65)
    print("      微台10 (TMF202610) 期貨合約 - 全歷史高頻每秒 K 線匯出系統")
    print("=" * 65)

    try:
        import shioaji as sj
        api = sj.Shioaji(simulation=True)
        logger.info("正在連線登入永豐金證券 Shioaji API (Simulation 模式)...")
        api.login(api_key, secret_key)
        logger.info("Shioaji 登入成功！正在檢索微台10合約資訊...")

        # 檢索微台10合約
        target_code = "TMFJ6"
        contract = api.Contracts.Futures.TMF.get(target_code)
        if not contract:
            # 搜尋代碼開頭符合者
            for c in api.Contracts.Futures.TMF:
                if c.symbol == "TMF202610" or c.name == "微台10":
                    contract = c
                    break

        if not contract:
            print(f"Status: ERROR | Root Cause: 未在 Shioaji 期貨清單中找到微台10 (代碼: {target_code}) | Suggested Fix: 檢查合約代碼。")
            api.logout()
            sys.exit(1)

        contract_info = {
            "code": contract.code,
            "symbol": contract.symbol,
            "name": "微台10",
            "delivery_month": contract.delivery_month,
            "delivery_date": contract.delivery_date
        }

        print(f"  目標商品 : {contract_info['name']} ({contract_info['symbol']})")
        print(f"  合約代碼 : {contract_info['code']}")
        print(f"  到期月份 : {contract_info['delivery_month']} (法定結算日: {contract_info['delivery_date']})")
        print("-----------------------------------------------------------------")

        # 檢索該合約自上架起的所有交易日
        logger.info("正在分析微台10掛牌交易時程表...")
        kbars = api.kbars(contract, start="2026-09-16", end="2026-10-12")
        df_k = pd.DataFrame({**kbars})
        df_k["ts"] = pd.to_datetime(df_k["ts"])
        trading_days = sorted(df_k["ts"].dt.strftime("%Y-%m-%d").unique())

        if not trading_days:
            logger.warning("未檢索到 K 線交易日，採用標準台曆推算交易日...")
            trading_days = [
                "2026-09-16", "2026-09-17", "2026-09-18", "2026-09-19", "2026-09-21",
                "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-29",
                "2026-09-30", "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-05",
                "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09", "2026-10-12"
            ]

        print(f"  掛牌起訖 : {trading_days[0]} 至 {trading_days[-1]} (共計 {len(trading_days)} 個有效交易日)")
        print("-----------------------------------------------------------------")

        config = ExportConfig(
            symbol=contract.code,
            start_date=trading_days[0],
            end_date=trading_days[-1],
            output_excel="微台10_每秒逐筆歷史紀錄.xlsx",
            cache_dir="data/ticks_cache",
            only_active_seconds=True
        )
        exporter = TickExporter(config=config)

        daily_second_dfs = []
        total_ticks_count = 0

        for idx, date_str in enumerate(trading_days, 1):
            print(f"  [{idx:02d}/{len(trading_days):02d}] 處理交易日: {date_str} ...", end=" ", flush=True)
            ticks_df = exporter.fetch_and_cache_ticks(api, contract, date_str)
            ticks_cnt = len(ticks_df)
            total_ticks_count += ticks_cnt

            if ticks_cnt > 0:
                sec_df = exporter.resample_to_second_bars(ticks_df)
                daily_second_dfs.append((date_str, sec_df))
                print(f"Tick數: {ticks_cnt:>7,} 筆 -> 秒K數: {len(sec_df):>6,} 筆")
            else:
                print("無成交資料 (休市或非交易時段)")

        api.logout()
        logger.info("已安全登出 Shioaji API。")

        print("-----------------------------------------------------------------")
        print("  正在匯總並流式寫入 Excel 檔案（採用 constant_memory 低記憶體架構）...")
        
        output_excel_path = "微台10_每秒逐筆歷史紀錄.xlsx"
        summary: ExportSummary = exporter.export_to_excel_streaming(
            daily_second_dfs=daily_second_dfs,
            contract_info=contract_info,
            output_file=output_excel_path
        )
        summary.total_ticks_processed = total_ticks_count

        # 同步儲存 Parquet 格式資產（供極速資料分析）
        all_sec_dfs = [df for _, df in daily_second_dfs if not df.empty]
        if all_sec_dfs:
            combined_sec_df = pd.concat(all_sec_dfs, ignore_index=True)
            parquet_path = "微台10_每秒逐筆歷史紀錄.parquet"
            combined_sec_df.to_parquet(parquet_path, index=False)
            logger.info(f"高效能 Parquet 檔案已同步存檔至: {parquet_path}")

        print("\n" + "=" * 65)
        print("                     匯出任務順利完成！")
        print("=" * 65)
        print(f"  商品名稱             : {summary.contract_name} ({summary.symbol})")
        print(f"  資料涵蓋區間         : {summary.start_date} 至 {summary.end_date}")
        print(f"  處理交易日數         : {summary.total_trading_days} 天")
        print(f"  處理逐筆成交 Tick 數 : {summary.total_ticks_processed:>12,} 筆")
        print(f"  產出每秒 K 線筆數    : {summary.total_second_bars:>12,} 筆")
        print(f"  總累積成交口數       : {summary.total_volume:>12,} 口")
        print(f"  Excel 檔案路徑       : {summary.output_file_path}")
        print(f"  Excel 檔案大小       : {summary.file_size_mb:.2f} MB")
        print("=" * 65 + "\n")

    except Exception as e:
        logger.error(f"Status: ERROR | Root Cause: 匯出過程發生非預期例外 ({e}) | Suggested Fix: 檢查詳細錯誤追蹤並重試。")
        raise


if __name__ == "__main__":
    main()
