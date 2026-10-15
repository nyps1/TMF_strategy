"""
微台10 歷史 Tick 回補 CLI (backfill_history_tmf.py)

僅負責：解析參數 → 登入 Shioaji → 呼叫服務層 → 輸出報表。業務邏輯皆位於 services/tick_export/。

流程：
1. 以 login(fetch_contract=False) 輕量登入（省去約 17 MB 合約下載流量），手動建構 TMFR1 近月連續合約。
2. HistoricalBackfillService.run()：由新到舊逐日回補 → SQLite (symbol=TMFR1)，內建每日流量配額守門。
3. 有新增資料時，由 SQLite 重建「一月一工作表」Excel (微台10_每秒逐筆歷史紀錄.xlsx)。

可重複執行：已完成日期不重抓；額度用盡時優雅暫停，隔日 08:00 配額重置後再次執行即自動續跑。

用法範例：
    python backfill_history_tmf.py                       # 回補全部缺漏區間
    python backfill_history_tmf.py --max-days 3          # 試跑：最多下載 3 個交易日
    python backfill_history_tmf.py --excel-only          # 不連線，僅由 SQLite 重建 Excel
    python backfill_history_tmf.py --retry-empty         # 重試先前判定為無資料之日期
"""

import os
import sys
import argparse
import logging
from typing import Any, Dict, Optional

from dotenv import load_dotenv

from services.tick_export import HistoricalBackfillService, MonthlyExcelExporter
from services.tick_export.history_backfill import DEFAULT_START_DATE

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_history_tmf")

DB_PATH = "data/market_data.db"
EXCEL_PATH = "data/excel/微台10_每秒逐筆歷史紀錄.xlsx"
CACHE_DIR = "data/ticks_cache"
STATE_PATH = "data/backfill_state.json"


def parse_arguments() -> argparse.Namespace:
    """
    解析 CLI 參數。

    返回:
        argparse.Namespace: 解析後之參數物件。
    """
    parser = argparse.ArgumentParser(description="微台10 歷史 Tick 回補 (SQLite + 月份分頁 Excel)")
    parser.add_argument("--start", default=DEFAULT_START_DATE, help="回補起日 YYYY-MM-DD (預設: 微台上市日 2024-07-29)")
    parser.add_argument("--end", default=None, help="回補迄日 YYYY-MM-DD (預設: 既有實體合約資料最早日)")
    parser.add_argument("--max-days", type=int, default=None, help="本次最多下載之交易日數 (試跑用)")
    parser.add_argument("--reserve-mb", type=float, default=40.0, help="保留給每日增量同步之流量額度 MB (預設 40)")
    parser.add_argument("--retry-empty", action="store_true", help="重試先前判定為無資料之日期")
    parser.add_argument("--no-excel", action="store_true", help="回補後不重建 Excel")
    parser.add_argument("--excel-only", action="store_true", help="不連線 Shioaji，僅由 SQLite 重建 Excel")
    return parser.parse_args()


def build_history_contract(api: Any) -> Any:
    """
    取得 TMFR1 近月連續合約；輕量登入未下載合約檔時，手動建構等價合約物件。

    參數:
        api: 已登入之 Shioaji 實例。

    返回:
        Any: TMFR1 合約物件。
    """
    import shioaji as sj
    from shioaji.contracts import Future

    try:
        existing = api.Contracts.Futures.TMF.get("TMFR1")
        if existing:
            return existing
    except Exception:  # noqa: BLE001
        pass
    return Future(
        code="TMFR1", symbol="TMFR1", name="微型臺指期貨近月", category="TMF",
        exchange=sj.constant.Exchange.TAIFEX
    )


def rebuild_excel() -> Dict[str, Any]:
    """
    由 SQLite 重建「一月一工作表」Excel 並輸出摘要。

    返回:
        Dict[str, Any]: MonthlyExcelExporter.export() 之成果摘要。
    """
    summary = MonthlyExcelExporter(db_path=DB_PATH, excel_path=EXCEL_PATH).export()
    print("\n【Excel 月份分頁】")
    for s in summary["sheets"]:
        print(f"  工作表 {s['sheet']:<10} {s['rows']:>9,} 列   {s['first_time']} ~ {s['last_time']}")
    print(f"  合計 {len(summary['sheets'])} 個工作表 / {summary['total_rows']:,} 列 / {summary['file_size_mb']} MB "
          f"(耗時 {summary['duration_seconds']} 秒)")
    return summary


