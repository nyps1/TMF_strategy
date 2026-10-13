"""
微台指每日定時自動更新流水線服務 (services/automation/daily_update_pipeline.py)

專門負責每日特定時段（07:00 與 14:00）自動執行：
1. 行情增量同步：檢索永豐 Shioaji 最新日夜盤時序並寫入 SQLite 與秒 K Excel。
2. 策略 Excel 更新：執行三大自訂參數回測，產出最新交易明細試算表 (three_strategies_trades_comparison.xlsx)。
3. HTML 儀表板重繪：聚合 11 種週期並重新編譯產生最新互動儀表板 (kline_dashboard.html)。

遵循 PEP 8、Python Type Hints 與專案工程規範。
"""

import os
import sys
import time
import logging
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger("DailyUpdatePipeline")


class DailyUpdatePipeline:
    """
    提供自動化數據同步、Excel 匯出與 HTML 儀表板編譯之全鏈條流水線。
    """

    def __init__(
        self,
        db_path: str = "data/market_data.db",
        excel_path: str = "three_strategies_trades_comparison.xlsx",
        html_path: str = "kline_dashboard.html"
    ) -> None:
        """
        初始化自動更新管線。

        參數:
            db_path (str): SQLite 時序資料庫路徑。
            excel_path (str): 輸出之 Excel 試算表路徑。
            html_path (str): 輸出之 HTML 儀表板路徑。
        """
        self.db_path = os.path.abspath(db_path)
        self.excel_path = os.path.abspath(excel_path)
        self.html_path = os.path.abspath(html_path)

    def run_sync_step(self, force_refresh: bool = True) -> Dict[str, Any]:
        """
        步驟一：透過 Shioaji API 執行行情數據增量同步（自動載入 .env 憑證並預設強制刷新當日最新 Tick）。
        將最新日夜盤秒 K 線同步寫入 SQLite (data/market_data.db)、Parquet 與秒 K Excel。
        若環境未配置 API 金鑰，則優雅降級跳過外部連線，保留本機既有歷史數據。

        參數:
            force_refresh (bool): 是否強制刷新下載當日最新 Tick，預設為 True。

        返回:
            Dict[str, Any]: 行情同步執行狀態摘要。
        """
        logger.info("【管線步驟 1/3】開始執行行情數據增量同步檢測...")
        from dotenv import load_dotenv
        env_path = os.path.join(os.path.dirname(self.html_path), ".env")
        if os.path.exists(env_path):
            load_dotenv(dotenv_path=env_path, override=False)
        else:
            load_dotenv(override=False)

        api_key = os.environ.get("SHIOAJI_API_KEY")
        secret_key = os.environ.get("SHIOAJI_SECRET_KEY")

        if not api_key or not secret_key:
            logger.info("未檢測到有效之 Shioaji API 憑證，採用本機既有 SQLite 資料庫進行後續編譯。")
            return {
                "step": "sync",
                "status": "SKIPPED_OFFLINE",
                "message": "無 API 憑證，保留本機資料庫"
            }

        try:
            from daily_sync_tmf10 import run_daily_sync
            res = run_daily_sync(force_refresh=force_refresh)
            logger.info(
                f"行情增量同步執行完成: status={res.get('status')}, "
                f"new_bars={res.get('new_bars_count', 0)}, "
                f"range=[{res.get('new_range_start')} ~ {res.get('new_range_end')}]"
            )
            return {"step": "sync", "status": res.get("status", "SUCCESS"), "detail": res}
        except Exception as e:
            logger.warning(f"行情增量同步遭遇非致命異常 ({e})，降級繼續執行本機回測與圖表更新。")
            return {"step": "sync", "status": "DEGRADED", "error": str(e)}

    def run_excel_step(self) -> Dict[str, Any]:
        """
        步驟二：重新回測三組自訂策略並更新交易明細 Excel 試算表。

        返回:
            Dict[str, Any]: Excel 產出結果資訊。
        """
        logger.info("【管線步驟 2/3】開始執行三大策略回測並更新 Excel 試算表...")
        start_t = time.time()
        try:
            from generate_three_images_excel import main as generate_excel
            output_abs = generate_excel()
            file_size_kb = os.path.getsize(output_abs) / 1024.0
            elapsed = time.time() - start_t
            logger.info(f"Excel 試算表已成功更新: {output_abs} ({file_size_kb:.1f} KB, 耗時 {elapsed:.2f}s)")
            return {
                "step": "excel",
                "status": "SUCCESS",
                "path": output_abs,
                "size_kb": round(file_size_kb, 1),
                "elapsed_seconds": round(elapsed, 2)
            }
        except Exception as e:
            logger.error(f"Excel 產出失敗: {e}", exc_info=True)
            return {"step": "excel", "status": "FAILED", "error": str(e)}

    def run_html_step(self) -> Dict[str, Any]:
        """
        步驟三：重新聚合 11 種時間週期並重新編譯產生 HTML 互動儀表板。

        返回:
            Dict[str, Any]: HTML 產出結果資訊。
        """
        logger.info("【管線步驟 3/3】開始聚合 11 種週期並重新生成 HTML 儀表板...")
        start_t = time.time()
        try:
            from generate_kline_dashboard import generate_dashboard
            output_abs = generate_dashboard(
                db_path=self.db_path,
                output_html_path=self.html_path
            )
            file_size_mb = os.path.getsize(output_abs) / (1024.0 * 1024.0)
            elapsed = time.time() - start_t
            logger.info(f"HTML 互動儀表板已成功更新: {output_abs} ({file_size_mb:.2f} MB, 耗時 {elapsed:.2f}s)")
            return {
                "step": "html",
                "status": "SUCCESS",
                "path": output_abs,
                "size_mb": round(file_size_mb, 2),
                "elapsed_seconds": round(elapsed, 2)
            }
        except Exception as e:
            logger.error(f"HTML 產出失敗: {e}", exc_info=True)
            return {"step": "html", "status": "FAILED", "error": str(e)}

    def execute_pipeline(self) -> Dict[str, Any]:
        """
        執行整套每日定時更新流水線。

        返回:
            Dict[str, Any]: 包含各步驟執行成果與耗時統計之總報表。
        """
        total_start = time.time()
        executed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(f"=== 微台10 定時資料更新管線啟動 [{executed_at}] ===")

        sync_res = self.run_sync_step()
        excel_res = self.run_excel_step()
        html_res = self.run_html_step()

        total_elapsed = time.time() - total_start
        all_success = (excel_res.get("status") == "SUCCESS") and (html_res.get("status") == "SUCCESS")

        report = {
            "pipeline_status": "SUCCESS" if all_success else "PARTIAL_FAILURE",
            "executed_at": executed_at,
            "total_elapsed_seconds": round(total_elapsed, 2),
            "sync_step": sync_res,
            "excel_step": excel_res,
            "html_step": html_res
        }

        logger.info(
            f"=== 微台10 定時資料更新管線完成 [{report['pipeline_status']}] (總耗時: {total_elapsed:.2f}s) ==="
        )
        return report
