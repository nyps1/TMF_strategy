"""
定時自動化更新任務執行腳本 (run_scheduled_update.py)

由排程系統 (Antigravity Schedule / Windows Task Scheduler) 於每日 07:00 與 14:00 自動調用。
依序執行行情同步、Excel 交易明細與對比表更新，以及 HTML 儀表板重新編譯。
遵循 PEP 8 與 Python Type Hints 規範。
"""

import os
import sys
import logging

# 確保當前路徑在模組搜尋範圍內
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from services.automation.daily_update_pipeline import DailyUpdatePipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("ScheduledRunner")


def main() -> None:
    """主入口點。"""
    pipeline = DailyUpdatePipeline()
    report = pipeline.execute_pipeline()
    print("\n" + "=" * 60)
    print("           微台10 定時資料更新管線執行摘要報表")
    print("=" * 60)
    print(f"  執行時點       : {report['executed_at']}")
    print(f"  整體狀態       : {report['pipeline_status']}")
    print(f"  總執行耗時     : {report['total_elapsed_seconds']} 秒")
    print(f"  1. 行情增量同步: {report['sync_step'].get('status')}")
    print(f"  2. Excel 更新  : {report['excel_step'].get('status')} ({report['excel_step'].get('size_kb', 0)} KB)")
    print(f"  3. HTML 儀表板 : {report['html_step'].get('status')} ({report['html_step'].get('size_mb', 0)} MB)")
    print("=" * 60 + "\n")

    if report["pipeline_status"] != "SUCCESS":
        sys.exit(1)


if __name__ == "__main__":
    main()