def run_backfill(args: argparse.Namespace) -> Dict[str, Any]:
    """
    執行回補（登入 → 回補 → 登出）。

    參數:
        args (argparse.Namespace): CLI 參數。

    返回:
        Dict[str, Any]: HistoricalBackfillService.run() 之執行報告；缺憑證時回傳 status=FAILED。
    """
    load_dotenv()
    api_key = os.environ.get("SHIOAJI_API_KEY")
    secret_key = os.environ.get("SHIOAJI_SECRET_KEY")
    if not api_key or not secret_key:
        msg = ("Status: ERROR | Root Cause: 缺少 SHIOAJI_API_KEY 或 SHIOAJI_SECRET_KEY | "
               "Suggested Fix: 於 .env 配置永豐證券 API 金鑰。")
        print(msg)
        return {"status": "FAILED", "error": msg}

    import shioaji as sj
    api = sj.Shioaji(simulation=True)
    try:
        api.login(api_key, secret_key, fetch_contract=False)
        logger.info("Shioaji 輕量登入成功 (fetch_contract=False)。")
        service = HistoricalBackfillService(
            db_path=DB_PATH, cache_dir=CACHE_DIR, state_path=STATE_PATH, reserve_mb=args.reserve_mb
        )
        report = service.run(
            api=api,
            contract=build_history_contract(api),
            start_date=args.start,
            end_date=args.end,
            max_days=args.max_days,
            retry_empty=args.retry_empty,
        )
        report["status"] = "SUCCESS"
        return report
    except Exception as e:  # noqa: BLE001
        msg = (f"Status: ERROR | Root Cause: 回補過程發生非預期例外 ({type(e).__name__}: {e}) | "
               f"Suggested Fix: 檢視日誌；進度已逐日持久化，修正後重新執行即可續跑。")
        logger.error(msg, exc_info=True)
        print(msg)
        return {"status": "FAILED", "error": str(e)}
    finally:
        try:
            api.logout()
        except Exception:  # noqa: BLE001
            pass


def print_report(report: Dict[str, Any]) -> None:
    """
    輸出回補執行報表。

    參數:
        report (Dict[str, Any]): 回補執行報告。
    """
    print("\n" + "=" * 66)
    print("                微台10 歷史 Tick 回補成果報表")
    print("=" * 66)
    print(f"  停止原因        : {report.get('stop_reason')}  (COMPLETED=全部完成 / QUOTA_PAUSED=額度用盡待次日續跑)")
    print(f"  回補區間        : {report.get('range')}  (邊界時間: {report.get('boundary_time')})")
    print(f"  本次處理交易日  : {report.get('processed')} 天 (實際下載 {report.get('downloaded_days')} 天)")
    print(f"  寫入 SQLite     : {report.get('inserted_rows', 0):,} 列秒 K")
    print(f"  失敗日期        : {report.get('failed_days') or '無'}")
    print(f"  尚待處理        : {report.get('pending_after')} 天")
    print(f"  剩餘流量額度    : 約 {report.get('quota_remaining_mb')} MB")
    print(f"  耗時            : {report.get('duration_seconds')} 秒")
    print("=" * 66)


def main() -> None:
    """程式進入點。"""
    args = parse_arguments()
    inserted = 0

    if not args.excel_only:
        report = run_backfill(args)
        if report.get("status") == "FAILED":
            sys.exit(1)
        print_report(report)
        inserted = int(report.get("inserted_rows", 0))

    if args.excel_only or (inserted > 0 and not args.no_excel):
        try:
            rebuild_excel()
        except (PermissionError, ValueError) as e:
            print(str(e))
            sys.exit(1)

    if not args.excel_only:
        try:
            from services.notification.telegram_bot import TelegramBotService
            TelegramBotService(db_path=DB_PATH).notify_update_success(
                report={
                    "stop_reason": report.get("stop_reason"),
                    "inserted_rows": inserted,
                    "remaining_mb": report.get("quota_remaining_mb", 0.0),
                    "reserve_mb": args.reserve_mb,
                }
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Telegram 推播通知略過: {e}")


if __name__ == "__main__":
    main()
